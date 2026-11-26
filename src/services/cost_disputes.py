"""Customer cost disputes on job usage lines (Gatewayz x GenLayer, shadow mode).

A key owner contests lines of a sealed job ("this cost too much for what I got").
Gatewayz builds each line's v2 entry, its Merkle proof against the job's sealed
``usage_root_v2`` and, when the owner kept it, a link to the line's content, and
opens a dispute on the CostDispute Intelligent Contract. GenLayer validators rule
under the Fair Billing Policy; Gatewayz records the verdict. **No money moves**:
refunds stay at zero until the escrow ledger ships.

    submitted ──open tx──▶ adjudicating ──ACCEPTED──▶ decided ──FINALIZED──▶ final
        │                       │
        │ non-retryable/3 tries │ consensus failed / contract refused
        ▼                       ▼
      error                 undetermined / error      (lines released)

Config (env), read here so the shared Config class is untouched:
  COST_DISPUTES_ENABLED              true to accept disputes (default false)
  GENLAYER_NETWORK                   studionet | bradbury (shared with Verify)
  GENLAYER_COST_DISPUTE_CONTRACT     deployed CostDispute address
  GENLAYER_DISPUTE_SUBMITTER_KEY     key allowlisted as a CostDispute submitter
  COST_DISPUTE_POLICY_URI            immutable URL of the approved policy JSON
  COST_DISPUTE_WINDOW_HOURS          how long after a job closes it can be disputed (72)
  PRICE_SHEET_BASE                   public base serving /v1/price-sheets/{hash}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from src.db import job_disputes as db

logger = logging.getLogger(__name__)

VIEW_SCHEMA_VERSION = 1
MAX_LINES = 10  # the policy's max_lines (fbp-v5); the contract enforces it too
MAX_CLAIM_CHARS = 1000
MAX_URI_CHARS = 512
MAX_SUBMIT_ATTEMPTS = 3
MAX_POLICY_BYTES = 64_000
ZERO_HASH = "0x" + "00" * 32
DEFAULT_POLICY_URI = (
    "https://raw.githubusercontent.com/Alpaca-Network/gatewayz-genlayer/"
    "131dce1639f637ea523e6c3fe07a11a257870752/policies/fbp-v5.json"
)
# Contract UserErrors that fail identically on every retry.
_PERMANENT_ERRORS = (
    "submitter_not_allowed",
    "dispute_exists",
    "line_already_disputed",
    "line_not_in_epoch",
    "policy_not_allowed",
    "price_sheet_mismatch",
    "invalid_",
)


class DisputeError(Exception):
    """A request the API should refuse: carries the HTTP status and error code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --------------------------------------------------------------------------- config


def config() -> dict[str, Any]:
    vault_base = os.environ.get("DISPUTE_VAULT_PUBLIC_BASE") or "https://api.gatewayz.ai"
    return {
        "enabled": os.environ.get("COST_DISPUTES_ENABLED", "false").lower() in {"1", "true", "yes"},
        "network": os.environ.get("GENLAYER_NETWORK", "studionet"),
        "contract": os.environ.get("GENLAYER_COST_DISPUTE_CONTRACT", ""),
        "policy_uri": os.environ.get("COST_DISPUTE_POLICY_URI") or DEFAULT_POLICY_URI,
        "window_hours": int(os.environ.get("COST_DISPUTE_WINDOW_HOURS", "72")),
        "price_sheet_base": (
            os.environ.get("PRICE_SHEET_BASE") or f"{vault_base.rstrip('/')}/v1/price-sheets/"
        ),
    }


def is_configured() -> bool:
    cfg = config()
    return bool(
        cfg["enabled"] and cfg["contract"] and os.environ.get("GENLAYER_DISPUTE_SUBMITTER_KEY")
    )


# --------------------------------------------------------------------------- chain


class DisputeClient:
    """Thin client for the CostDispute Intelligent Contract. Chain-specific code lives
    here and in genlayer_verify's shared helpers, so the lifecycle can be tested with
    a fake."""

    def __init__(self) -> None:
        from genlayer_py import create_account, create_client
        from genlayer_py.chains import studionet, testnet_bradbury

        from src.services.genlayer_verify import _hex_chain_id_in_gas_estimates

        cfg = config()
        chains = {"studionet": studionet, "bradbury": testnet_bradbury}
        if cfg["network"] not in chains:
            raise RuntimeError(f"unknown GENLAYER_NETWORK {cfg['network']!r}")
        self.network = cfg["network"]
        self.contract = cfg["contract"]
        self._client = create_client(
            chain=chains[cfg["network"]],
            account=create_account(os.environ["GENLAYER_DISPUTE_SUBMITTER_KEY"]),
        )
        _hex_chain_id_in_gas_estimates(self._client)

    def open_dispute(self, args: list) -> str:
        from src.services.genlayer_verify import write_with_fees

        call = {"address": self.contract, "function_name": "open_dispute", "args": args}
        return write_with_fees(self._client, call)

    def tx_state(self, tx: str):
        from src.services.genlayer_verify import tx_state_of

        return tx_state_of(self._client, tx)

    def leader_error(self, tx: str) -> str | None:
        from src.services.genlayer_verify import leader_error

        return leader_error(self._client, tx)

    def read_dispute(self, dispute_id: str, final: bool) -> dict:
        from genlayer_py.types import TransactionHashVariant

        variant = (
            TransactionHashVariant.LATEST_FINAL if final else TransactionHashVariant.LATEST_NONFINAL
        )
        return dict(
            self._client.read_contract(
                address=self.contract,
                function_name="get_dispute",
                args=[dispute_id],
                transaction_hash_variant=variant,
            )
        )


_client: DisputeClient | None = None


def get_client() -> DisputeClient:
    global _client
    if _client is None:
        _client = DisputeClient()
    return _client


_policy_cache: dict[str, str] = {}


def policy_text(uri: str) -> str:
    """The approved policy JSON, byte for byte. Fetched from an immutable (commit-pinned)
    URL and cached; the contract refuses any policy whose hash it has not approved."""
    if uri in _policy_cache:
        return _policy_cache[uri]
    if not uri.startswith("https://"):
        raise RuntimeError("COST_DISPUTE_POLICY_URI must be https")
    req = urllib.request.Request(uri, headers={"User-Agent": "gatewayz-cost-disputes"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - https only, checked above
        body = resp.read(MAX_POLICY_BYTES + 1)
    if len(body) > MAX_POLICY_BYTES:
        raise RuntimeError("policy document too large")
    text = body.decode("utf-8")
    json.loads(text)  # must be JSON; the contract parses it again
    _policy_cache[uri] = text
    return text


# --------------------------------------------------------------------------- open


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def validate_request(
    job: dict,
    entries_v2: list[dict] | None,
    indexes: list[int],
    claims: dict[str, str],
    content_uris: dict[str, str],
    already: set[int],
    now: datetime | None = None,
) -> None:
    """Raise DisputeError for anything the contract or the window rule would refuse."""
    from src.services import job_usage_v2

    cfg = config()
    if not job.get("usage_root_v2") or entries_v2 is None:
        raise DisputeError(409, "job_not_sealed_v2", "Close the job before disputing its lines.")
    if job_usage_v2.seal(entries_v2)["root"] != job["usage_root_v2"]:
        logger.error("v2 usage log for %s no longer matches its sealed root", job["job_id"])
        raise DisputeError(500, "usage_root_mismatch", "The stored log does not match its seal.")
    closed = _parse_ts(job.get("closed_at"))
    if closed is None:
        raise DisputeError(409, "job_not_closed", "Close the job before disputing its lines.")
    if (now or _now()) > closed + timedelta(hours=cfg["window_hours"]):
        raise DisputeError(
            409,
            "dispute_window_closed",
            f"Lines can be disputed for {cfg['window_hours']} hours after the job closes.",
        )
    if not 1 <= len(indexes) <= MAX_LINES or len(set(indexes)) != len(indexes):
        raise DisputeError(422, "invalid_lines", f"Dispute 1 to {MAX_LINES} distinct lines.")
    for i in indexes:
        if not 0 <= i < len(entries_v2):
            raise DisputeError(
                404, "usage_line_not_found", f"The job has {len(entries_v2)} usage lines."
            )
        if not entries_v2[i]:
            raise DisputeError(422, "usage_line_has_no_v2_entry", f"Line {i} cannot be disputed.")
    extra = (set(claims) | set(content_uris)) - {str(i) for i in indexes}
    if extra:
        raise DisputeError(
            422, "invalid_lines", f"claims/content_uris for lines not disputed: {sorted(extra)}"
        )
    for k, c in claims.items():
        if not isinstance(c, str) or len(c) > MAX_CLAIM_CHARS:
            raise DisputeError(
                422, "invalid_claim", f"claim {k}: text of at most {MAX_CLAIM_CHARS} chars"
            )
    for k, u in content_uris.items():
        if not isinstance(u, str) or not u.startswith("https://") or len(u) > MAX_URI_CHARS:
            raise DisputeError(
                422, "invalid_content_uri", f"content_uris {k}: an https URL under 512 chars"
            )
    taken = sorted(set(indexes) & already)
    if taken:
        raise DisputeError(409, "line_already_disputed", f"Lines already disputed: {taken}")


def new_dispute_id(job_id: str, indexes: list[int]) -> str:
    seed = f"{job_id}:{sorted(indexes)}:{secrets.token_hex(16)}"
    return "0x" + hashlib.sha256(seed.encode()).hexdigest()


def open_dispute(
    job: dict,
    user_id: int,
    indexes: list[int],
    claims: dict[str, str],
    content_uris: dict[str, str],
) -> dict:
    """Validate and record a dispute (status `submitted`). The caller submits it in
    the background; the poller retries anything left `submitted`."""
    from src.db.inference_jobs import list_usage_v2

    if not is_configured():
        raise DisputeError(404, "cost_disputes_disabled", "Cost disputes are not enabled.")
    entries_v2 = list_usage_v2(job["job_id"])
    already = db.disputed_lines(job["job_id"])
    validate_request(job, entries_v2, indexes, claims, content_uris, already)
    cfg = config()
    row = {
        "dispute_id": new_dispute_id(job["job_id"], indexes),
        "job_id": job["job_id"],
        "user_id": user_id,
        "usage_root": job["usage_root_v2"],
        "line_indexes": sorted(indexes),
        "claims": claims,
        "content_uris": content_uris,
        "network": cfg["network"],
        "contract_address": cfg["contract"],
        "policy_uri": cfg["policy_uri"],
    }
    try:
        return db.insert_dispute(row)
    except db.LinesAlreadyDisputed as e:
        raise DisputeError(409, "line_already_disputed", "A line was disputed meanwhile.") from e


# --------------------------------------------------------------------------- submit


def build_lines(dispute: dict, entries_v2: list[dict]) -> list[dict]:
    """Each line: its v2 entry, Merkle proof and claim, plus a content link when the
    owner gave one or the dispute vault kept the request (else judged on metadata)."""
    from src.services import dispute_vault, job_usage_v2

    vault = dispute_vault.enabled()
    lines = []
    for i in dispute["line_indexes"]:
        p = job_usage_v2.proof_for(entries_v2, i)
        uri = (dispute.get("content_uris") or {}).get(str(i)) or ""
        if not uri and vault:
            kept = dispute_vault.reveal(dispute["job_id"], p["entry"]["request_id"])
            uri = (kept or {}).get("content_uri") or ""
        claim = (dispute.get("claims") or {}).get(str(i)) or "This cost too much for what I got."
        lines.append(
            {
                "index": i,
                "entry": p["entry"],
                "proof": p["proof"],
                "content_uri": uri,
                "claim": claim,
            }
        )
    return lines


def submit_args(dispute: dict, lines: list[dict], policy: str) -> list:
    # price_sheet_hash = 0: the contract fetches each line's own sheet from the base
    # URL + the line's price_sheet hash, so one dispute can cover several models.
    return [
        dispute["dispute_id"],
        dispute["job_id"],
        dispute["usage_root"],
        policy,
        config()["price_sheet_base"],
        ZERO_HASH,
        json.dumps(lines, separators=(",", ":")),
    ]


def _fail(dispute: dict, status: str, error: str, extra: dict | None = None) -> dict:
    db.release_lines(dispute["dispute_id"])
    return db.update_dispute(
        dispute["dispute_id"], {**(extra or {}), "status": status, "error": error[:500]}
    )


def submit(dispute: dict, client) -> dict:
    """Send the dispute transaction. Retryable failures stay `submitted` (the poller tries
    again); permanent ones, or the 3rd failure, end in `error` and release the lines."""
    from src.db.inference_jobs import list_usage_v2

    attempts = int(dispute.get("submit_attempts") or 0) + 1
    try:
        entries_v2 = list_usage_v2(dispute["job_id"]) or []
        lines = build_lines(dispute, entries_v2)
        tx = client.open_dispute(submit_args(dispute, lines, policy_text(dispute["policy_uri"])))
    except Exception as e:
        msg = str(e)[:500]
        permanent = any(p in msg for p in _PERMANENT_ERRORS)
        if permanent or attempts >= MAX_SUBMIT_ATTEMPTS:
            logger.error("dispute %s submission failed for good: %s", dispute["dispute_id"], msg)
            return _fail(dispute, "error", msg, {"submit_attempts": attempts})
        logger.warning(
            "dispute %s submission attempt %d failed: %s", dispute["dispute_id"], attempts, msg
        )
        return db.update_dispute(
            dispute["dispute_id"],
            {"submit_attempts": attempts, "error": msg, "last_checked_at": _now().isoformat()},
        )
    return db.update_dispute(
        dispute["dispute_id"],
        {"status": "adjudicating", "genlayer_tx": tx, "submit_attempts": attempts, "error": None},
    )


# --------------------------------------------------------------------------- refresh


def _verdict_fields(v: dict) -> dict:
    return {
        "verdict": json.loads(json.dumps(v, default=str)),
        "billed_usd": str(v.get("billed_usd")) if v.get("billed_usd") is not None else None,
        "refund_usd": str(v.get("refund_usd")) if v.get("refund_usd") is not None else None,
    }


def refresh(dispute: dict, client) -> dict:
    """Advance one open dispute from chain state. Returns the (possibly updated) row."""
    if dispute["status"] == "submitted" or not dispute.get("genlayer_tx"):
        return submit(dispute, client)
    tx = dispute["genlayer_tx"]
    state = client.tx_state(tx)
    stamp = {"last_checked_at": _now().isoformat()}
    if state is None:
        return db.update_dispute(dispute["dispute_id"], stamp)
    if state.recipient and state.recipient.lower() != dispute["contract_address"].lower():
        return _fail(dispute, "error", f"dispute tx {tx} targets {state.recipient}", stamp)
    if state.failed_execution:
        reason = client.leader_error(tx) or "CostDispute execution failed"
        return _fail(dispute, "error", f"contract refused the dispute: {reason}", stamp)
    if state.dead:
        return _fail(dispute, "undetermined", f"consensus: {state.status}/{state.result}", stamp)
    if state.final:
        v = client.read_dispute(dispute["dispute_id"], final=True)
        return db.update_dispute(
            dispute["dispute_id"],
            {
                **stamp,
                **_verdict_fields(v),
                "status": "final",
                "finalized_at": _now().isoformat(),
                "decided_at": dispute.get("decided_at") or _now().isoformat(),
            },
        )
    if state.decided and dispute["status"] == "adjudicating":
        v = client.read_dispute(dispute["dispute_id"], final=False)
        return db.update_dispute(
            dispute["dispute_id"],
            {**stamp, **_verdict_fields(v), "status": "decided", "decided_at": _now().isoformat()},
        )
    return db.update_dispute(dispute["dispute_id"], stamp)


def view(d: dict) -> dict[str, Any]:
    """Dispute view schema v1. `refund_usd` is the verdict's ruling; in shadow mode it is
    recorded only, never paid (`settled` stays false)."""
    v = d.get("verdict") or {}
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "dispute_id": d["dispute_id"],
        "job_id": d["job_id"],
        "status": d["status"],
        "final": d["status"] == "final",
        "lines": d["line_indexes"],
        "billed_usd": d.get("billed_usd"),
        "refund_usd": d.get("refund_usd"),
        "rulings": v.get("lines") or [],
        "policy_hash": v.get("policy_hash"),
        "settled": False,
        "shadow_mode": True,
        "network": d.get("network"),
        "contract": d.get("contract_address"),
        "genlayer_tx": d.get("genlayer_tx"),
        "usage_root": d["usage_root"],
        "created_at": d.get("created_at"),
        "decided_at": d.get("decided_at"),
        "finalized_at": d.get("finalized_at"),
        "error": d.get("error"),
    }


def changed(before: dict, after: dict) -> bool:
    return (before.get("status"), before.get("refund_usd")) != (
        after.get("status"),
        after.get("refund_usd"),
    )


def submit_now(dispute_id: str) -> None:
    """Background task after the API records a dispute: submit it right away instead of
    waiting for the poller. Any failure is left to the poller's retry rules."""
    from src.services.outbound_webhooks import emit

    try:
        d = db.get_dispute(dispute_id)
        if not d or d["status"] != "submitted":
            return
        after = submit(d, get_client())
        if changed(d, after):
            emit(after["user_id"], "job.dispute.updated", view(after))
    except Exception as e:
        logger.warning("immediate submit of dispute %s failed: %s", dispute_id, e)


async def poll_open_disputes() -> int:
    """Scheduler job: advance every open dispute, and webhook each state change."""
    import asyncio

    from src.services.outbound_webhooks import emit

    if not is_configured():
        return 0
    client = get_client()
    n = 0
    for d in await asyncio.to_thread(db.open_disputes_due, 15):
        try:
            after = await asyncio.to_thread(refresh, d, client)
        except Exception as e:
            logger.warning("dispute poll of %s failed: %s", d["dispute_id"], e)
            continue
        if changed(d, after):
            n += 1
            await asyncio.to_thread(emit, after["user_id"], "job.dispute.updated", view(after))
    return n
