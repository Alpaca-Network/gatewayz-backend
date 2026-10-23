"""v2 usage lines for cost disputes (Gatewayz x GenLayer, shadow mode).

A v2 line is recorded next to the v1 line (``job_usage.py``) for every completed
request on a job key. v1 is untouched: it keeps the keccak tree that
``InferenceEscrow`` verifies. v2 adds what a ``CostDispute`` needs to judge a line:

    {request_id, ts, model, provider, tokens_in, tokens_out, tokens_reasoning,
     cost_usd, status, finish_reason, price_sheet, content_hash, commit}

where ``tokens_out`` is VISIBLE completion tokens (reasoning split out),
``content_hash`` fingerprints the client's original messages plus the visible
output (the text itself is never stored), and ``price_sheet`` is the sha256 of
the rates the charge was computed from, served at ``GET /v1/price-sheets/{hash}``.

Hashing is sha256, not keccak, so the contract can verify proofs with hashlib:

    leaf = sha256(0x00 || canonical_json(entry))
    node = sha256(0x01 || min(a, b) || max(a, b))      # odd node promoted

Everything here MUST stay byte-identical to ``gzgl/billing.py`` in
Alpaca-Network/gatewayz-genlayer. ``tests/services/test_job_usage_v2.py`` replays
that repo's ``fixtures/usage-v2.json``, so a drift fails CI here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

logger = logging.getLogger(__name__)

ENTRY_FIELDS = (
    "request_id",
    "ts",
    "model",
    "provider",
    "tokens_in",
    "tokens_out",
    "tokens_reasoning",
    "cost_usd",
    "status",
    "finish_reason",
    "price_sheet",
    "content_hash",
    "commit",
)
_INT_FIELDS = ("tokens_in", "tokens_out", "tokens_reasoning", "status")
NANO = 10**9
ZERO_HASH = "0x" + "00" * 32
ZERO_ROOT = ZERO_HASH

# The contract refuses a price sheet larger than this (cost_dispute.MAX_SHEET_BYTES).
MAX_SHEET_BYTES = 128_000
# Bumping this changes every sheet hash; only do it with a matching contract change.
PRICE_SHEET_VERSION = "gatewayz-effective-v1"

CONTENT_HASH_HEADER = "X-Gatewayz-Content-Hash"


def enabled() -> bool:
    """Config.JOB_USAGE_V2_ENABLED, read per call so it can be flipped in tests."""
    from src.config.config import Config

    return bool(getattr(Config, "JOB_USAGE_V2_ENABLED", False))


# --------------------------------------------------------------------------- canonical form


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(b: bytes) -> str:
    return "0x" + hashlib.sha256(b).hexdigest()


def to_nano(usd: str) -> int:
    """'0.0012' -> 1_200_000. Exact for up to 9 decimals; same rules as gzgl."""
    s = str(usd).strip()
    if not s or s.startswith("-"):
        raise ValueError(f"bad amount {usd!r}")
    whole, _, frac = s.partition(".")
    if not (whole or "0").isdigit() or (frac and not frac.isdigit()) or len(frac) > 9:
        raise ValueError(f"bad amount {usd!r}")
    return int(whole or "0") * NANO + int((frac + "000000000")[:9])


def nano_to_usd(n: int) -> str:
    whole, frac = divmod(n, NANO)
    frac_s = f"{frac:09d}".rstrip("0")
    return f"{whole}.{frac_s}" if frac_s else str(whole)


def usd9(value: Any) -> str:
    """A money amount rounded half-up to whole nano-dollars, in canonical form.

    v2 amounts carry at most 9 decimals (the contract's unit). A charge is a
    float with more digits; the rounding moves it by under $0.000000001.
    """
    d = Decimal(str(value or 0))
    if not d.is_finite() or d < 0:
        raise ValueError(f"bad amount {value!r}")
    return nano_to_usd(int(d.quantize(Decimal(1) / NANO, rounding=ROUND_HALF_UP) * NANO))


def canonical_entry(entry: dict) -> bytes:
    missing = [f for f in ENTRY_FIELDS if f not in entry]
    extra = set(entry) - set(ENTRY_FIELDS)
    if missing or extra:
        raise ValueError(f"billing entry fields: missing={missing} extra={sorted(extra)}")
    e = dict(entry)
    for f in _INT_FIELDS:
        if not isinstance(e[f], int) or isinstance(e[f], bool) or e[f] < 0:
            raise ValueError(f"{f} must be a non-negative int")
    e["cost_usd"] = nano_to_usd(to_nano(e["cost_usd"]))
    return canonical_json(e)


# --------------------------------------------------------------------------- merkle


def leaf_hash(entry: dict) -> bytes:
    return hashlib.sha256(b"\x00" + canonical_entry(entry)).digest()


def _node(a: bytes, b: bytes) -> bytes:
    lo, hi = (a, b) if a < b else (b, a)
    return hashlib.sha256(b"\x01" + lo + hi).digest()


def _levels(leaves: list[bytes]) -> list[list[bytes]]:
    if not leaves:
        raise ValueError("empty epoch")
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


def seal(entries: list[dict]) -> dict:
    """sha256 root + totals for a job's v2 lines (seq order). Empty seals to zero."""
    if not entries:
        return {"root": ZERO_ROOT, "requests": 0, "cost_usd": "0"}
    return {
        "root": "0x" + merkle_root([leaf_hash(e) for e in entries]).hex(),
        "requests": len(entries),
        "cost_usd": nano_to_usd(sum(to_nano(e["cost_usd"]) for e in entries)),
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


# --------------------------------------------------------------------------- content


def content_hash(messages: Any, output: str) -> str:
    """sha256 of the content bundle the user keeps and reveals on dispute.

    ``messages`` is the request body's ``messages`` array exactly as the client
    sent it (parsed JSON, before any normalisation); ``output`` is the visible
    completion text. Returns ZERO_HASH when the bundle cannot be encoded (e.g. a
    lone surrogate in the text): the line is then judged on metadata only.
    """
    try:
        return sha256_hex(canonical_json({"messages": messages, "output": output or ""}))
    except (TypeError, ValueError, UnicodeEncodeError) as e:
        logger.warning("content_hash: bundle not encodable, recording zero hash: %s", e)
        return ZERO_HASH


# --------------------------------------------------------------------------- tokens


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def reported_reasoning_tokens(usage: Any) -> int:
    """Reasoning tokens as the provider reported them, else 0.

    OpenAI / OpenRouter / DeepSeek: ``completion_tokens_details.reasoning_tokens``.
    xAI: ``reasoning_tokens`` at the top level. Anthropic and Vertex report none on
    the surfaces this gateway uses, so their lines record 0.
    """
    details = _get(usage, "completion_tokens_details")
    return _int(_get(details, "reasoning_tokens")) or _int(_get(usage, "reasoning_tokens"))


def split_completion_tokens(
    usage: Any, prompt_tokens: int, completion_tokens: int
) -> tuple[int, int]:
    """(visible, reasoning) for a BILLED completion count.

    Most providers count reasoning inside ``completion_tokens``; visible is then
    completion − reasoning. Some (xAI) report reasoning on top of it: there
    ``total_tokens`` covers prompt + completion + reasoning, or reasoning exceeds
    completion. Those reasoning tokens were never in the billed count, so the
    line records them as 0 — a dispute must not refund tokens nobody paid for.
    """
    completion_tokens = _int(completion_tokens)
    reasoning = reported_reasoning_tokens(usage)
    if not reasoning:
        return completion_tokens, 0
    total = _get(usage, "total_tokens")
    outside = reasoning > completion_tokens or (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total >= _int(prompt_tokens) + completion_tokens + reasoning
    )
    if outside:
        return completion_tokens, 0
    return completion_tokens - reasoning, reasoning


# --------------------------------------------------------------------------- price sheet


def _per_mtok(per_token: Any, markup: Any) -> str:
    rate = Decimal(str(per_token or 0)) * Decimal(str(markup)) * 10**6
    return usd9(rate)


def price_sheet(
    model: str, prompt_per_token: Any, completion_per_token: Any, markup: Any
) -> tuple[str, bytes]:
    """(sha256, body) of the one-model sheet a charge was computed from.

    Rates are the per-token prices the billing path used, times PRICING_MARKUP,
    per million tokens. The body is the exact canonical JSON whose sha256 is the
    hash: the contract fetches the URL and hashes the bytes it gets back.
    Reasoning tokens are billed at the output rate.
    """
    sheet = {
        "version": PRICE_SHEET_VERSION,
        "models": {
            model: {
                "in_per_mtok": _per_mtok(prompt_per_token, markup),
                "out_per_mtok": _per_mtok(completion_per_token, markup),
            }
        },
    }
    body = canonical_json(sheet)
    if len(body) > MAX_SHEET_BYTES:
        raise ValueError("price sheet too large")
    return sha256_hex(body), body
