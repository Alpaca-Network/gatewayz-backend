"""DB access for staking_reward_rates and staking_reward_accruals
(gatewayz-backend staking rewards -- boss's rule: WAYZ stakers are paid in
inference credits; see docs/staking/REWARDS.md and
supabase/migrations/20260911120000_staking_rewards.sql).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning +
safe-default convention exactly -- callers (the daily job in
src/services/staking_rewards.py, and the /staking/rewards and
/admin/staking/* routes) must treat a lookup failure as "no data," never
as a hard failure.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_RATES_TABLE = "staking_reward_rates"
_ACCRUALS_TABLE = "staking_reward_accruals"

# Row cap for summary/history reads -- same convention as
# src/db/wallet_stakes.py's _STAKE_TOTALS_ROW_CAP: fine for testnet scale,
# a real limit rather than an unbounded select.
_ROW_CAP = 10000


def get_active_rates() -> list[dict[str, Any]]:
    """Active rate tiers, ascending by min_stake_wayz. Empty list on any
    lookup error (or in the pathological case that none are active, which
    shouldn't happen after the seed migration)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_RATES_TABLE)
            .select("*")
            .eq("active", True)
            .order("min_stake_wayz", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"staking_reward_rates lookup failed: {e}")
        return []


def replace_active_rates(rates: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Deactivate every currently-active rate row and insert `rates` as the
    new active set.

    Not transactional (an UPDATE then an INSERT, two round-trips) -- this is
    an infrequent, superadmin-only admin action rather than a hot path. A
    failure between the two steps leaves zero active rates; the caller
    should surface that as a 500 (regenerating the rate list is itself
    idempotent, so a retry recovers cleanly). Existing rate rows are never
    updated in place -- a rate already referenced by a paid accrual
    (staking_reward_accruals.rate_id FK) must never change out from under
    it, so a rate change always means "deactivate old, insert new."

    Returns the newly-inserted rows, or None on any failure.
    """
    try:
        client = get_supabase_client()
        client.table(_RATES_TABLE).update({"active": False}).eq("active", True).execute()
        rows = [
            {
                "min_stake_wayz": rate["min_stake_wayz"],
                "credits_per_1k_wayz_per_day": rate["credits_per_1k_wayz_per_day"],
                "note": rate.get("note"),
                "active": True,
            }
            for rate in rates
        ]
        result = client.table(_RATES_TABLE).insert(rows).execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"staking_reward_rates replace failed: {e}")
        return None


def get_accrual(wallet_address: str, reward_date: str) -> dict[str, Any] | None:
    """The accrual row for one (wallet_address, reward_date), or None if it
    doesn't exist yet (or on error). The caller uses this to decide whether
    a reward has already been decided for this day -- the core of the
    staking-rewards job's idempotency."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("reward_date", reward_date)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(
            f"staking_reward_accruals lookup failed for {wallet_address}/{reward_date}: {e}"
        )
        return None


def create_accrual(
    wallet_address: str,
    reward_date: str,
    staked_amount_wei: str,
    rate_id: int,
    credits: str,
    status: str,
    user_id: int | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any] | None:
    """Insert one new accrual row. Returns the created row, or None on any
    failure -- including the (wallet_address, reward_date) UNIQUE conflict.
    Callers (the staking-rewards job) call get_accrual() first and only
    reach this when no row exists yet, so a conflict here means a
    concurrent run raced it; the caller re-reads via get_accrual() rather
    than treating None as a hard failure."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .insert(
                {
                    "wallet_address": wallet_address.lower(),
                    "user_id": user_id,
                    "reward_date": reward_date,
                    "staked_amount_wei": staked_amount_wei,
                    "rate_id": rate_id,
                    "credits": credits,
                    "status": status,
                    "skip_reason": skip_reason,
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(
            f"staking_reward_accruals insert failed for {wallet_address}/{reward_date}: {e}"
        )
        return None


def mark_accrual_paid(
    accrual_id: int,
    user_id: int,
    credit_transaction_id: int | None,
    paid_at: str,
) -> bool:
    """Mark an accrual paid, stamping the definitive user_id (a row created
    while the wallet was still unlinked has user_id=null until this call)."""
    try:
        client = get_supabase_client()
        client.table(_ACCRUALS_TABLE).update(
            {
                "status": "paid",
                "user_id": user_id,
                "credit_transaction_id": credit_transaction_id,
                "paid_at": paid_at,
                "skip_reason": None,
            }
        ).eq("id", accrual_id).execute()
        return True
    except Exception as e:
        logger.warning(f"staking_reward_accruals mark_paid failed for id={accrual_id}: {e}")
        return False


def mark_accrual_pending_failed(accrual_id: int, skip_reason: str) -> bool:
    """Leave a row 'pending' after a failed credit write, recording why (an
    error class name) so a stuck retry is visible on the row itself, not
    just in logs. The next run's retry sweep picks it back up."""
    try:
        client = get_supabase_client()
        client.table(_ACCRUALS_TABLE).update({"skip_reason": skip_reason}).eq(
            "id", accrual_id
        ).execute()
        return True
    except Exception as e:
        logger.warning(
            f"staking_reward_accruals mark_pending_failed failed for id={accrual_id}: {e}"
        )
        return False


def mark_accrual_skipped(accrual_id: int, skip_reason: str) -> bool:
    try:
        client = get_supabase_client()
        client.table(_ACCRUALS_TABLE).update({"status": "skipped", "skip_reason": skip_reason}).eq(
            "id", accrual_id
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"staking_reward_accruals mark_skipped failed for id={accrual_id}: {e}")
        return False


def list_pending_accruals(
    min_reward_date: str, wallet_address: str | None = None
) -> list[dict[str, Any]]:
    """Pending accruals on/after `min_reward_date` (bounds the retry window
    -- 30 days per spec), optionally scoped to one wallet. Empty list on
    any lookup error."""
    try:
        client = get_supabase_client()
        query = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("status", "pending")
            .gte("reward_date", min_reward_date)
        )
        if wallet_address is not None:
            query = query.eq("wallet_address", wallet_address.lower())
        result = query.limit(_ROW_CAP).execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"staking_reward_accruals pending lookup failed: {e}")
        return []


def get_accruals_for_user(user_id: int) -> list[dict[str, Any]]:
    """Every accrual row tied to one user (any status), newest reward_date
    first, row-capped. Backs GET /staking/rewards' totals + history. Empty
    list on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .order("reward_date", desc=True)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"staking_reward_accruals user lookup failed for user {user_id}: {e}")
        return []


def get_all_accruals_since(min_reward_date: str | None = None) -> list[dict[str, Any]]:
    """Every accrual row (any status, any user), optionally bounded to
    reward_date >= min_reward_date, newest first, row-capped. Backs the
    admin rewards summary's global totals. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        query = client.table(_ACCRUALS_TABLE).select("*")
        if min_reward_date is not None:
            query = query.gte("reward_date", min_reward_date)
        result = query.order("reward_date", desc=True).limit(_ROW_CAP).execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"staking_reward_accruals global lookup failed: {e}")
        return []
