"""Staker-credit pay path shared with the emission epoch job.

The rate-table WAYZ staking rewards program that used to live here (daily
job, rate tiers, /staking/rewards, /admin/staking/*) is retired and removed.
What remains is exactly what src/services/emission/epoch.py's staker leg
imports: the stake-sync staleness guard and the insert-before-pay step that
turns one 'pending' staking_reward_accruals row into a ledger credit.

Amounts are Decimal end-to-end; float only appears at the
add_credits_to_user() call boundary, which is USD-float typed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import (
    TransactionType,
    get_transaction_by_request_id,
    reward_request_uuid,
)
from src.db.staking_rewards import mark_accrual_paid, mark_accrual_pending_failed
from src.db.user_wallets import get_wallet
from src.db.users import add_credits_to_user
from src.db.wallet_stakes import get_max_last_synced_at

logger = logging.getLogger(__name__)

_WEI_PER_WAYZ = Decimal(10) ** 18


class StakingRewardsStaleError(Exception):
    """Raised when the on-chain stake sync is too far behind (or has never
    run) to trust today's stake amounts. Callers (the scheduled emission
    job, the admin run-now endpoint) turn this into ok=False / a 409, never
    a crash."""


def wei_to_wayz(staked_amount_wei: str | int) -> Decimal:
    return Decimal(str(staked_amount_wei)) / _WEI_PER_WAYZ


def _is_sync_stale() -> bool:
    max_synced_at = get_max_last_synced_at()
    if max_synced_at is None:
        return True
    try:
        synced_dt = datetime.fromisoformat(str(max_synced_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    max_age_minutes = 2 * Config.WAYZ_STAKING_SYNC_INTERVAL_MINUTES + 30
    age_minutes = (datetime.now(UTC) - synced_dt).total_seconds() / 60
    return age_minutes > max_age_minutes


def is_stake_sync_stale() -> bool:
    """Public wrapper over _is_sync_stale() -- the staleness check used by
    the emission_epoch job (src/services/emission/epoch.py)'s staker-payout
    path, which reads wallet_stakes and must not trust it when the on-chain
    sync has fallen behind."""
    return _is_sync_stale()


def _staking_request_id(wallet_address: str, reward_date_str: str) -> str:
    return reward_request_uuid(f"staking_reward:{wallet_address}:{reward_date_str}")


def _find_ledger_id(wallet_address: str, reward_date_str: str) -> int | None:
    row = get_transaction_by_request_id(_staking_request_id(wallet_address, reward_date_str))
    return row.get("id") if row else None


def _pay_or_leave_pending(accrual: dict[str, Any], capped: bool = False) -> tuple[str, Decimal]:
    """Pay one 'pending' accrual if its wallet is linked to an active user;
    otherwise leave it pending. Returns (outcome, credits_paid) where
    outcome is 'paid' or 'pending'. Never raises -- a failed credit write
    is caught, recorded on the row, and reported as 'pending' so the
    caller's per-wallet loop keeps going."""
    wallet_address = accrual["wallet_address"]
    reward_date_str = accrual["reward_date"]
    user_id = accrual.get("user_id")

    if user_id is None:
        wallet_row = get_wallet(wallet_address)
        if wallet_row is None or wallet_row.get("is_active") is False:
            return "pending", Decimal(0)
        user_id = wallet_row["user_id"]

    staked_wayz = wei_to_wayz(accrual["staked_amount_wei"])
    credits = Decimal(str(accrual["credits"]))

    try:
        add_credits_to_user(
            user_id=user_id,
            credits=float(credits),
            transaction_type=TransactionType.STAKING_REWARD,
            description=f"Staking reward for {reward_date_str} ({staked_wayz:.2f} WAYZ)",
            metadata={
                "wallet": wallet_address,
                "reward_date": reward_date_str,
                "staked_amount_wei": accrual["staked_amount_wei"],
                "rate_id": accrual["rate_id"],
                "capped": capped,
            },
            request_id=_staking_request_id(wallet_address, reward_date_str),
        )
    except Exception as e:
        mark_accrual_pending_failed(accrual["id"], type(e).__name__)
        logger.warning(
            "staking_rewards: credit write failed for %s/%s: %s",
            wallet_address,
            reward_date_str,
            e,
        )
        return "pending", Decimal(0)

    credit_transaction_id = _find_ledger_id(wallet_address, reward_date_str)
    mark_accrual_paid(accrual["id"], user_id, credit_transaction_id, datetime.now(UTC).isoformat())
    return "paid", credits


def pay_pending_accrual(accrual: dict[str, Any], capped: bool = False) -> tuple[str, Decimal]:
    """Public wrapper over _pay_or_leave_pending -- the emission-mode staker
    path (src/services/emission/epoch.py) writes a 'pending'
    staking_reward_accruals row first and then pays it through here, so the
    insert-before-pay idempotency lives in one place."""
    return _pay_or_leave_pending(accrual, capped=capped)
