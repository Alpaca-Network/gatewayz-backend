"""DB access for emission_epochs, provider_scores, and the trailing-7-day
provider_work window the emission_epoch job scores providers over
(Chutes-style WAYZ emission rewards, gatewayz-backend tokenomics -- boss
asks: split WAYZ rewards between stakers and GPU providers the way Chutes
does; see docs/tokenomics/EMISSION.md and
supabase/migrations/20260913120000_emission_rewards.sql).

Mirrors src/db/gpu_payouts.py's try/except + logger.warning + safe-default
convention exactly -- callers (src/services/emission/epoch.py, the
GET /gpu/providers/me/earnings and GET /admin/emission/* routes) must
treat a lookup failure as "no data," never as a hard failure.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_EPOCHS_TABLE = "emission_epochs"
_SCORES_TABLE = "provider_scores"
_WORK_TABLE = "provider_work"

# Generous but real cap on the trailing-7-day work window a single epoch
# run scans -- same rationale as every other src/db/* row cap in this
# codebase (see src/db/wallet_stakes.py's _STAKE_TOTALS_ROW_CAP).
_WORK_WINDOW_ROW_CAP = 200_000
_EPOCH_LIST_ROW_CAP = 90

_WORK_WINDOW_COLUMNS = (
    "provider_id,node_id,model,prompt_tokens,completion_tokens,latency_ms,attested,created_at"
)


# ---------------------------------------------------------------------------
# emission_epochs
# ---------------------------------------------------------------------------


def get_epoch(epoch_date: str) -> dict[str, Any] | None:
    """The emission_epochs row for one day, or None if it hasn't run yet
    (or on error). Consulted by run_emission_epoch BEFORE any scoring work
    so a re-run of an already-'computed'/'allocated' day is a no-op --
    the epoch_date primary key is the job's idempotency anchor."""
    try:
        client = get_supabase_client()
        result = client.table(_EPOCHS_TABLE).select("*").eq("epoch_date", epoch_date).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"emission_epochs lookup failed for {epoch_date}: {e}")
        return None


def create_epoch(
    epoch_date: str,
    emission_wei: int,
    providers_wei: int,
    stakers_wei: int,
    treasury_wei: int,
    providers_scored: int,
    stakers_paid: int,
    status: str,
    summary: dict[str, Any],
    providers_usd_micros: int | None = None,
) -> dict[str, Any] | None:
    """Insert the emission_epochs row for one day. Returns the created row,
    or None on any failure -- including the epoch_date PK conflict (a
    concurrent run got there first; the caller re-reads via get_epoch()
    rather than treating None as a hard failure, same pattern as
    src/db/staking_rewards.py::create_accrual)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_EPOCHS_TABLE)
            .insert(
                {
                    "epoch_date": epoch_date,
                    "emission_wei": str(emission_wei),
                    "providers_wei": str(providers_wei),
                    "stakers_wei": str(stakers_wei),
                    "treasury_wei": str(treasury_wei),
                    "providers_scored": providers_scored,
                    "stakers_paid": stakers_paid,
                    "status": status,
                    "summary": summary,
                    "providers_usd_micros": providers_usd_micros,
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.warning(f"emission_epochs insert failed for {epoch_date}: {e}")
        return None


def list_epochs(limit: int = _EPOCH_LIST_ROW_CAP) -> list[dict[str, Any]]:
    """Most recent emission_epochs rows, newest first -- backs
    GET /admin/emission/epochs. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_EPOCHS_TABLE)
            .select("*")
            .order("epoch_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"emission_epochs list failed: {e}")
        return []


def get_latest_epoch() -> dict[str, Any] | None:
    """The most recent emission_epochs row (any status), or None if none
    have run yet (or on error) -- backs the `emission.last_epoch` block on
    GET /gpu/public/summary and GET /staking/rewards."""
    rows = list_epochs(limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# provider_scores
# ---------------------------------------------------------------------------


def create_provider_scores(rows: list[dict[str, Any]]) -> bool:
    """Bulk-insert provider_scores for one epoch. Each row's
    UNIQUE(epoch_date, provider_id) index makes a duplicate insert a no-op
    error rather than a double-count -- callers check get_epoch() first
    and skip the whole run for an already-computed epoch_date, so this
    should only ever be hit once per epoch in practice. A failure here is
    logged and returned as False; the caller still has the epoch_date row
    it already wrote (with status reflecting whether scores persisted) as
    the source of truth for what happened."""
    if not rows:
        return True
    try:
        client = get_supabase_client()
        client.table(_SCORES_TABLE).insert(rows).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_scores bulk insert failed for {len(rows)} rows: {e}")
        return False


def list_provider_scores_for_epoch(epoch_date: str) -> list[dict[str, Any]]:
    """Every provider_scores row for one epoch, highest share first --
    backs GET /admin/emission/epochs/{date}. Empty list on any lookup
    error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_SCORES_TABLE)
            .select("*")
            .eq("epoch_date", epoch_date)
            .order("share", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_scores list failed for {epoch_date}: {e}")
        return []


def get_latest_provider_score(provider_id: int) -> dict[str, Any] | None:
    """The most recent provider_scores row for one provider (any epoch), or
    None if it has never been scored (or on error) -- backs
    GET /gpu/providers/me/earnings' `emission` block."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_SCORES_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .order("epoch_date", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"provider_scores latest lookup failed for provider {provider_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# provider_work -- trailing-window scoring input
# ---------------------------------------------------------------------------


def list_verified_work_window(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    """provider_work rows with verification='verified', created_at in
    [start_iso, end_iso) -- the trailing-7-day scoring input for
    src/services/emission/epoch.py. Only the columns scoring needs are
    selected (never prompt_hash/response_hash/billing_ref). Row-capped;
    logs a warning (rather than silently under-scoring) if the cap is hit.
    Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select(_WORK_WINDOW_COLUMNS)
            .eq("verification", "verified")
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .limit(_WORK_WINDOW_ROW_CAP)
            .execute()
        )
        rows = result.data or []
        if len(rows) >= _WORK_WINDOW_ROW_CAP:
            logger.warning(
                f"list_verified_work_window hit the {_WORK_WINDOW_ROW_CAP}-row cap for "
                f"[{start_iso}, {end_iso}); provider scores for this epoch may be incomplete"
            )
        return rows
    except Exception as e:
        logger.warning(f"provider_work emission-window lookup failed: {e}")
        return []
