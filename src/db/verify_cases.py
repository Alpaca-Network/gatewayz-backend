"""DB access for verify_cases / verify_key_caps (migration 20261005020000).

Reads raise rather than defaulting: a case read that silently returned nothing
would let the poller drop a paid case on the floor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.config.supabase_config import get_supabase_client

_CASES = "verify_cases"
_CAPS = "verify_key_caps"
OPEN_STATUSES = ("submitted", "adjudicating", "decided", "appealed")


def insert_case(row: dict[str, Any]) -> dict:
    r = get_supabase_client().table(_CASES).insert(row).execute()
    if not r.data:
        raise RuntimeError("verify_cases insert returned no row")
    return r.data[0]


def get_case(case_id: str, user_id: int | None = None) -> dict | None:
    q = get_supabase_client().table(_CASES).select("*").eq("case_id", case_id)
    if user_id is not None:
        q = q.eq("user_id", user_id)
    r = q.execute()
    return r.data[0] if r.data else None


def update_case(case_id: str, fields: dict[str, Any]) -> dict:
    fields = {**fields, "updated_at": datetime.now(UTC).isoformat()}
    r = get_supabase_client().table(_CASES).update(fields).eq("case_id", case_id).execute()
    if not r.data:
        raise RuntimeError(f"verify_cases update for {case_id} matched nothing")
    return r.data[0]


def open_cases_due(stale_after_s: int, limit: int = 50) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(seconds=stale_after_s)).isoformat()
    r = (
        get_supabase_client()
        .table(_CASES)
        .select("*")
        .in_("status", list(OPEN_STATUSES))
        .or_(f"last_checked_at.is.null,last_checked_at.lt.{cutoff}")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return r.data or []


def charge_cap(api_key_id: int, amount: Decimal, default_cap: Decimal) -> bool:
    r = (
        get_supabase_client()
        .rpc(
            "charge_verify_cap",
            {
                "p_api_key_id": api_key_id,
                "p_amount": str(amount),
                "p_default_cap": str(default_cap),
            },
        )
        .execute()
    )
    return bool(r.data)


def refund_cap(api_key_id: int, amount: Decimal) -> None:
    get_supabase_client().rpc(
        "refund_verify_cap", {"p_api_key_id": api_key_id, "p_amount": str(amount)}
    ).execute()


def get_cap(api_key_id: int) -> dict | None:
    r = get_supabase_client().table(_CAPS).select("*").eq("api_key_id", api_key_id).execute()
    return r.data[0] if r.data else None


def set_cap(api_key_id: int, cap_usd: Decimal) -> dict:
    r = (
        get_supabase_client()
        .table(_CAPS)
        .upsert({"api_key_id": api_key_id, "cap_usd": str(cap_usd)}, on_conflict="api_key_id")
        .execute()
    )
    return r.data[0]


def set_job_genlayer_tx(job_id: str, tx: str) -> None:
    get_supabase_client().table("inference_jobs").update({"genlayer_tx": tx}).eq(
        "job_id", job_id
    ).execute()
