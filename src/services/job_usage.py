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
# "not allowed" maps to 403 not_allowed in deps._KEY_FAILURE_MAP.
JOB_ROUTE_NOT_ALLOWED = "Route not allowed for a job-scoped key"
JOB_BUSY = "Job has too many requests in flight"

# The only billed routes whose billing appends a usage line (chat_handler, and
# /v1/completions, which is translated to a chat completion). A job key spending
# anywhere else would bypass both the cap (only the append raises spent_usd) and
# the sealed record, so it is refused there. Reads (GET) stay open: they bill nothing.
JOB_KEY_BILLED_PATHS = frozenset({"/v1/chat/completions", "/v1/completions", "/v1/messages"})
_JOB_KEY_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


def job_route_allowed(method: str | None, path: str | None) -> bool:
    """Whether a job key may call this route. No method/path (a caller that does
    not pass them) is not checked."""
    if not method or not path or method.upper() in _JOB_KEY_READ_METHODS:
        return True
    return path.rstrip("/") in JOB_KEY_BILLED_PATHS


def enforce_job_key(
    key_data: dict,
    client: Any,
    *,
    method: str | None = None,
    path: str | None = None,
    lease_sink: list | None = None,
) -> None:
    """Raise ValueError if a job key may not spend. No-op for non-job keys.

    The cap is checked before each request; cost is only known afterwards. A
    billed request (a POST, with ``lease_sink`` given) is also admitted through
    ``admit_job_request``, which re-checks the cap under the job's row lock and
    holds at most ``JOB_MAX_INFLIGHT`` requests in flight. Spend can therefore
    pass the cap by at most that many requests' cost. The lease goes into
    ``lease_sink``; JobLeaseMiddleware releases it when the response finishes.
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
    if not job_route_allowed(method, path):
        raise ValueError(JOB_ROUTE_NOT_ALLOWED)
    job = result.data[0]
    if job["status"] != "running" or _parse_ts(job["deadline"]) <= datetime.now(UTC):
        raise ValueError(JOB_NOT_RUNNING)
    if Decimal(str(job["spent_usd"])) >= Decimal(str(job["cap_usd"])):
        raise ValueError(JOB_CAP_REACHED)
    if lease_sink is not None and method and method.upper() not in _JOB_KEY_READ_METHODS:
        lease = _admit(client, key_data["id"])
        if lease is not None:
            lease_sink.append(lease)


_ADMIT_ERRORS = {
    "job_busy": JOB_BUSY,
    "job_cap_reached": JOB_CAP_REACHED,
    "job_not_running": JOB_NOT_RUNNING,
}


def _admit(client: Any, api_key_id: int) -> tuple[str, str] | None:
    """(job_id, lease_id) from admit_job_request, or None when it can't be taken.

    Refusals raise ValueError. Any other failure (e.g. the migration is not applied
    yet) is logged at ERROR and admits the request on the plain checks above: an
    unbounded burst is the pre-lease behaviour, not an outage.
    """
    from src.config import Config

    try:
        result = client.rpc(
            "admit_job_request",
            {
                "p_api_key_id": api_key_id,
                "p_max_inflight": Config.JOB_MAX_INFLIGHT,
                "p_ttl_seconds": Config.JOB_LEASE_TTL_SECONDS,
            },
        ).execute()
    except Exception as e:
        for code, message in _ADMIT_ERRORS.items():
            if code in str(e):
                raise ValueError(message) from e
        logger.error("job admission unavailable, in-flight limit NOT applied: %s", e)
        return None
    data = result.data
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data.get("lease_id"):
        return None
    return data["job_id"], str(data["lease_id"])


def job_lease_of(request: Any) -> tuple[str, str] | None:
    """The in-flight lease taken for this request, if any (request.state.job_lease)."""
    state = getattr(request, "state", None)
    return getattr(state, "job_lease", None) if state is not None else None


def release_job_lease(job_id: str, lease_id: str) -> None:
    """Free an in-flight slot. Never raises: a lease that fails to release expires."""
    try:
        from src.config.supabase_config import get_supabase_client

        get_supabase_client().rpc(
            "release_job_request", {"p_job_id": job_id, "p_lease_id": lease_id}
        ).execute()
    except Exception as e:
        logger.error("job lease not released (expires on its own): job=%s error=%s", job_id, e)


def record_job_usage(
    user: dict | None,
    model: str,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    commit: str,
    *,
    request_id: str | None = None,
    tokens_reasoning: int = 0,
    finish_reason: str | None = None,
    content_hash: str | None = None,
    priced_model: str | None = None,
    usage_estimated: bool = False,
) -> int | None:
    """Append one billing-metadata line for a job key. Returns the seq, or None.

    The v1 entry is exactly as before (``tokens_out`` = billed completion tokens).
    A v2 entry for cost disputes is recorded alongside it (see job_usage_v2):
    ``tokens_reasoning`` is the part of ``tokens_out`` that was hidden reasoning,
    ``priced_model`` is the model whose rates produced ``cost_usd``. If the v2
    entry cannot be built the v1 line is still written — v2 never costs v1 a line.

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
    entry_v2, sheet_body = None, None
    try:
        from src.services import job_usage_v2

        if job_usage_v2.enabled():
            entry_v2, sheet_body = _build_entry_v2(
                entry,
                cost_usd=cost_usd,
                request_id=request_id or commit,
                tokens_reasoning=tokens_reasoning,
                finish_reason=finish_reason,
                content_hash=content_hash,
                priced_model=priced_model,
            )
    except Exception as e:
        logger.error(
            "job usage v2 entry not built: job=%s model=%s error=%s", job_id, entry["model"], e
        )
    try:
        from src.db.inference_jobs import append_usage

        if entry_v2 is None:
            return append_usage(job_id, entry)
        return append_usage(
            job_id,
            entry,
            entry_v2=entry_v2,
            usage_estimated=bool(usage_estimated),
            price_sheet_body=sheet_body,
        )
    except Exception as e:
        logger.error(
            "job usage line LOST: job=%s model=%s cost_usd=%s error=%s",
            job_id,
            entry["model"],
            entry["cost_usd"],
            e,
        )
        return None


def _build_entry_v2(
    entry: dict,
    *,
    cost_usd: Any,
    request_id: str | None,
    tokens_reasoning: int,
    finish_reason: str | None,
    content_hash: str | None,
    priced_model: str | None,
) -> tuple[dict, str | None]:
    from src.services import job_usage_v2 as v2

    billed_out = entry["tokens_out"]
    reasoning = min(max(0, int(tokens_reasoning or 0)), billed_out)
    sheet_hash, sheet_body = _price_sheet_for(entry["model"], priced_model, entry["provider"])
    entry_v2 = {
        "request_id": (request_id or "")[:128],
        "ts": entry["ts"],
        "model": entry["model"],
        "provider": entry["provider"],
        "tokens_in": entry["tokens_in"],
        "tokens_out": billed_out - reasoning,
        "tokens_reasoning": reasoning,
        "cost_usd": v2.usd9(cost_usd),
        "status": 200,
        "finish_reason": (finish_reason or "unknown")[:64],
        "price_sheet": sheet_hash,
        "content_hash": content_hash if _is_hash(content_hash) else v2.ZERO_HASH,
        "commit": entry["commit"],
    }
    v2.canonical_entry(entry_v2)  # refuse a line the contract could not parse
    return entry_v2, sheet_body


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 66
        and value.startswith("0x")
        and all(c in "0123456789abcdef" for c in value[2:])
    )


def _price_sheet_for(
    model: str, priced_model: str | None, provider: str | None = None
) -> tuple[str, str | None]:
    """(hash, body) of the rates this line was charged at; (zero hash, None) if the
    rates cannot be read back, so the line is judged as price_unlisted, not lost."""
    from src.config.config import Config
    from src.services import job_usage_v2 as v2
    from src.services.pricing import get_model_pricing

    try:
        pricing = get_model_pricing(priced_model or model)
        prompt_rate = float(pricing.get("prompt", 0) or 0)
        sheet_hash, body = v2.price_sheet(
            model,
            pricing.get("prompt", 0),
            pricing.get("completion", 0),
            Config.PRICING_MARKUP,
            cache_write_per_token=_cache_write_rate(
                priced_model or model, provider, pricing, prompt_rate
            ),
        )
        return sheet_hash, body.decode()
    except Exception as e:
        logger.warning(
            "job usage v2: no price sheet for %s (priced as %s): %s", model, priced_model, e
        )
        return v2.ZERO_HASH, None


def _cache_write_rate(model: str, provider: str | None, pricing: dict, prompt_rate: float):
    """The per-token rate a prompt-cache write is billed at, as cache_pricing does it."""
    try:
        from src.services.pricing.cache_pricing import resolve_cache_multipliers

        return float(
            pricing.get("cache_write")
            or prompt_rate * resolve_cache_multipliers(model, provider).write
        )
    except Exception as e:  # never cost the sheet over the optional field
        logger.warning("job usage v2: no cache-write rate for %s: %s", model, e)
        return None
