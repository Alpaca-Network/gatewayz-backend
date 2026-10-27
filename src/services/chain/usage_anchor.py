"""Daily usage anchoring on Avalanche C-Chain (docs/usage-anchor/README.md).

Once a day, every api_usage row of the previous UTC day in
credit_transactions becomes one salted leaf of an RFC 6962 Merkle tree
(src/services/chain/merkle.py), and only the tree's root is written to the
UsageAnchor contract. A user can later fetch an inclusion proof for one of
their own rows and check it against the on-chain root.

Daily request volume is not disclosed: the leaf count is stored in
usage_anchors but is neither sent on-chain nor returned by public routes.
Only a user's own inclusion proof carries tree_size, which RFC 6962
verification needs.

Leaf format v1 -- canonical JSON (sorted keys, no whitespace, UTF-8) of:

    {"v":1,"id":"<ledger id>","ts":"<created_at, UTC, microseconds, Z>",
     "model":<metadata.model or null>,"tokens_in":<prompt_tokens>,
     "tokens_out":<completion_tokens>,"cost":"<amount, 8 dp>",
     "salt":"<hex HMAC-SHA256(USAGE_ANCHOR_SALT_KEY, id)>"}

`cost` is credit_transactions.amount exactly as the ledger stores it
(numeric(14,8); negative = debit). Leaves are ordered by (created_at, id).
No user id, email, key, prompt or response is ever part of a leaf, and
leaves never leave the database except to their own user via the proof
endpoint.

Safety rules the job enforces:
- a day whose row is `confirmed` is never touched again (idempotent);
- a day is never anchored with a root that differs from the stored or
  on-chain root -- that is flagged loudly (Sentry + ERROR log) and the row
  is marked `failed` for a human;
- the tx hash is recorded before broadcast; a vanished receipt marks the row
  `reorged` and it is resubmitted at most once.

Everything here is synchronous; scheduled_sync.py runs it via
asyncio.to_thread so the event loop never blocks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.config.config import Config
from src.db import usage_anchors as db
from src.services.chain.merkle import (
    inclusion_proof_from_hashes,
    leaf_hash,
    root_from_leaf_hashes,
)
from src.services.chain.usage_anchor_client import (
    AnchorBroadcastError,
    AnchorNotSentError,
    SignedAnchor,
    UsageAnchorClient,
    UsageAnchorReader,
    explorer_base,
)

logger = logging.getLogger(__name__)

LEAF_VERSION = 1
# First send + one resubmit after a reorg / dropped tx.
MAX_SEND_ATTEMPTS = 2
# How long the job waits for the first receipt before leaving it to the sweep.
RECEIPT_WAIT_SECONDS = 60
# Rows the sweep looks at per pass; unanchored days older than this window
# are left for a human (or the admin trigger).
SWEEP_LOOKBACK_DAYS = 7
_EPOCH = date(1970, 1, 1)
_COST_QUANTUM = Decimal("0.00000001")  # credit_transactions.amount is numeric(14,8)

MERKLE_SPEC = (
    "RFC 6962 Merkle tree over SHA-256: leaf = SHA-256(0x00 || leaf_bytes), "
    "node = SHA-256(0x01 || left || right), split at the largest power of two "
    "below n. leaf_bytes = UTF-8 canonical JSON (sorted keys, separators ',' "
    "and ':') of {v, id, ts, model, tokens_in, tokens_out, cost, salt}; leaves "
    "ordered by (ts, id); one tree per UTC day; only the root is published "
    "on-chain via UsageAnchor.anchor(day, root), day = UTC days "
    "since 1970-01-01. The leaf count (request volume) is not published; an "
    "inclusion proof carries its own tree_size."
)


# Serializes whole runs (read state -> send -> record) within this process,
# so the daily job, the sweep and the admin trigger can't race on one day.
# The contract's one-root-per-day rule is the backstop across processes.
_run_lock = threading.RLock()


class UsageAnchorError(Exception):
    """Configuration problem that stops the job before it reads anything."""


class ProofNotFoundError(Exception):
    """No api_usage row with that id belongs to the caller."""


class ProofNotAvailableError(Exception):
    """The row exists but its day isn't anchored (yet), or can't be proven."""


@dataclass(frozen=True)
class DayTree:
    day: date
    leaves: list[bytes]  # canonical leaf bytes, in tree order
    ledger_ids: list[int]
    leaf_hashes: list[bytes]
    root: bytes

    @property
    def leaf_count(self) -> int:
        return len(self.leaves)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def day_index(day: date) -> int:
    return (day - _EPOCH).days


def day_from_index(index: int) -> date:
    return _EPOCH + timedelta(days=index)


def utc_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _canon_ts(value: Any) -> str:
    return _parse_ts(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canon_cost(value: Any) -> str:
    d = Decimal(str(value if value is not None else 0)).quantize(_COST_QUANTUM)
    if d == 0:
        d = Decimal(0).quantize(_COST_QUANTUM)  # no "-0.00000000"
    return format(d, "f")


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def leaf_salt(ledger_id: int | str, salt_key: str) -> str:
    return hmac.new(salt_key.encode(), str(ledger_id).encode(), hashlib.sha256).hexdigest()


def leaf_record(row: dict[str, Any], salt_key: str) -> dict[str, Any]:
    """The v1 leaf object for one api_usage ledger row."""
    metadata = row.get("metadata") or {}
    model = metadata.get("model")
    return {
        "v": LEAF_VERSION,
        "id": str(row["id"]),
        "ts": _canon_ts(row["created_at"]),
        "model": str(model) if model is not None else None,
        "tokens_in": _int_or_zero(metadata.get("prompt_tokens")),
        "tokens_out": _int_or_zero(metadata.get("completion_tokens")),
        "cost": _canon_cost(row.get("amount")),
        "salt": leaf_salt(row["id"], salt_key),
    }


def leaf_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def build_day_tree(day: date, rows: list[dict[str, Any]], salt_key: str) -> DayTree | None:
    """Tree over one day's rows, or None for an empty day."""
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: (_parse_ts(r["created_at"]), int(r["id"])))
    leaves = [leaf_bytes(leaf_record(r, salt_key)) for r in ordered]
    hashes = [leaf_hash(leaf) for leaf in leaves]
    return DayTree(
        day=day,
        leaves=leaves,
        ledger_ids=[int(r["id"]) for r in ordered],
        leaf_hashes=hashes,
        root=root_from_leaf_hashes(hashes),
    )


def root_hex(root: bytes) -> str:
    return "0x" + root.hex()


def _root_bytes(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("0x"))


def explorer_tx_url(chain_id: int, tx_hash: str | None) -> str | None:
    base = explorer_base(chain_id)
    return f"{base}/tx/{tx_hash}" if base and tx_hash else None


def explorer_address_url(chain_id: int, address: str | None) -> str | None:
    base = explorer_base(chain_id)
    return f"{base}/address/{address}" if base and address else None


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def _alert(message: str) -> None:
    """Loud, never raises: ERROR log + Sentry."""
    logger.error("USAGE ANCHOR ALERT: %s", message)
    try:
        import sentry_sdk

        sentry_sdk.capture_message(f"Usage anchor: {message}", level="error")
    except Exception as e:  # pragma: no cover - sentry optional
        logger.warning("Failed to capture usage-anchor alert to Sentry: %s", e)


def _require_salt_key() -> str:
    if not Config.USAGE_ANCHOR_SALT_KEY:
        raise UsageAnchorError("USAGE_ANCHOR_SALT_KEY is not set")
    return Config.USAGE_ANCHOR_SALT_KEY


def _fail(client: UsageAnchorReader, day: date, message: str, **fields: Any) -> dict[str, Any]:
    _alert(f"{day.isoformat()}: {message}")
    db.upsert_anchor(
        client.chain_id, client.contract_address, day, status="failed", last_error=message, **fields
    )
    return {"day": day.isoformat(), "status": "failed", "error": message}


def run_usage_anchor_once(
    day: date | None = None,
    client: UsageAnchorClient | None = None,
    wait_seconds: float = RECEIPT_WAIT_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Anchor one finished UTC day (default: yesterday). Idempotent."""
    with _run_lock:
        return _run_usage_anchor_locked(day, client, wait_seconds, now)


def _run_usage_anchor_locked(
    day: date | None,
    client: UsageAnchorClient | None,
    wait_seconds: float,
    now: datetime | None,
) -> dict[str, Any]:
    if not Config.USAGE_ANCHOR_ENABLED:
        return {"skipped": "disabled"}
    salt_key = _require_salt_key()
    today = utc_today(now)
    day = day or (today - timedelta(days=1))
    if day >= today:
        raise UsageAnchorError(f"{day.isoformat()} is not a finished UTC day")
    client = client or UsageAnchorClient.from_config()
    chain_id, contract = client.chain_id, client.contract_address
    idx = day_index(day)

    existing = db.get_anchor(chain_id, contract, day)
    if existing and existing.get("status") == "confirmed":
        return {"day": day.isoformat(), "status": "confirmed", "noop": True}

    tree = build_day_tree(day, db.fetch_usage_rows_for_day(day), salt_key)
    if tree is None:
        if existing and existing.get("root"):
            return _fail(client, day, "day now has no usage rows but a root was already built")
        db.upsert_anchor(chain_id, contract, day, status="skipped_empty", leaf_count=0)
        return {"day": day.isoformat(), "status": "skipped_empty"}

    new_root = root_hex(tree.root)
    if existing and existing.get("root") and existing["root"] != new_root:
        return _fail(
            client,
            day,
            f"rebuilt root {new_root} differs from stored root {existing['root']} -- not sending",
        )

    onchain = client.root_of(idx)
    if onchain is not None:
        if onchain == tree.root:
            db.upsert_anchor(
                chain_id,
                contract,
                day,
                root=new_root,
                leaf_count=tree.leaf_count,
                status="confirmed",
                last_error=None,
            )
            return {"day": day.isoformat(), "status": "confirmed", "root": new_root}
        return _fail(
            client,
            day,
            f"on-chain root {root_hex(onchain)} differs from rebuilt root {new_root}",
            root=new_root,
            leaf_count=tree.leaf_count,
        )

    status = existing.get("status") if existing else None
    if status == "pending" and existing.get("tx_hash"):
        # In flight; the sweep confirms it or detects a reorg.
        return {"day": day.isoformat(), "status": "pending", "tx_hash": existing["tx_hash"]}
    if status == "failed":
        return {"day": day.isoformat(), "status": "failed", "noop": True}

    attempts = int((existing or {}).get("attempts") or 0)
    if attempts >= MAX_SEND_ATTEMPTS:
        return _fail(client, day, f"not anchored after {attempts} broadcast attempts")

    db.upsert_anchor(
        chain_id,
        contract,
        day,
        root=new_root,
        leaf_count=tree.leaf_count,
        status=status if status in ("reorged", "unanchored") else "unanchored",
        attempts=attempts,
    )

    def _record(signed: SignedAnchor) -> bool:
        db.upsert_anchor(
            chain_id,
            contract,
            day,
            status="pending",
            tx_hash=signed.tx_hash,
            tx_nonce=signed.nonce,
            block_number=None,
            attempts=attempts + 1,
            last_error=None,
        )
        return True

    try:
        signed = client.send_anchor(idx, tree.root, _record)
    except AnchorNotSentError as e:
        # Nothing reached the network; the sweep retries unanchored days.
        logger.error("Usage anchor for %s not sent: %s", day.isoformat(), e)
        db.upsert_anchor(chain_id, contract, day, status="unanchored", last_error=str(e)[:500])
        return {"day": day.isoformat(), "status": "unanchored", "error": str(e)}
    except AnchorBroadcastError as e:
        logger.error("Usage anchor broadcast for %s uncertain: %s", day.isoformat(), e)
        db.upsert_anchor(chain_id, contract, day, last_error=str(e)[:500])
        return {"day": day.isoformat(), "status": "pending", "tx_hash": e.tx_hash}

    result = {
        "day": day.isoformat(),
        "status": "pending",
        "root": new_root,
        "leaf_count": tree.leaf_count,
        "tx_hash": signed.tx_hash,
    }
    if wait_seconds > 0 and client.wait_for_receipt(signed.tx_hash, wait_seconds) is not None:
        row = db.get_anchor(chain_id, contract, day)
        if row:
            result["status"] = _reconcile_pending(client, row)
    return result


def _reconcile_pending(client: UsageAnchorClient, row: dict[str, Any]) -> str:
    """Advance one pending row. Returns its new status."""
    day = date.fromisoformat(str(row["day"]))
    chain_id, contract = client.chain_id, client.contract_address
    expected = _root_bytes(row["root"])
    tx_hash = row.get("tx_hash")
    receipt = client.get_receipt(tx_hash) if tx_hash else None

    if receipt is not None:
        block = int(receipt.get("blockNumber") or 0)
        if int(receipt.get("status", 1)) == 0:
            onchain = client.root_of(day_index(day))
            if onchain == expected:
                db.upsert_anchor(chain_id, contract, day, status="confirmed", last_error=None)
                return "confirmed"
            if onchain is not None:
                _fail(client, day, f"tx {tx_hash} reverted and chain holds a different root")
                return "failed"
            db.upsert_anchor(
                chain_id, contract, day, status="reorged", last_error=f"tx {tx_hash} reverted"
            )
            return "reorged"
        confirmations = client.block_number() - block + 1
        if confirmations < Config.USAGE_ANCHOR_MIN_CONFIRMATIONS:
            db.upsert_anchor(chain_id, contract, day, block_number=block)
            return "pending"
        onchain = client.root_of(day_index(day))
        if onchain != expected:
            _fail(
                client,
                day,
                f"tx {tx_hash} mined but rootOf returns "
                f"{root_hex(onchain) if onchain else 'nothing'}",
            )
            return "failed"
        db.upsert_anchor(
            chain_id, contract, day, status="confirmed", block_number=block, last_error=None
        )
        logger.info("✅ Usage anchor confirmed: day=%s tx=%s block=%s", day, tx_hash, block)
        return "confirmed"

    # No receipt.
    onchain = client.root_of(day_index(day))
    if onchain == expected:
        db.upsert_anchor(chain_id, contract, day, status="confirmed", last_error=None)
        return "confirmed"
    if onchain is not None:
        _fail(client, day, "chain holds a different root than the one we sent")
        return "failed"
    seen_mined = row.get("block_number") is not None
    nonce = row.get("tx_nonce")
    replaced = nonce is not None and client.confirmed_nonce() > int(nonce)
    if seen_mined or replaced:
        reason = "receipt vanished (reorg)" if seen_mined else "tx dropped or replaced"
        logger.warning("Usage anchor %s: %s, tx=%s", day, reason, tx_hash)
        db.upsert_anchor(
            chain_id, contract, day, status="reorged", block_number=None, last_error=reason
        )
        return "reorged"
    return "pending"


def sweep_usage_anchors(
    client: UsageAnchorClient | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Confirm pending anchors, resubmit reorged ones (once), retry days
    whose send never reached the network."""
    if not Config.USAGE_ANCHOR_ENABLED:
        return {"skipped": "disabled"}
    client = client or UsageAnchorClient.from_config()
    with _run_lock:
        return _sweep_locked(client, utc_today(now), now)


def _sweep_locked(client: UsageAnchorClient, today: date, now: datetime | None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    rows = db.list_anchors(
        client.chain_id,
        client.contract_address,
        limit=50,
        statuses=["pending", "reorged", "unanchored"],
    )
    for row in rows:
        day = date.fromisoformat(str(row["day"]))
        try:
            if row["status"] == "pending":
                status = _reconcile_pending(client, row)
                if status == "reorged":
                    status = run_usage_anchor_once(day, client, wait_seconds=0, now=now)["status"]
            elif today - day <= timedelta(days=SWEEP_LOOKBACK_DAYS):
                status = run_usage_anchor_once(day, client, wait_seconds=0, now=now)["status"]
            else:
                status = "stale"
        except Exception as e:
            logger.error("Usage anchor sweep failed for %s: %s", day, e)
            status = "error"
        counts[status] = counts.get(status, 0) + 1
    return {"checked": len(rows), "by_status": counts}


# ---------------------------------------------------------------------------
# Reads (public API + proofs) -- never need the signing key
# ---------------------------------------------------------------------------


def anchor_view(row: dict[str, Any]) -> dict[str, Any]:
    """Public view of one anchor. Deliberately omits leaf_count: daily
    request volume is not disclosed (only the root is public)."""
    chain_id = int(row["chain_id"])
    return {
        "day": str(row["day"]),
        "day_index": int(row["day_index"]),
        "root": row.get("root"),
        "status": row.get("status"),
        "tx_hash": row.get("tx_hash"),
        "block_number": row.get("block_number"),
        "explorer_url": explorer_tx_url(chain_id, row.get("tx_hash")),
    }


def build_proof(ledger_id: int, user_id: int) -> dict[str, Any]:
    """Inclusion proof for one of the caller's own api_usage rows."""
    contract = Config.USAGE_ANCHOR_CONTRACT_ADDRESS
    if not contract:
        raise ProofNotAvailableError("usage anchoring is not configured")
    salt_key = _require_salt_key()
    row = db.get_usage_row(ledger_id)
    if row is None or int(row.get("user_id") or -1) != int(user_id):
        raise ProofNotFoundError("no such usage record")
    day = _parse_ts(row["created_at"]).date()
    chain_id = Config.USAGE_ANCHOR_CHAIN_ID
    anchor = db.get_anchor(chain_id, contract, day)
    if not anchor or anchor.get("status") not in ("pending", "confirmed") or not anchor.get("root"):
        raise ProofNotAvailableError(f"{day.isoformat()} is not anchored yet")
    tree = build_day_tree(day, db.fetch_usage_rows_for_day(day), salt_key)
    if tree is None or root_hex(tree.root) != anchor["root"]:
        _alert(f"{day.isoformat()}: proof rebuild does not match the anchored root")
        raise ProofNotAvailableError("this day's records no longer match the anchored root")
    index = tree.ledger_ids.index(int(ledger_id))
    proof = inclusion_proof_from_hashes(index, tree.leaf_hashes)
    return {
        "leaf": tree.leaves[index].decode("utf-8"),
        "leaf_hash": "0x" + tree.leaf_hashes[index].hex(),
        "leaf_index": index,
        "tree_size": tree.leaf_count,
        "proof": ["0x" + p.hex() for p in proof],
        "root": anchor["root"],
        "day": day.isoformat(),
        "day_index": day_index(day),
        "chain_id": chain_id,
        "contract_address": contract,
        "tx_hash": anchor.get("tx_hash"),
        "status": anchor.get("status"),
        "explorer_url": explorer_tx_url(chain_id, anchor.get("tx_hash")),
    }
