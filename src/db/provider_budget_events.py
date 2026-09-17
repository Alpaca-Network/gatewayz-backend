#!/usr/bin/env python3
"""Provider budget-exhaustion events (``public.provider_budget_events``).

Durable record of "one of our provider accounts is out of money", aggregated per
(provider, reason). Written from the inference failure path via
``src/services/provider_budget_alerts.py`` (which is what guarantees a failure here can
never reach the user's request), read by ``GET /admin/status``.

See ``supabase/migrations/20260916210000_provider_budget_events.sql`` for why this is a
table rather than in-memory state or a Redis key.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.supabase_config import execute_with_retry

logger = logging.getLogger(__name__)

# Columns this module reads. Verified against the migration above; kept as a constant so
# tests/schema/ style guards and reviewers have one list to check.
_EVENT_COLUMNS = "provider, reason, first_seen_at, last_seen_at, occurrences, sample_model"

_missing_table_warning_logged = False


def _maybe_log_missing_table_hint(error: Exception) -> None:
    """Emit a single actionable warning when the table/function is missing from the
    PostgREST schema cache, so an operator knows to apply migrations rather than
    wondering why the block is empty."""
    global _missing_table_warning_logged

    if _missing_table_warning_logged:
        return

    message = str(error)
    if (
        "provider_budget_events" in message
        or "record_provider_budget_event" in message
        or "PGRST205" in message
        or "PGRST202" in message
    ):
        logger.warning(
            "provider_budget_events is unavailable in Supabase (likely migrations not "
            "applied or schema cache stale). Apply migration "
            "20260916210000_provider_budget_events.sql, then run "
            "NOTIFY pgrst, 'reload schema'; to refresh PostgREST."
        )
        _missing_table_warning_logged = True


class ProviderBudgetEventsUnavailable(RuntimeError):
    """A provider_budget_events read failed (missing table, bad query, outage).

    Read paths raise this instead of returning ``[]``. "No provider is out of credit" and
    "the query that would tell you is broken" must not render identically -- that is the
    exact failure this whole feature exists to stop repeating one level up.
    """


def record_budget_event(
    provider: str,
    reason: str,
    sample_model: str | None = None,
    increment: int = 1,
    stale_after_hours: int = 24,
) -> None:
    """Upsert one (provider, reason) row, adding ``increment`` occurrences.

    ``stale_after_hours`` is the reader's recency window. A row untouched for longer than
    that has stopped being reported, so the next occurrence starts a new incident:
    ``first_seen_at`` and ``occurrences`` reset rather than carrying a months-old outage
    into today's. Within the window both are preserved and accumulated, so a process
    restart cannot make a running outage look like it just began.

    Raises on failure. The caller (``provider_budget_alerts._flush``) runs this off the
    request path and is responsible for swallowing and re-queuing.
    """

    def _op(client: Any) -> Any:
        return client.rpc(
            "record_provider_budget_event",
            {
                "p_provider": provider,
                "p_reason": reason,
                "p_model": sample_model,
                "p_increment": int(increment),
                "p_stale_after_hours": int(stale_after_hours),
            },
        ).execute()

    try:
        execute_with_retry(_op, operation_name="record provider budget event")
    except Exception as e:
        _maybe_log_missing_table_hint(e)
        raise


def list_recent_budget_events(within_hours: int = 24) -> list[dict[str, Any]]:
    """Every (provider, reason) seen within the window, most recent first.

    Rows are never deleted -- the history is the useful part -- so recency is a read-time
    filter, not a retention policy.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=within_hours)).isoformat()

    def _op(client: Any) -> Any:
        return (
            client.table("provider_budget_events")
            .select(_EVENT_COLUMNS)
            .gte("last_seen_at", cutoff)
            .order("last_seen_at", desc=True)
            .execute()
        )

    try:
        result = execute_with_retry(_op, operation_name="list provider budget events")
    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error("Error listing provider budget events: %s", e, exc_info=True)
        raise ProviderBudgetEventsUnavailable("Failed listing provider budget events") from e

    return list(result.data or [])
