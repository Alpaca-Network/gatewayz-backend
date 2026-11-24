"""DB access for staking_reward_accruals -- the accrual rows behind the
emission epoch job's staker-credit leg (src/services/emission/epoch.py,
paid through src/services/staking_rewards.py::pay_pending_accrual).

The rate-table WAYZ staking rewards program that also used this table (and
staking_reward_rates) is retired; its readers and writers are removed. The
tables themselves stay in the database (see
supabase/migrations/20260911120000_staking_rewards.sql).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning +
safe-default convention exactly -- callers must treat a lookup failure as
"no data," never as a hard failure.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_ACCRUALS_TABLE = "staking_reward_accruals"


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
    rate_id: int | None,
    credits: str,
    status: str,
    user_id: int | None = None,
    skip_reason: str | None = None,
    source: str = "rate_table",
    wayz_amount_wei: str | None = None,
) -> dict[str, Any] | None:
    """Insert one new accrual row. Returns the created row, or None on any
    failure -- including the (wallet_address, reward_date) UNIQUE conflict.
    Callers (the staking-rewards job) call get_accrual() first and only
    reach this when no row exists yet, so a conflict here means a
    concurrent run raced it; the caller re-reads via get_accrual() rather
    than treating None as a hard failure.

    source/wayz_amount_wei/a nullable rate_id (Chutes-style WAYZ emission
    rewards, gatewayz-backend tokenomics) back the emission-mode staker
    path (src/services/emission/epoch.py) -- an emission accrual isn't
    priced off staking_reward_rates at all, so it has no rate_id, and
    wayz_amount_wei records the WAYZ amount its credits were converted
    from. 'rate_table' callers (this module's own job) are unaffected --
    source defaults to 'rate_table' and wayz_amount_wei to None, exactly
    the previous insert payload."""
    payload: dict[str, Any] = {
        "wallet_address": wallet_address.lower(),
        "user_id": user_id,
        "reward_date": reward_date,
        "staked_amount_wei": staked_amount_wei,
        "rate_id": rate_id,
        "credits": credits,
        "status": status,
        "skip_reason": skip_reason,
        "source": source,
    }
    if wayz_amount_wei is not None:
        payload["wayz_amount_wei"] = wayz_amount_wei
    try:
        client = get_supabase_client()
        result = client.table(_ACCRUALS_TABLE).insert(payload).execute()
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
