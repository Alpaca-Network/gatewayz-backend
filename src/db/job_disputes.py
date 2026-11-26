"""DB access for job_disputes / job_dispute_lines (migration 20261127090000).

Reads raise rather than defaulting: a dispute read that silently returned nothing
would let the poller drop an open dispute on the floor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.supabase_config import get_supabase_client

_DISPUTES = "job_disputes"
_LINES = "job_dispute_lines"
OPEN_STATUSES = ("submitted", "adjudicating", "decided")


class LinesAlreadyDisputed(Exception):
    """At least one requested line is already part of a live dispute."""


def insert_dispute(row: dict[str, Any]) -> dict:
    """Insert the dispute, then claim its lines. If any line is taken, the dispute
    row is removed again and LinesAlreadyDisputed is raised."""
    client = get_supabase_client()
    r = client.table(_DISPUTES).insert(row).execute()
    if not r.data:
        raise RuntimeError("job_disputes insert returned no row")
    lines = [
        {"job_id": row["job_id"], "line_index": i, "dispute_id": row["dispute_id"]}
        for i in row["line_indexes"]
    ]
    try:
        client.table(_LINES).insert(lines).execute()
    except Exception as e:
        client.table(_DISPUTES).delete().eq("dispute_id", row["dispute_id"]).execute()
        if "duplicate key" in str(e) or "23505" in str(e):
            raise LinesAlreadyDisputed() from e
        raise
    return r.data[0]


def disputed_lines(job_id: str) -> set[int]:
    r = get_supabase_client().table(_LINES).select("line_index").eq("job_id", job_id).execute()
    return {int(x["line_index"]) for x in (r.data or [])}


def release_lines(dispute_id: str) -> None:
    """A failed dispute never marked its lines on chain; let the owner dispute them again."""
    get_supabase_client().table(_LINES).delete().eq("dispute_id", dispute_id).execute()


def get_dispute(dispute_id: str, user_id: int | None = None) -> dict | None:
    q = get_supabase_client().table(_DISPUTES).select("*").eq("dispute_id", dispute_id)
    if user_id is not None:
        q = q.eq("user_id", user_id)
    r = q.execute()
    return r.data[0] if r.data else None


def list_for_job(job_id: str, user_id: int) -> list[dict]:
    r = (
        get_supabase_client()
        .table(_DISPUTES)
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return r.data or []


def update_dispute(dispute_id: str, fields: dict[str, Any]) -> dict:
    fields = {**fields, "updated_at": datetime.now(UTC).isoformat()}
    r = get_supabase_client().table(_DISPUTES).update(fields).eq("dispute_id", dispute_id).execute()
    if not r.data:
        raise RuntimeError(f"job_disputes update for {dispute_id} matched nothing")
    return r.data[0]


def open_disputes_due(stale_after_s: int, limit: int = 50) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(seconds=stale_after_s)).isoformat()
    r = (
        get_supabase_client()
        .table(_DISPUTES)
        .select("*")
        .in_("status", list(OPEN_STATUSES))
        .or_(f"last_checked_at.is.null,last_checked_at.lt.{cutoff}")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return r.data or []
