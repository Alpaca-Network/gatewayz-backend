"""Gatewayz Verify — case lifecycle (PRD 2026-10-01, Feature 2).

    submitted ──submit tx──▶ adjudicating ──ACCEPTED──▶ decided ──FINALIZED──▶ final
        │                         │                       │
        │ non-retryable / 3 tries │ consensus failed      └─appeal─▶ appealed ──▶ final
        ▼                         ▼
      error (cap refunded)     undetermined

`final` and the two failures are terminal. A failure is never parked in an open
status, so nothing polls a dead case forever and no caller waits on one.

Called per JOB, never per inference request: nothing here sits on the hot path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.db import verify_cases as db
from src.services.genlayer_verify import TxState, config

logger = logging.getLogger(__name__)

VERDICT_SCHEMA_VERSION = 1
MAX_SUBMIT_ATTEMPTS = 3
# Contract UserErrors that will fail identically on every retry.
_PERMANENT_ERRORS = (
    "submitter_not_allowed",
    "case_exists",
    "rubric_invalid",
    "invalid_",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def submit_args(case: dict) -> list:
    from src.services.verify_rubric import canonical

    return [
        case["case_id"],
        case["spec_uri"],
        case["spec_hash"],
        case["deliverable_uri"],
        case["deliverable_hash"],
        case["usage_root"],
        canonical(case["rubric"]),
    ]


def submit(case: dict, client) -> dict:
    """Send the case transaction. Retryable failures stay `submitted` (the poller
    tries again); permanent ones, or the 3rd failure, end in `error` with the
    verify-cap charge refunded, because no GenLayer fee was spent."""
    attempts = int(case.get("submit_attempts") or 0) + 1
    try:
        tx = client.submit_case(submit_args(case))
    except Exception as e:
        msg = str(e)[:500]
        permanent = any(p in msg for p in _PERMANENT_ERRORS)
        if permanent or attempts >= MAX_SUBMIT_ATTEMPTS:
            logger.error("verify case %s submission failed for good: %s", case["case_id"], msg)
            if case.get("api_key_id") and Decimal(str(case.get("charged_usd") or 0)) > 0:
                db.refund_cap(case["api_key_id"], Decimal(str(case["charged_usd"])))
            return db.update_case(
                case["case_id"],
                {"status": "error", "error": msg, "submit_attempts": attempts, "charged_usd": "0"},
            )
        logger.warning(
            "verify case %s submission attempt %d failed: %s", case["case_id"], attempts, msg
        )
        return db.update_case(
            case["case_id"], {"submit_attempts": attempts, "error": msg, "last_checked_at": _now()}
        )
    case = db.update_case(
        case["case_id"],
        {"status": "adjudicating", "genlayer_tx": tx, "submit_attempts": attempts, "error": None},
    )
    if case.get("job_id"):
        db.set_job_genlayer_tx(case["job_id"], tx)
    return case


def refresh(case: dict, client) -> dict:
    """Advance one open case from chain state. Returns the (possibly updated) row."""
    if case["status"] == "submitted" or not case.get("genlayer_tx"):
        return submit(case, client)
    tx = case.get("appeal_tx") or case["genlayer_tx"]
    state: TxState | None = client.tx_state(case["genlayer_tx"])
    stamp = {"last_checked_at": _now()}
    if state is None:
        return db.update_case(case["case_id"], stamp)
    if state.recipient and state.recipient.lower() != client.contract.lower():
        return db.update_case(
            case["case_id"],
            {**stamp, "status": "error", "error": f"case tx {tx} targets {state.recipient}"},
        )
    if state.failed_execution:
        return db.update_case(
            case["case_id"], {**stamp, "status": "error", "error": "VerifyJob execution failed"}
        )
    if state.dead:
        return db.update_case(
            case["case_id"],
            {
                **stamp,
                "status": "undetermined",
                "error": f"consensus: {state.status}/{state.result}",
            },
        )
    if state.final:
        v = client.read_verdict(case["case_id"], final=True)
        return db.update_case(
            case["case_id"],
            {
                **stamp,
                **_verdict_fields(v),
                "status": "final",
                "finalized_at": _now(),
                "decided_at": case.get("decided_at") or _now(),
            },
        )
    if state.decided and case["status"] == "adjudicating":
        v = client.read_verdict(case["case_id"], final=False)
        return db.update_case(
            case["case_id"],
            {**stamp, **_verdict_fields(v), "status": "decided", "decided_at": _now()},
        )
    return db.update_case(case["case_id"], stamp)


def _verdict_fields(v: dict) -> dict:
    return {
        "pass": bool(v["pass"]),
        "score": int(v["score"]),
        "reasons": list(v.get("reasons") or []),
    }


def view(case: dict) -> dict[str, Any]:
    """Verdict schema v1 (fixed + versioned; PRD Feature 2, R2)."""
    window_ends = None
    if case["status"] in ("decided", "appealed") and case.get("decided_at"):
        decided = datetime.fromisoformat(str(case["decided_at"]).replace("Z", "+00:00"))
        window_ends = (decided + timedelta(seconds=config()["finality_window_s"])).isoformat()
    return {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "case_id": case["case_id"],
        "status": case["status"],
        "final": case["status"] == "final",
        "pass": case.get("pass"),
        "score": case.get("score"),
        "reasons": case.get("reasons") or [],
        "genlayer_tx": case.get("genlayer_tx"),
        "appeal_tx": case.get("appeal_tx"),
        "network": case.get("network"),
        "contract": case.get("contract_address"),
        "job_id": case.get("job_id"),
        "spec_hash": case["spec_hash"],
        "deliverable_hash": case["deliverable_hash"],
        "usage_root": case["usage_root"],
        "rubric_hash": case["rubric_hash"],
        "private": case.get("private", False),
        "charged_usd": str(case.get("charged_usd") or "0"),
        "decided_at": case.get("decided_at"),
        "finalized_at": case.get("finalized_at"),
        "appeal_window_ends": window_ends,
        "error": case.get("error"),
    }


def changed(before: dict, after: dict) -> bool:
    return (before.get("status"), before.get("pass"), before.get("score")) != (
        after.get("status"),
        after.get("pass"),
        after.get("score"),
    )


async def poll_open_cases() -> int:
    """Scheduler job: advance every open case, and webhook each state change."""
    import asyncio

    from src.services.genlayer_verify import get_client, is_configured
    from src.services.outbound_webhooks import emit

    if not is_configured():
        return 0
    client = get_client()
    n = 0
    for case in await asyncio.to_thread(db.open_cases_due, 15):
        try:
            after = await asyncio.to_thread(refresh, case, client)
        except Exception as e:
            logger.warning("verify poll of %s failed: %s", case["case_id"], e)
            continue
        if changed(case, after):
            n += 1
            await asyncio.to_thread(emit, after["user_id"], "verify.case.updated", view(after))
    return n
