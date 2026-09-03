"""Retention cleanup for high-churn tables outside the inference/billing path.

Threat model L11 (docs/security/ANONYMITY_THREAT_MODEL.md): usage_records and
activity_log grow unbounded — they have no natural retention pressure, unlike
chat_completion_requests, which already has a pg_cron rollup+delete job (see
supabase/migrations/20260525010000_fix_ttl_cleanup_jobs.sql). credit_transactions
is the financial audit ledger and is intentionally never pruned here. See
docs/security/DATA_RETENTION.md for every table's window.
"""

import logging
from datetime import UTC, datetime, timedelta

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def _delete_older_than(
    table: str,
    timestamp_column: str,
    cutoff_iso: str,
    batch_size: int,
    max_batches: int,
) -> int:
    """Delete rows from `table` where `timestamp_column` < cutoff, in batches.

    PostgREST's DELETE does not support LIMIT (postgrest-py's delete() filter
    builder has no .limit()/.order() — verified against the installed
    postgrest 0.19.3: only select() builders expose those), so each batch
    first SELECTs a page of ids older than cutoff, then deletes exactly those
    ids. Stops early once a page returns fewer than batch_size rows (nothing
    older than cutoff remains) or max_batches is reached — whichever comes
    first — so one run can't lock up the table indefinitely on a large backlog.
    """
    client = get_supabase_client()
    total_deleted = 0

    for _ in range(max_batches):
        page = (
            client.table(table)
            .select("id")
            .lt(timestamp_column, cutoff_iso)
            .limit(batch_size)
            .execute()
        )
        ids = [row["id"] for row in (page.data or [])]
        if not ids:
            break

        client.table(table).delete().in_("id", ids).execute()
        total_deleted += len(ids)

        if len(ids) < batch_size:
            break

    return total_deleted


def cleanup_usage_records(
    retention_days: int, batch_size: int = 5000, max_batches: int = 20
) -> int:
    """Delete usage_records rows older than retention_days. Never raises —
    retention is a background job and a DB error must not crash the scheduler.

    Returns the number of rows deleted (0 on failure).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    try:
        deleted = _delete_older_than("usage_records", "timestamp", cutoff, batch_size, max_batches)
        logger.info(
            "Retention cleanup: usage_records deleted=%s (older than %sd)",
            deleted,
            retention_days,
        )
        return deleted
    except Exception as e:
        logger.warning("Retention cleanup failed for usage_records (non-fatal): %s", e)
        return 0


def cleanup_activity_log(retention_days: int, batch_size: int = 5000, max_batches: int = 20) -> int:
    """Delete activity_log rows older than retention_days. Never raises —
    retention is a background job and a DB error must not crash the scheduler.

    Returns the number of rows deleted (0 on failure).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    try:
        deleted = _delete_older_than("activity_log", "timestamp", cutoff, batch_size, max_batches)
        logger.info(
            "Retention cleanup: activity_log deleted=%s (older than %sd)",
            deleted,
            retention_days,
        )
        return deleted
    except Exception as e:
        logger.warning("Retention cleanup failed for activity_log (non-fatal): %s", e)
        return 0
