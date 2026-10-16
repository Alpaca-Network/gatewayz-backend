"""Job-scoped keys and sealed usage records (Gatewayz x GenLayer inference escrow).

A job key is an ordinary api_keys_new row named ``job:<job_id>``. Two hooks touch
it, and both are no-ops for every other key:

* ``enforce_job_key`` — called from key validation on every request. It refuses a
  job key whose job is closed, past its deadline, or at its USD cap.
* ``record_job_usage`` — called after a completed, billed request. It appends one
  line of billing metadata to the job's usage log.

The usage log hashes exactly as ``gzgl/usage.py`` in Alpaca-Network/gatewayz-genlayer
and ``InferenceEscrow.verifyUsageLeaf`` on-chain:

    leaf = keccak256(0x00 || canonical_json(entry))
    node = keccak256(0x01 || min(a, b) || max(a, b))      # odd node promoted

``tests/services/test_job_usage.py`` pins a root computed by that repo, so a drift
between the two implementations fails CI here.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from web3 import Web3

logger = logging.getLogger(__name__)

JOB_KEY_PREFIX = "job:"
ENTRY_FIELDS = ("ts", "model", "provider", "tokens_in", "tokens_out", "cost_usd", "commit")

# Non-empty and without "api_keys", so validate_api_key_permissions refuses every
# key-management call made with a job key (an empty dict would mean "allow all").
JOB_KEY_SCOPES = {"read": ["inference"], "write": ["inference"]}

JOB_CAP_REACHED = "Job spend cap reached"
JOB_NOT_RUNNING = "Job is not running"


def job_key_name(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def is_job_key_name(key_name: str | None) -> bool:
    return bool(key_name) and key_name.startswith(JOB_KEY_PREFIX)


# --------------------------------------------------------------------------- hashing


def _keccak(data: bytes) -> bytes:
    return bytes(Web3.keccak(data))


def format_usd(value: Any) -> str:
    """Money as a canonical decimal string; floats would make hashes format-dependent."""
    d = Decimal(str(value)).normalize()
    return format(d, "f")


def canonical_entry(entry: dict) -> bytes:
    missing = [f for f in ENTRY_FIELDS if f not in entry]
    extra = set(entry) - set(ENTRY_FIELDS)
    if missing or extra:
        raise ValueError(f"usage entry fields: missing={missing} extra={sorted(extra)}")
    e = dict(entry)
    for f in ("tokens_in", "tokens_out"):
        if not isinstance(e[f], int) or isinstance(e[f], bool) or e[f] < 0:
            raise ValueError(f"{f} must be a non-negative int")
    e["cost_usd"] = format_usd(e["cost_usd"])
    return json.dumps(e, sort_keys=True, separators=(",", ":")).encode()


def leaf_hash(entry: dict) -> bytes:
    return _keccak(b"\x00" + canonical_entry(entry))


def _node(a: bytes, b: bytes) -> bytes:
    lo, hi = (a, b) if a < b else (b, a)
    return _keccak(b"\x01" + lo + hi)


def _levels(leaves: list[bytes]) -> list[list[bytes]]:
    if not leaves:
        raise ValueError("empty usage record")
    levels = [leaves]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = [_node(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2:
            nxt.append(cur[-1])
        levels.append(nxt)
    return levels


def merkle_root(leaves: list[bytes]) -> bytes:
    return _levels(leaves)[-1][0]


def merkle_proof(leaves: list[bytes], index: int) -> list[bytes]:
    if not 0 <= index < len(leaves):
        raise IndexError(index)
    proof = []
    for level in _levels(leaves)[:-1]:
        if (index ^ 1) < len(level):
            proof.append(level[index ^ 1])
        index //= 2
    return proof


ZERO_ROOT = "0x" + "00" * 32


def seal(entries: list[dict]) -> dict:
    """Merkle root + totals for a job's usage log (entries in seq order).
    A job with no requests seals to the zero root, which proves nothing on-chain."""
    if not entries:
        return {"root": ZERO_ROOT, "requests": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": "0"}
    leaves = [leaf_hash(e) for e in entries]
    return {
        "root": "0x" + merkle_root(leaves).hex(),
        "requests": len(entries),
        "tokens_in": sum(e["tokens_in"] for e in entries),
        "tokens_out": sum(e["tokens_out"] for e in entries),
        "cost_usd": format_usd(sum((Decimal(str(e["cost_usd"])) for e in entries), Decimal(0))),
    }


def proof_for(entries: list[dict], index: int) -> dict:
    leaves = [leaf_hash(e) for e in entries]
    return {
        "index": index,
        "entry": entries[index],
        "leaf": "0x" + leaves[index].hex(),
        "proof": ["0x" + p.hex() for p in merkle_proof(leaves, index)],
        "root": "0x" + merkle_root(leaves).hex(),
    }


# --------------------------------------------------------------------------- hooks


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def enforce_job_key(key_data: dict, client: Any) -> None:
    """Raise ValueError if a job key may not spend. No-op for non-job keys.

    The cap is checked before each request; cost is only known afterwards, so the
    last admitted request can take spend past the cap by at most its own cost.
    """
    if not is_job_key_name(key_data.get("key_name")):
        return
    result = (
        client.table("inference_jobs")
        .select("status,cap_usd,spent_usd,deadline")
        .eq("api_key_id", key_data["id"])
        .execute()
    )
    if not result.data:
        # A user-chosen key name that merely looks like a job key. Not ours.
        return
    job = result.data[0]
    if job["status"] != "running" or _parse_ts(job["deadline"]) <= datetime.now(UTC):
        raise ValueError(JOB_NOT_RUNNING)
    if Decimal(str(job["spent_usd"])) >= Decimal(str(job["cap_usd"])):
        raise ValueError(JOB_CAP_REACHED)


def record_job_usage(
    user: dict | None,
    model: str,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    commit: str,
) -> int | None:
    """Append one billing-metadata line for a job key. Returns the seq, or None.

    Never raises into the request path, but a lost line is NOT quiet: it is logged
    at ERROR with the job and cost, because a sealed record missing a line no longer
    matches what the account was charged.
    """
    if not user or not is_job_key_name(user.get("key_name")):
        return None
    job_id = user["key_name"][len(JOB_KEY_PREFIX) :]
    entry = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model or "unknown",
        "provider": provider or "unknown",
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": format_usd(cost_usd or 0),
        "commit": (commit or "")[:64],
    }
    try:
        from src.db.inference_jobs import append_usage

        return append_usage(job_id, entry)
    except Exception as e:
        logger.error(
            "job usage line LOST: job=%s model=%s cost_usd=%s error=%s",
            job_id,
            entry["model"],
            entry["cost_usd"],
            e,
        )
        return None
