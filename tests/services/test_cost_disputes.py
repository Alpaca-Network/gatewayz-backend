"""Customer cost disputes (services/cost_disputes.py), DB and chain faked.

Uses the cross-repo v2 usage fixture (tests/fixtures/genlayer_usage_v2.json), so the
entries, proofs and root are the ones the CostDispute contract verifies."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.services import cost_disputes as cd
from src.services import job_usage_v2
from src.services.genlayer_verify import TxState

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "genlayer_usage_v2.json").read_text()
)
ENTRIES = [line["entry"] for line in FIXTURE["lines"]]
ROOT = FIXTURE["root"]
JOB = "0x" + "11" * 32
CONTRACT = "0x338f24b70eFE8F2888dAe52D21c9E2827E842D86"
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("COST_DISPUTES_ENABLED", "true")
    monkeypatch.setenv("GENLAYER_COST_DISPUTE_CONTRACT", CONTRACT)
    monkeypatch.setenv("GENLAYER_DISPUTE_SUBMITTER_KEY", "0x" + "01" * 32)
    monkeypatch.delenv("PRICE_SHEET_BASE", raising=False)
    monkeypatch.delenv("DISPUTE_VAULT_PUBLIC_BASE", raising=False)


def _job(**over):
    return {
        "job_id": JOB,
        "usage_root_v2": ROOT,
        "closed_at": (NOW - timedelta(hours=1)).isoformat(),
        **over,
    }


def _check(job=None, entries=ENTRIES, idx=(0,), claims=None, uris=None, already=()):
    cd.validate_request(
        job or _job(), entries, list(idx), claims or {}, uris or {}, set(already), now=NOW
    )


def test_fixture_root_matches_the_seal():
    assert job_usage_v2.seal(ENTRIES)["root"] == ROOT


def test_valid_request_passes():
    _check(idx=(0, 2), claims={"0": "loops"}, uris={"2": "https://example.org/c.json"})


@pytest.mark.parametrize(
    "kwargs,status,code",
    [
        ({"job": _job(usage_root_v2=None)}, 409, "job_not_sealed_v2"),
        ({"entries": None}, 409, "job_not_sealed_v2"),
        ({"job": _job(usage_root_v2="0x" + "22" * 32)}, 500, "usage_root_mismatch"),
        ({"job": _job(closed_at=None)}, 409, "job_not_closed"),
        (
            {"job": _job(closed_at=(NOW - timedelta(hours=73)).isoformat())},
            409,
            "dispute_window_closed",
        ),
        ({"idx": ()}, 422, "invalid_lines"),
        ({"idx": (0, 0)}, 422, "invalid_lines"),
        ({"idx": tuple(range(11))}, 422, "invalid_lines"),
        ({"idx": (3,)}, 404, "usage_line_not_found"),
        ({"claims": {"1": "not disputed"}}, 422, "invalid_lines"),
        ({"claims": {"0": "x" * 1001}}, 422, "invalid_claim"),
        ({"uris": {"0": "http://plain.example/c"}}, 422, "invalid_content_uri"),
        ({"already": (0,)}, 409, "line_already_disputed"),
    ],
)
def test_refusals(kwargs, status, code):
    with pytest.raises(cd.DisputeError) as e:
        _check(**kwargs)
    assert (e.value.status, e.value.code) == (status, code)


def test_disabled_without_config(monkeypatch):
    monkeypatch.setenv("COST_DISPUTES_ENABLED", "false")
    assert not cd.is_configured()
    with pytest.raises(cd.DisputeError) as e:
        cd.open_dispute(_job(), 1, [0], {}, {})
    assert e.value.code == "cost_disputes_disabled"


def _dispute(**over):
    return {
        "dispute_id": "0x" + "33" * 32,
        "job_id": JOB,
        "user_id": 7,
        "usage_root": ROOT,
        "line_indexes": [0, 2],
        "claims": {"0": "it looped"},
        "content_uris": {"2": "https://example.org/c2.json"},
        "contract_address": CONTRACT,
        "policy_uri": "https://example.org/policy.json",
        "status": "submitted",
        "submit_attempts": 0,
        **over,
    }


def test_lines_and_args_match_what_the_contract_checks(monkeypatch):
    monkeypatch.setattr("src.services.dispute_vault.enabled", lambda: False)
    lines = cd.build_lines(_dispute(), ENTRIES)
    assert [ln["index"] for ln in lines] == [0, 2]
    assert lines[0]["claim"] == "it looped" and lines[0]["content_uri"] == ""
    assert lines[1]["content_uri"] == "https://example.org/c2.json"
    assert lines[1]["claim"]  # a default claim, never empty
    # each proof verifies against the sealed root, as CostDispute does on chain
    for ln in lines:
        leaf = job_usage_v2.leaf_hash(ln["entry"])
        node = leaf
        for p in ln["proof"]:
            sib = bytes.fromhex(p[2:])
            node = job_usage_v2._node(node, sib)
        assert "0x" + node.hex() == ROOT
    args = cd.submit_args(_dispute(), lines, '{"version":"fbp-v5"}')
    assert args[0] == "0x" + "33" * 32 and args[1] == JOB and args[2] == ROOT
    assert args[4] == "https://api.gatewayz.ai/v1/price-sheets/"  # per-line sheets
    assert args[5] == cd.ZERO_HASH
    assert json.loads(args[6]) == lines


def test_vault_link_used_when_kept(monkeypatch):
    monkeypatch.setattr("src.services.dispute_vault.enabled", lambda: True)
    monkeypatch.setattr(
        "src.services.dispute_vault.reveal",
        lambda job, rid: {"content_uri": f"https://api.gatewayz.ai/v1/dispute-content/{rid}"},
    )
    lines = cd.build_lines(_dispute(content_uris={}), ENTRIES)
    assert lines[0]["content_uri"].endswith("/req_fixture_0")


class FakeDB:
    def __init__(self, row):
        self.row, self.released = dict(row), False

    def update_dispute(self, dispute_id, fields):
        self.row.update(fields)
        return dict(self.row)

    def release_lines(self, dispute_id):
        self.released = True


class FakeClient:
    def __init__(self, tx="0xtx", fail=None, state=None, verdict=None, reason=None):
        self.tx, self.fail, self.state, self.verdict, self.reason = tx, fail, state, verdict, reason
        self.args, self.reads = None, []

    def open_dispute(self, args):
        if self.fail:
            raise RuntimeError(self.fail)
        self.args = args
        return self.tx

    def tx_state(self, tx):
        return self.state

    def leader_error(self, tx):
        return self.reason

    def read_dispute(self, dispute_id, final):
        self.reads.append(final)
        return self.verdict


@pytest.fixture
def fake(monkeypatch):
    def make(row):
        f = FakeDB(row)
        monkeypatch.setattr(cd.db, "update_dispute", f.update_dispute)
        monkeypatch.setattr(cd.db, "release_lines", f.release_lines)
        monkeypatch.setattr("src.db.inference_jobs.list_usage_v2", lambda job_id: ENTRIES)
        monkeypatch.setattr("src.services.dispute_vault.enabled", lambda: False)
        monkeypatch.setattr(cd, "policy_text", lambda uri: '{"version":"fbp-v5"}')
        return f

    return make


def test_submit_moves_to_adjudicating(fake):
    f = fake(_dispute())
    c = FakeClient()
    out = cd.submit(_dispute(), c)
    assert out["status"] == "adjudicating" and out["genlayer_tx"] == "0xtx"
    assert c.args[0] == "0x" + "33" * 32 and not f.released


def test_permanent_refusal_ends_in_error_and_releases_lines(fake):
    f = fake(_dispute())
    out = cd.submit(_dispute(), FakeClient(fail="UserError: line_already_disputed"))
    assert out["status"] == "error" and f.released


def test_transient_failure_retries_then_gives_up(fake):
    f = fake(_dispute())
    out = cd.submit(_dispute(), FakeClient(fail="503 Service Unavailable"))
    assert out["status"] == "submitted" and out["submit_attempts"] == 1 and not f.released
    out = cd.submit(_dispute(submit_attempts=2), FakeClient(fail="503 Service Unavailable"))
    assert out["status"] == "error" and f.released


VERDICT = {
    "dispute_id": "0x" + "33" * 32,
    "status": "decided",
    "billed_usd": "0.000039",
    "refund_usd": "0.0000195",
    "policy_hash": "0xpol",
    "lines": [{"line": 0, "refund_usd": "0.0000195"}, {"line": 2, "refund_usd": "0"}],
}


def _open(**over):
    return _dispute(**{"status": "adjudicating", "genlayer_tx": "0xtx", **over})


def test_decided_then_final(fake):
    fake(_open())
    c = FakeClient(state=TxState("ACCEPTED", "MAJORITY_AGREE", None, CONTRACT), verdict=VERDICT)
    out = cd.refresh(_open(), c)
    assert out["status"] == "decided" and out["refund_usd"] == "0.0000195" and c.reads == [False]
    c = FakeClient(state=TxState("FINALIZED", "MAJORITY_AGREE", None, CONTRACT), verdict=VERDICT)
    out = cd.refresh(_open(status="decided"), c)
    assert out["status"] == "final" and c.reads == [True]
    v = cd.view(out)
    assert v["final"] and v["settled"] is False and v["shadow_mode"] is True
    assert v["rulings"] == VERDICT["lines"]


def test_contract_refusal_is_an_error_with_its_reason(fake):
    f = fake(_open())
    c = FakeClient(
        state=TxState("ACCEPTED", "MAJORITY_AGREE", "FINISHED_WITH_ERROR", CONTRACT),
        reason="policy_not_allowed",
    )
    out = cd.refresh(_open(), c)
    assert out["status"] == "error" and "policy_not_allowed" in out["error"] and f.released


def test_no_consensus_is_undetermined(fake):
    f = fake(_open())
    out = cd.refresh(_open(), FakeClient(state=TxState("UNDETERMINED", None, None, CONTRACT)))
    assert out["status"] == "undetermined" and f.released


def test_wrong_contract_is_an_error(fake):
    f = fake(_open())
    state = TxState("ACCEPTED", "MAJORITY_AGREE", None, "0x" + "ee" * 20)
    out = cd.refresh(_open(), FakeClient(state=state))
    assert out["status"] == "error" and f.released


def test_still_processing_only_stamps(fake):
    fake(_open())
    out = cd.refresh(_open(), FakeClient(state=TxState("PENDING", None, None, CONTRACT)))
    assert out["status"] == "adjudicating" and out["last_checked_at"]
