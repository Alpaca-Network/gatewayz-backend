"""DB access for inference_jobs / inference_job_usage (migration 20261005000000).

Unlike the faucet module's safe-default reads, these raise: a job read that
silently returned "no usage" would seal a wrong Merkle root, which is worse than
a 503.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_JOBS = "inference_jobs"
_USAGE = "inference_job_usage"


def insert_job(row: dict[str, Any]) -> dict:
    result = get_supabase_client().table(_JOBS).insert(row).execute()
    if not result.data:
        raise RuntimeError("inference_jobs insert returned no row")
    return result.data[0]


def get_job(job_id: str, user_id: int) -> dict | None:
    """The job, only if it belongs to user_id (the account that created it)."""
    result = (
        get_supabase_client()
        .table(_JOBS)
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data[0] if result.data else None


def list_usage(job_id: str) -> list[dict]:
    """All usage lines in seq order. Paged so a long job is never truncated."""
    client = get_supabase_client()
    out: list[dict] = []
    page = 1000
    start = 0
    while True:
        result = (
            client.table(_USAGE)
            .select("seq,entry")
            .eq("job_id", job_id)
            .order("seq")
            .range(start, start + page - 1)
            .execute()
        )
        rows = result.data or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    for i, row in enumerate(out):
        if row["seq"] != i:
            raise RuntimeError(f"usage log for {job_id} has a gap at seq {i}")
    return [row["entry"] for row in out]


def append_usage(job_id: str, entry: dict) -> int:
    result = (
        get_supabase_client()
        .rpc(
            "append_job_usage",
            {"p_job_id": job_id, "p_entry": entry, "p_cost": entry["cost_usd"]},
        )
        .execute()
    )
    return int(result.data)


def mark_closed(job_id: str, api_key_id: int | None) -> None:
    """Stop the job taking new lines: retire its key and flip it to closed.

    Done BEFORE reading the log, so the root covers exactly the rows in the table:
    append_job_usage refuses a closed job, and a request still in flight at this
    moment is reported as a lost line instead of drifting from a published root.
    Idempotent: re-closing a closed job is a no-op.
    """
    client = get_supabase_client()
    if api_key_id is not None:
        client.table("api_keys_new").update({"is_active": False}).eq("id", api_key_id).execute()
    client.table(_JOBS).update({"status": "closed", "closed_at": datetime.now(UTC).isoformat()}).eq(
        "job_id", job_id
    ).eq("status", "running").execute()


def store_seal(job_id: str, sealed: dict) -> dict:
    """Write the root once. A second close re-derives the same root from the same
    (now frozen) rows, and the usage_root IS NULL guard keeps the first write."""
    client = get_supabase_client()
    client.table(_JOBS).update(
        {
            "usage_root": sealed["root"],
            "usage_requests": sealed["requests"],
            "usage_tokens_in": sealed["tokens_in"],
            "usage_tokens_out": sealed["tokens_out"],
            "usage_cost_usd": sealed["cost_usd"],
        }
    ).eq("job_id", job_id).is_("usage_root", "null").execute()
    result = client.table(_JOBS).select("*").eq("job_id", job_id).execute()
    if not result.data:
        raise RuntimeError("job vanished while sealing")
    return result.data[0]
