"""Daily staking-rewards job (gatewayz-backend staking rewards -- boss's
rule: WAYZ stakers are paid in inference credits; providers who offer
inference are paid in WAYZ, already built via M4 provider earnings +
settlement). See docs/staking/REWARDS.md for the full design.

Idempotent: safe to run any number of times for the same reward_date.
staking_reward_accruals' UNIQUE(wallet_address, reward_date) index is the
backstop, but the primary idempotency check is application-level --
get_accrual() is consulted before ever computing a reward, and:

  * a row already 'paid' or 'skipped' for that date is left untouched
    (nothing to do -- one accrual, one ledger call, forever).
  * a row still 'pending' (a prior credit write failed, or the wallet was
    unlinked at creation time and has since linked) is retried.
  * no row at all means this is the first time this (wallet, date) has
    been processed.

Amounts are Decimal end-to-end; float only appears at the
add_credits_to_user() call boundary, which is USD-float typed.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import TransactionType, get_transaction_by_request_id
from src.db.staking_rewards import (
    create_accrual,
    get_accrual,
    get_active_rates,
    list_pending_accruals,
    mark_accrual_paid,
    mark_accrual_pending_failed,
)
from src.db.user_wallets import get_wallet
from src.db.users import add_credits_to_user
from src.db.wallet_stakes import get_max_last_synced_at, list_wallets_with_stake

logger = logging.getLogger(__name__)

_WEI_PER_WAYZ = Decimal(10) ** 18
_CREDITS_DP = Decimal("0.000001")  # 6 dp, matches credits numeric(18,6)
_PENDING_RETRY_WINDOW_DAYS = 30


class StakingRewardsStaleError(Exception):
    """Raised when the on-chain stake sync is too far behind (or has never
    run) to trust today's stake amounts. Callers (the scheduled job, the
    admin run-now endpoint) turn this into ok=False / a 409, never a crash."""


def wei_to_wayz(staked_amount_wei: str | int) -> Decimal:
    return Decimal(str(staked_amount_wei)) / _WEI_PER_WAYZ


def _select_rate(staked_wayz: Decimal, rates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The active rate with the largest min_stake_wayz <= staked_wayz, or
    None if no tier covers this stake (e.g. rates table misconfigured with
    no zero-floor tier)."""
    eligible = [r for r in rates if Decimal(str(r["min_stake_wayz"])) <= staked_wayz]
    if not eligible:
        return None
    return max(eligible, key=lambda r: Decimal(str(r["min_stake_wayz"])))


def _compute_credits(staked_wayz: Decimal, rate: dict[str, Any]) -> Decimal:
    per_1k = Decimal(str(rate["credits_per_1k_wayz_per_day"]))
    raw = (staked_wayz / Decimal(1000)) * per_1k
    return raw.quantize(_CREDITS_DP, rounding=ROUND_DOWN)


def estimate_daily_credits(staked_amount_wei: str | int, rates: list[dict[str, Any]]) -> Decimal:
    """Uncapped daily credit estimate for a given stake -- used by the
    public wallet-rewards preview and the logged-in user's rewards view.
    The daily cap only applies to what's actually paid, not the estimate."""
    staked_wayz = wei_to_wayz(staked_amount_wei)
    rate = _select_rate(staked_wayz, rates)
    if rate is None:
        return Decimal(0)
    return _compute_credits(staked_wayz, rate)


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


def _find_ledger_id(wallet_address: str, reward_date_str: str) -> int | None:
    row = get_transaction_by_request_id(f"staking_reward:{wallet_address}:{reward_date_str}")
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
            request_id=f"staking_reward:{wallet_address}:{reward_date_str}",
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


def _process_wallet_for_date(
    wallet_address: str,
    reward_date_str: str,
    staked_amount_wei: str,
    rates: list[dict[str, Any]],
) -> tuple[str, bool, Decimal]:
    """Process one wallet for one reward_date. Returns
    (outcome, was_capped, credits_paid) where outcome is
    'paid' | 'pending' | 'skipped'. Never raises -- per-wallet failures
    must not abort the run; the caller wraps this in its own try/except."""
    existing = get_accrual(wallet_address, reward_date_str)
    if existing is not None and existing["status"] != "pending":
        return existing["status"], False, Decimal(0)

    was_capped = False

    if existing is None:
        staked_wayz = wei_to_wayz(staked_amount_wei)
        rate = _select_rate(staked_wayz, rates)
        if rate is None:
            logger.warning(
                "staking_rewards: no active rate covers %s WAYZ (%s)", staked_wayz, wallet_address
            )
            return "skipped", False, Decimal(0)

        credits = _compute_credits(staked_wayz, rate)
        cap = Decimal(str(Config.STAKING_REWARDS_DAILY_CAP_CREDITS))
        if credits > cap:
            credits = cap.quantize(_CREDITS_DP, rounding=ROUND_DOWN)
            was_capped = True

        if credits < Decimal(str(Config.STAKING_REWARDS_MIN_CREDITS)):
            create_accrual(
                wallet_address,
                reward_date_str,
                staked_amount_wei,
                rate["id"],
                str(credits),
                status="skipped",
                skip_reason="below_min",
            )
            return "skipped", False, Decimal(0)

        wallet_row = get_wallet(wallet_address)
        user_id = None
        if wallet_row is not None and wallet_row.get("is_active") is not False:
            user_id = wallet_row["user_id"]

        created = create_accrual(
            wallet_address,
            reward_date_str,
            staked_amount_wei,
            rate["id"],
            str(credits),
            status="pending",
            user_id=user_id,
        )
        if created is None:
            # Lost a race with a concurrent run/insert -- re-read rather
            # than assume anything about what the winner did.
            existing = get_accrual(wallet_address, reward_date_str)
            if existing is None:
                return "skipped", False, Decimal(0)
            if existing["status"] != "pending":
                return existing["status"], False, Decimal(0)
        else:
            existing = created

    if existing.get("user_id") is None:
        return "pending", was_capped, Decimal(0)

    outcome, paid_credits = _pay_or_leave_pending(existing, capped=was_capped)
    return outcome, was_capped, paid_credits


def run_staking_rewards_once(reward_date: date | None = None) -> dict[str, Any]:
    """Run one pass of the staking-rewards job for `reward_date` (default:
    yesterday UTC). Idempotent -- see module docstring. Raises
    StakingRewardsStaleError if the on-chain stake sync is too stale (or
    wallet_stakes is empty) to trust; callers turn that into a failed job
    run / a 409, never a crash.
    """
    started = datetime.now(UTC)

    if not Config.STAKING_REWARDS_ENABLED:
        return {"skipped": "disabled"}

    effective_date = reward_date or (started.date() - timedelta(days=1))
    reward_date_str = effective_date.isoformat()

    wallets = list_wallets_with_stake()
    if not wallets:
        raise StakingRewardsStaleError("stake_sync_stale")

    if _is_sync_stale():
        raise StakingRewardsStaleError("stake_sync_stale")

    rates = get_active_rates()

    counts = {"paid": 0, "pending": 0, "skipped": 0}
    credits_paid = Decimal(0)
    capped = 0

    for wallet in wallets:
        address = wallet["wallet_address"]
        try:
            outcome, was_capped, paid_credits = _process_wallet_for_date(
                address, reward_date_str, wallet["staked_amount"], rates
            )
        except Exception as e:
            logger.warning("staking_rewards: wallet %s failed: %s", address, e)
            outcome, was_capped, paid_credits = "pending", False, Decimal(0)

        counts[outcome] = counts.get(outcome, 0) + 1
        if was_capped:
            capped += 1
        if outcome == "paid":
            credits_paid += paid_credits

    # Retry sweep: pending accruals from earlier runs (bounded 30 days),
    # excluding reward_date_str -- already handled above. Covers a prior
    # failed credit write, or a wallet linked since without the
    # pay_pending_for_wallet hook having fired for some reason.
    min_pending_date = (effective_date - timedelta(days=_PENDING_RETRY_WINDOW_DAYS)).isoformat()
    for accrual in list_pending_accruals(min_pending_date):
        if accrual["reward_date"] == reward_date_str:
            continue
        try:
            outcome, paid_credits = _pay_or_leave_pending(accrual)
        except Exception as e:
            logger.warning(
                "staking_rewards: retry failed for accrual id=%s: %s", accrual.get("id"), e
            )
            continue
        if outcome == "paid":
            counts["paid"] += 1
            credits_paid += paid_credits

    duration_seconds = (datetime.now(UTC) - started).total_seconds()

    return {
        "reward_date": reward_date_str,
        "wallets": len(wallets),
        "paid": counts["paid"],
        "pending": counts["pending"],
        "skipped": counts["skipped"],
        "credits_paid": str(credits_paid),
        "capped": capped,
        "duration": duration_seconds,
    }


def pay_pending_for_wallet(address: str, user_id: int) -> None:
    """Pay any pending accruals (last 30 days) for a wallet that just got
    linked to a user. Called inline from link_wallet's callers (a request
    path, not a background job) -- lazy-imported at each call site, and
    must never raise regardless of what goes wrong here."""
    if not Config.STAKING_REWARDS_ENABLED:
        return
    try:
        cutoff = (datetime.now(UTC).date() - timedelta(days=_PENDING_RETRY_WINDOW_DAYS)).isoformat()
        for accrual in list_pending_accruals(cutoff, wallet_address=address):
            row = dict(accrual)
            row["user_id"] = user_id
            try:
                _pay_or_leave_pending(row)
            except Exception as e:
                logger.warning(
                    "pay_pending_for_wallet: failed for accrual id=%s: %s", row.get("id"), e
                )
    except Exception as e:
        logger.warning("pay_pending_for_wallet failed for %s: %s", address, e)
