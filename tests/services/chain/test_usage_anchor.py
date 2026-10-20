"""Tests for src.services.chain.usage_anchor -- daily Merkle-root anchoring
of api_usage ledger rows on Avalanche C-Chain. DB and chain are faked."""

import json
from datetime import UTC, date, datetime

import pytest

from src.services.chain import usage_anchor as ua
from src.services.chain.merkle import leaf_hash, verify_inclusion
from src.services.chain.usage_anchor_client import (
    AnchorBroadcastError,
    AnchorNotSentError,
    SignedAnchor,
)

DAY = date(2026, 10, 18)
NOW = datetime(2026, 10, 19, 0, 15, tzinfo=UTC)
SALT = "test-salt-key"
CONTRACT = "0x00000000000000000000000000000000000000aa"


@pytest.fixture
def sb():
    """No-op fixture whose presence bypasses the autouse DB-skip in
    tests/conftest.py -- everything here is faked."""
    return None


def _row(i, ts="2026-10-18T12:00:00+00:00", user_id=7, amount=-0.0012, **meta):
    return {
        "id": i,
        "user_id": user_id,
        "created_at": ts,
        "amount": amount,
        "metadata": {
            "model": "anthropic/claude-sonnet-4-6",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "request_id": "secret-request-id",
            **meta,
        },
    }


class FakeDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.anchors = {}

    def fetch_usage_rows_for_day(self, day):
        return [r for r in self.rows if r["created_at"].startswith(day.isoformat())]

    def get_usage_row(self, ledger_id):
        return next((r for r in self.rows if r["id"] == ledger_id), None)

    def get_anchor(self, chain_id, contract, day):
        row = self.anchors.get((chain_id, contract.lower(), day.isoformat()))
        return dict(row) if row else None

    def upsert_anchor(self, chain_id, contract, day, **fields):
        key = (chain_id, contract.lower(), day.isoformat())
        row = self.anchors.setdefault(
            key,
            {
                "chain_id": chain_id,
                "contract_address": contract.lower(),
                "day": day.isoformat(),
                "day_index": ua.day_index(day),
                "attempts": 0,
                "status": "unanchored",
            },
        )
        row.update(fields)

    def list_anchors(self, chain_id, contract, limit=30, statuses=None):
        rows = [
            dict(r)
            for (c, k, _), r in self.anchors.items()
            if c == chain_id and k == contract.lower() and (not statuses or r["status"] in statuses)
        ]
        return sorted(rows, key=lambda r: r["day"], reverse=True)[:limit]


class FakeClient:
    chain_id = 43113
    contract_address = CONTRACT

    def __init__(self):
        self.onchain = {}
        self.sent = []
        self.receipts = {}
        self.block = 100
        self.nonce_latest = 0
        self.fail_send = None
        self.mine_on_send = True

    def root_of(self, day):
        return self.onchain.get(day)

    def send_anchor(self, day, root, record):
        if self.fail_send == "not_sent":
            raise AnchorNotSentError("signer has no AVAX")
        info = SignedAnchor(
            tx_hash=f"0x{len(self.sent) + 1:064x}",
            nonce=len(self.sent),
            gas=50_000,
            day=day,
            root=root,
        )
        assert record(info) is True
        if self.fail_send == "broadcast":
            raise AnchorBroadcastError(info.tx_hash, info.nonce, RuntimeError("timeout"))
        self.sent.append(info)
        if self.mine_on_send:
            self.onchain[day] = root
            self.receipts[info.tx_hash] = {"status": 1, "blockNumber": self.block}
            self.nonce_latest = info.nonce + 1
        return info

    def wait_for_receipt(self, tx_hash, timeout_seconds):
        return self.receipts.get(tx_hash)

    def get_receipt(self, tx_hash):
        return self.receipts.get(tx_hash)

    def block_number(self):
        return self.block

    def confirmed_nonce(self):
        return self.nonce_latest


@pytest.fixture
def env(monkeypatch):
    fake_db = FakeDB([_row(1), _row(2, ts="2026-10-18T08:00:00+00:00"), _row(3)])
    client = FakeClient()
    for name in (
        "fetch_usage_rows_for_day",
        "get_usage_row",
        "get_anchor",
        "upsert_anchor",
        "list_anchors",
    ):
        monkeypatch.setattr(ua.db, name, getattr(fake_db, name))
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_ENABLED", True)
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_SALT_KEY", SALT)
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_CHAIN_ID", 43113)
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_CONTRACT_ADDRESS", CONTRACT)
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_MIN_CONFIRMATIONS", 1)
    alerts = []
    monkeypatch.setattr(ua, "_alert", alerts.append)
    return fake_db, client, alerts


def _anchor(fake_db):
    return fake_db.get_anchor(43113, CONTRACT, DAY)


# ---------------------------------------------------------------------------
# Leaf format
# ---------------------------------------------------------------------------


def test_leaf_is_canonical_json_with_only_v1_fields(sb):
    record = ua.leaf_record(_row(42, amount="-0.00120000"), SALT)
    assert set(record) == {"v", "id", "ts", "model", "tokens_in", "tokens_out", "cost", "salt"}
    raw = ua.leaf_bytes(record).decode()
    assert raw == json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert raw.startswith('{"cost":"-0.00120000","id":"42","model":')
    # Nothing identifying: no user id, no request id, no prompt.
    assert "secret-request-id" not in raw and '"user_id"' not in raw


def test_leaf_normalizes_timestamps_and_costs(sb):
    a = ua.leaf_record(_row(1, ts="2026-10-18T12:00:00Z", amount=0), SALT)
    b = ua.leaf_record(_row(1, ts="2026-10-18T14:00:00+02:00", amount="-0.0"), SALT)
    assert a["ts"] == b["ts"] == "2026-10-18T12:00:00.000000Z"
    assert a["cost"] == b["cost"] == "0.00000000"
    assert ua.leaf_record(_row(1, amount=-0.0012), SALT)["cost"] == "-0.00120000"


def test_salt_is_hmac_of_id_and_depends_on_key(sb):
    assert ua.leaf_salt(5, "k1") == ua.leaf_salt("5", "k1")
    assert ua.leaf_salt(5, "k1") != ua.leaf_salt(5, "k2")
    assert len(ua.leaf_salt(5, "k1")) == 64


def test_missing_token_metadata_defaults_to_zero(sb):
    row = _row(1)
    row["metadata"] = {}
    record = ua.leaf_record(row, SALT)
    assert record["model"] is None and record["tokens_in"] == 0 and record["tokens_out"] == 0


def test_tree_orders_by_created_at_then_id_regardless_of_input_order(sb):
    rows = [_row(3), _row(2, ts="2026-10-18T08:00:00+00:00"), _row(1)]
    tree = ua.build_day_tree(DAY, rows, SALT)
    assert tree.ledger_ids == [2, 1, 3]
    assert ua.build_day_tree(DAY, list(reversed(rows)), SALT).root == tree.root


def test_empty_day_has_no_tree(sb):
    assert ua.build_day_tree(DAY, [], SALT) is None


def test_day_index_is_days_since_epoch(sb):
    assert ua.day_index(date(1970, 1, 2)) == 1
    assert ua.day_from_index(ua.day_index(DAY)) == DAY


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def test_disabled_flag_skips_without_touching_anything(env, monkeypatch):
    fake_db, client, _ = env
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_ENABLED", False)
    assert ua.run_usage_anchor_once(DAY, client, now=NOW) == {"skipped": "disabled"}
    assert client.sent == [] and fake_db.anchors == {}


def test_missing_salt_key_refuses(env, monkeypatch):
    _, client, _ = env
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_SALT_KEY", None)
    with pytest.raises(ua.UsageAnchorError, match="SALT"):
        ua.run_usage_anchor_once(DAY, client, now=NOW)


def test_unfinished_day_is_refused(env):
    _, client, _ = env
    with pytest.raises(ua.UsageAnchorError, match="not a finished"):
        ua.run_usage_anchor_once(NOW.date(), client, now=NOW)


def test_anchors_yesterday_by_default_and_confirms(env):
    fake_db, client, alerts = env
    result = ua.run_usage_anchor_once(client=client, now=NOW)
    assert result["day"] == DAY.isoformat()
    assert result["status"] == "confirmed"
    assert len(client.sent) == 1
    sent = client.sent[0]
    expected = ua.build_day_tree(DAY, fake_db.rows, SALT)
    assert sent.root == expected.root
    # The count stays in the database, never on-chain.
    assert not hasattr(sent, "leaf_count")
    assert sent.day == ua.day_index(DAY)
    row = _anchor(fake_db)
    assert row["status"] == "confirmed"
    assert row["root"] == ua.root_hex(expected.root)
    assert row["tx_hash"] == sent.tx_hash and row["attempts"] == 1
    assert row["leaf_count"] == 3
    assert alerts == []


def test_confirmed_day_is_a_noop(env):
    fake_db, client, _ = env
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    again = ua.run_usage_anchor_once(DAY, client, now=NOW)
    assert again == {"day": DAY.isoformat(), "status": "confirmed", "noop": True}
    assert len(client.sent) == 1


def test_empty_day_is_recorded_and_not_sent(env):
    fake_db, client, _ = env
    fake_db.rows = []
    assert ua.run_usage_anchor_once(DAY, client, now=NOW)["status"] == "skipped_empty"
    assert client.sent == [] and _anchor(fake_db)["status"] == "skipped_empty"


def test_changed_rows_after_root_was_stored_alert_and_do_not_send(env):
    fake_db, client, alerts = env
    client.mine_on_send = False
    client.fail_send = "not_sent"
    ua.run_usage_anchor_once(DAY, client, now=NOW)  # stores root, send fails
    assert _anchor(fake_db)["status"] == "unanchored"
    fake_db.rows.append(_row(99))  # the ledger changed under us
    client.fail_send = None
    result = ua.run_usage_anchor_once(DAY, client, now=NOW)
    assert result["status"] == "failed"
    assert client.sent == [] and alerts and "differs" in alerts[0]


def test_onchain_root_matching_marks_confirmed_without_sending(env):
    fake_db, client, _ = env
    tree = ua.build_day_tree(DAY, fake_db.rows, SALT)
    client.onchain[ua.day_index(DAY)] = tree.root
    assert ua.run_usage_anchor_once(DAY, client, now=NOW)["status"] == "confirmed"
    assert client.sent == []


def test_onchain_root_differing_alerts_and_does_not_send(env):
    fake_db, client, alerts = env
    client.onchain[ua.day_index(DAY)] = b"\x11" * 32
    assert ua.run_usage_anchor_once(DAY, client, now=NOW)["status"] == "failed"
    assert client.sent == [] and _anchor(fake_db)["status"] == "failed"
    assert alerts


def test_not_sent_leaves_day_unanchored_for_the_sweep(env):
    fake_db, client, _ = env
    client.fail_send = "not_sent"
    result = ua.run_usage_anchor_once(DAY, client, now=NOW)
    assert result["status"] == "unanchored"
    row = _anchor(fake_db)
    assert row["status"] == "unanchored" and "AVAX" in row["last_error"]
    assert row["attempts"] == 0


def test_broadcast_error_stays_pending_with_recorded_hash(env):
    fake_db, client, _ = env
    client.fail_send = "broadcast"
    result = ua.run_usage_anchor_once(DAY, client, now=NOW)
    assert result["status"] == "pending"
    row = _anchor(fake_db)
    assert row["status"] == "pending" and row["tx_hash"] == result["tx_hash"]
    # A second run does not resend while pending.
    client.fail_send = None
    assert ua.run_usage_anchor_once(DAY, client, now=NOW)["status"] == "pending"
    assert client.sent == []


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def _send_unmined(env):
    fake_db, client, _ = env
    client.mine_on_send = False
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    assert _anchor(fake_db)["status"] == "pending"
    return fake_db, client


def test_sweep_confirms_after_enough_confirmations(env, monkeypatch):
    fake_db, client = _send_unmined(env)
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_MIN_CONFIRMATIONS", 3)
    tx = client.sent[0]
    client.onchain[tx.day] = tx.root
    client.receipts[tx.tx_hash] = {"status": 1, "blockNumber": 100}
    client.block = 101
    ua.sweep_usage_anchors(client, now=NOW)
    row = _anchor(fake_db)
    assert row["status"] == "pending" and row["block_number"] == 100
    client.block = 102
    ua.sweep_usage_anchors(client, now=NOW)
    assert _anchor(fake_db)["status"] == "confirmed"


def test_sweep_resubmits_once_after_reorg_then_fails(env):
    (
        fake_db,
        client,
    ) = (
        _send_unmined(env)[0],
        env[1],
    )
    alerts = env[2]
    tx = client.sent[0]
    # Seen mined, then the receipt vanishes (reorg).
    fake_db.upsert_anchor(43113, CONTRACT, DAY, block_number=100)
    ua.sweep_usage_anchors(client, now=NOW)
    assert len(client.sent) == 2  # resubmitted once
    row = _anchor(fake_db)
    assert row["status"] == "pending" and row["attempts"] == 2
    assert row["tx_hash"] != tx.tx_hash
    # Second vanish: no third send, flagged for a human.
    fake_db.upsert_anchor(43113, CONTRACT, DAY, block_number=100)
    ua.sweep_usage_anchors(client, now=NOW)
    assert len(client.sent) == 2
    assert _anchor(fake_db)["status"] == "failed"
    assert alerts


def test_sweep_treats_replaced_nonce_as_dropped(env):
    fake_db, client = _send_unmined(env)
    client.nonce_latest = 5  # nonce 0 is long past and no receipt exists
    ua.sweep_usage_anchors(client, now=NOW)
    assert len(client.sent) == 2


def test_sweep_retries_unsent_days(env):
    fake_db, client, _ = env
    client.fail_send = "not_sent"
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    client.fail_send = None
    result = ua.sweep_usage_anchors(client, now=NOW)
    assert result["checked"] == 1
    assert len(client.sent) == 1


def test_sweep_disabled_is_a_noop(env, monkeypatch):
    _, client, _ = env
    monkeypatch.setattr(ua.Config, "USAGE_ANCHOR_ENABLED", False)
    assert ua.sweep_usage_anchors(client, now=NOW) == {"skipped": "disabled"}


# ---------------------------------------------------------------------------
# Proofs
# ---------------------------------------------------------------------------


def test_proof_round_trips_against_anchored_root(env):
    fake_db, client, _ = env
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    proof = ua.build_proof(1, user_id=7)
    root = bytes.fromhex(proof["root"][2:])
    leaf = proof["leaf"].encode()
    path = [bytes.fromhex(p[2:]) for p in proof["proof"]]
    assert proof["tree_size"] == 3 and proof["leaf_index"] == 1
    assert "0x" + leaf_hash(leaf).hex() == proof["leaf_hash"]
    assert verify_inclusion(proof["leaf_index"], proof["tree_size"], leaf, path, root)
    assert root == client.onchain[ua.day_index(DAY)]
    assert proof["explorer_url"].startswith("https://testnet.snowtrace.io/tx/0x")


def test_proof_for_someone_elses_row_is_not_found(env):
    _, client, _ = env
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    with pytest.raises(ua.ProofNotFoundError):
        ua.build_proof(1, user_id=8)
    with pytest.raises(ua.ProofNotFoundError):
        ua.build_proof(12345, user_id=7)


def test_proof_before_anchoring_is_not_available(env):
    with pytest.raises(ua.ProofNotAvailableError):
        ua.build_proof(1, user_id=7)


def test_proof_refuses_when_ledger_no_longer_matches_root(env):
    fake_db, client, alerts = env
    ua.run_usage_anchor_once(DAY, client, now=NOW)
    fake_db.rows[0]["amount"] = -9.99
    with pytest.raises(ua.ProofNotAvailableError):
        ua.build_proof(1, user_id=7)
    assert alerts


def test_public_anchor_view_omits_leaf_count(sb):
    view = ua.anchor_view(
        {
            "chain_id": 43113,
            "day": "2026-10-18",
            "day_index": 20379,
            "root": "0x" + "42" * 32,
            "leaf_count": 3,
            "status": "confirmed",
            "tx_hash": "0x" + "ab" * 32,
            "block_number": 1,
        }
    )
    assert "leaf_count" not in view and "tree_size" not in view
