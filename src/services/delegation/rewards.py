"""The daily delegated-staking accrual: an inference allowance, paid in
credits, for stake delegated to the Gatewayz StakeWise vault / Cardano pool.

The economics: the stake's rewards reach us through the vault fee / pool
margin. If R is that revenue per USD staked per day and m our inference
margin, a credit costs us (1 - m), so granting A = R / (1 - m) credits per
USD per day spends exactly the revenue. The rate actually paid is the one an
admin set in delegation_allowance_rates -- per asset, changeable at any time,
published as the current rate, never a promised return.

The payout half is holdings rewards' (src/services/holdings/rewards.py),
reused rather than reinvented:

* **Basis = the day's LOWEST measurement**, and a day needs at least
  DELEGATION_MIN_MEASUREMENTS_PER_DAY measurements (clamped to the sweeps per
  day) or it is skipped -- lowest-of-day with one reading is just a moment.
* **Pending before pay.** The accrual row is written 'pending' before any
  credit moves, then add_credits_to_user(request_id=
  "delegation:{asset}:{wallet}:{date}"), then marked paid. Idempotent twice
  over: UNIQUE (wallet_address, asset, reward_date), and the credit ledger's
  unique request_id.
* **Two ceilings in code**: DELEGATION_DAILY_CAP_CREDITS per account per day
  (all its wallets and assets together) and DELEGATION_GLOBAL_DAILY_BUDGET_
  CREDITS per day across everyone. Budget order rotates daily
  (holdings' budget_order); accruals already decided for the date consume
  budget first, so a re-run cannot grant a second budget.
* **Fail closed per asset.** An asset whose accruals reconciliation paused
  (delegation_controls) gets no new accrual and no pending payout until an
  admin resumes it; an unreadable controls table pauses everything.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import TransactionType
from src.db.delegation import (
    ASSETS,
    create_accrual,
    get_accrual,
    get_active_rates,
    get_controls,
    get_measurements_for_date,
    list_accruals_for_date,
    list_accruals_for_wallet_date,
    list_measured_pairs_for_date,
    list_pending_accruals,
    list_pending_accruals_since,
    mark_accrual_paid,
)
from src.db.user_wallets import get_wallet, get_wallets_for_user
from src.db.users import add_credits_to_user
from src.services.delegation.measurements import ada_configured, eth_configured
from src.services.holdings.rewards import budget_order

logger = logging.getLogger(__name__)

_CREDITS_DP = Decimal("0.000001")
_PENDING_RETRY_WINDOW_DAYS = 30


class DelegationMeasurementsMissingError(Exception):
    """No wallet has a single measurement for the reward date -- the sweep
    did not run. Turned into a failed job run / a 409, never a crash."""


def _today() -> date:
    return datetime.now(UTC).date()


def _day_str(value: date | str) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_CREDITS_DP, rounding=ROUND_DOWN)


def _decimal(value: Any, default: Decimal = Decimal(0)) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def request_id_for(asset: str, wallet_address: str, reward_date: date | str) -> str:
    return f"delegation:{asset}:{wallet_address.lower()}:{_day_str(reward_date)}"


def daily_cap() -> Decimal:
    return _quantize(_decimal(Config.DELEGATION_DAILY_CAP_CREDITS))


def required_measurements() -> int:
    per_day = max(1, min(int(Config.DELEGATION_MEASUREMENTS_PER_DAY or 1), 24))
    return max(1, min(int(Config.DELEGATION_MIN_MEASUREMENTS_PER_DAY or 1), per_day))


def asset_configured(asset: str) -> bool:
    return {"eth": eth_configured, "ada": ada_configured}[asset]()


def paused_assets(controls: dict[str, dict[str, Any]] | None) -> set[str]:
    """Assets whose new accruals and pending payouts are blocked. An
    unreadable controls table blocks every asset."""
    if controls is None:
        return set(ASSETS)
    return {a for a in ASSETS if (controls.get(a) or {}).get("accruals_paused")}


def rate_for(rates: dict[str, dict[str, Any]], asset: str) -> Decimal:
    row = rates.get(asset)
    return _decimal(row.get("credits_per_1k_usd_per_day")) if row else Decimal(0)


def credits_for(usd_basis: Decimal, rate: Decimal) -> Decimal:
    return _quantize((usd_basis / Decimal(1000)) * rate)


def suggested_rate(expected_daily_revenue_per_usd: Decimal) -> Decimal:
    """credits per 1000 USD per day that spends exactly the expected revenue:
    1000 x R / (1 - m). Shown to admins; never applied automatically."""
    margin = _decimal(Config.DELEGATION_INFERENCE_MARGIN)
    if expected_daily_revenue_per_usd <= 0 or margin >= 1:
        return Decimal(0)
    return _quantize(Decimal(1000) * expected_daily_revenue_per_usd / (Decimal(1) - margin))


def _resolve_user_id(wallet_address: str) -> int | None:
    row = get_wallet(wallet_address)
    if row is None or row.get("is_active") is False:
        return None
    return row.get("user_id")


def _account_headroom(
    user_id: int, reward_date_str: str, wallet: str, asset: str
) -> Decimal | None:
    """What the account may still earn on the date, after every accrual its
    wallets already hold for it (any asset) except this (wallet, asset).
    None when any lookup failed -- the caller skips rather than guess."""
    spent = Decimal(0)
    for row in get_wallets_for_user(user_id):
        address = str(row.get("wallet_address") or "").lower()
        if not address:
            continue
        accruals = list_accruals_for_wallet_date(address, reward_date_str)
        if accruals is None:
            return None
        for accrual in accruals:
            if address == wallet and accrual.get("asset") == asset:
                continue
            if accrual.get("status") in ("pending", "paid"):
                spent += _decimal(accrual.get("credits"))
    return daily_cap() - spent


def _pay_or_leave_pending(
    accrual: dict[str, Any], user_id: int | None = None
) -> tuple[str, Decimal]:
    """Pay one pending accrual if its wallet belongs to an account, else
    leave it pending. Never raises: a failed credit write leaves the row
    pending, which is exactly the retryable state."""
    wallet = str(accrual["wallet_address"]).lower()
    asset = str(accrual["asset"])
    reward_date_str = _day_str(accrual["reward_date"])
    if user_id is None:
        user_id = _resolve_user_id(wallet)
    if user_id is None:
        return "pending", Decimal(0)
    credits = _decimal(accrual.get("credits"))
    if credits <= 0:
        return "pending", Decimal(0)

    request_id = request_id_for(asset, wallet, reward_date_str)
    try:
        add_credits_to_user(
            user_id=user_id,
            credits=float(credits),
            transaction_type=TransactionType.DELEGATION_REWARD,
            description=f"Delegated staking inference allowance ({asset.upper()}) for "
            f"{reward_date_str}",
            metadata={
                "wallet": wallet,
                "asset": asset,
                "reward_date": reward_date_str,
                "usd_basis": str(accrual.get("usd_basis")),
            },
            request_id=request_id,
        )
    except Exception as e:  # noqa: BLE001 - one failed grant must not sink the run
        logger.warning(
            "delegation_rewards: credit write failed for %s/%s: %s",
            asset,
            reward_date_str,
            type(e).__name__,
        )
        return "pending", Decimal(0)
    mark_accrual_paid(accrual["id"], request_id)
    return "paid", credits


def run_delegation_accruals_once(reward_date: date | None = None) -> dict[str, Any]:
    """One pass of the daily accrual for `reward_date` (default yesterday
    UTC). Idempotent. ``{"skipped": "disabled"}`` while the feature is off."""
    started = datetime.now(UTC)
    if not Config.DELEGATED_STAKING_ENABLED:
        return {"skipped": "disabled"}

    effective = reward_date or (_today() - timedelta(days=1))
    day = effective.isoformat()

    pairs = list_measured_pairs_for_date(effective)
    if not pairs:
        raise DelegationMeasurementsMissingError(f"no delegation measurements for {day}")

    already_decided = list_accruals_for_date(effective)
    if already_decided is None:
        # Budget accounting needs the date's existing accruals; guessing zero
        # could grant a second budget.
        raise RuntimeError(f"could not read existing delegation accruals for {day}")

    paused = paused_assets(get_controls())
    rates = get_active_rates()
    configured = {a: asset_configured(a) for a in ASSETS}
    cap = daily_cap()
    budget = _decimal(Config.DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS)
    required = required_measurements()

    committed = sum(
        (
            _decimal(r.get("credits"))
            for r in already_decided
            if r.get("status") in ("pending", "paid")
        ),
        Decimal(0),
    )
    counts = {"paid": 0, "pending": 0, "already": 0, "errors": 0, "capped": 0}
    skipped = {
        "unconfigured": 0,
        "paused": 0,
        "no_rate": 0,
        "no_measurements": 0,
        "too_few_measurements": 0,
        "cap_lookup_failed": 0,
        "zero_credits": 0,
        "budget_exhausted": 0,
    }
    credits_paid = Decimal(0)
    credits_by_asset = {a: Decimal(0) for a in ASSETS}
    budget_exhausted = False

    for key in budget_order([f"{asset}:{wallet}" for wallet, asset in pairs], day):
        asset, wallet = key.split(":", 1)
        try:
            existing = get_accrual(wallet, asset, day)
            if existing is not None:
                if existing.get("status") != "pending":
                    counts["already"] += 1
                elif asset in paused:
                    skipped["paused"] += 1
                else:
                    outcome, paid = _pay_or_leave_pending(existing)
                    counts[outcome] += 1
                    credits_paid += paid
                continue

            if not configured[asset]:
                skipped["unconfigured"] += 1
                continue
            if asset in paused:
                skipped["paused"] += 1
                continue
            rate = rate_for(rates, asset)
            if rate <= 0:
                skipped["no_rate"] += 1
                continue

            values = get_measurements_for_date(wallet, asset, effective)
            if values is None:
                counts["errors"] += 1
                continue
            if not values:
                skipped["no_measurements"] += 1
                continue
            if len(values) < required:
                skipped["too_few_measurements"] += 1
                continue

            basis = min(values)
            credits = credits_for(basis, rate)
            user_id = _resolve_user_id(wallet)
            ceiling = _account_headroom(user_id, day, wallet, asset) if user_id else cap
            if ceiling is None:
                skipped["cap_lookup_failed"] += 1
                continue
            was_capped = credits > ceiling
            if was_capped:
                credits = _quantize(max(ceiling, Decimal(0)))
            if credits <= 0:
                skipped["zero_credits"] += 1
                continue

            if budget_exhausted or committed + credits > budget:
                # Stop granting once one wallet does not fit, so who gets paid
                # never depends on how much budget happened to be left.
                if not budget_exhausted:
                    budget_exhausted = True
                    logger.warning("delegation_rewards: global daily budget %s exhausted", budget)
                skipped["budget_exhausted"] += 1
                continue

            created = create_accrual(wallet, asset, effective, basis, credits, rate, user_id)
            if created is None:
                created = get_accrual(wallet, asset, day)
                if created is None:
                    counts["errors"] += 1
                    continue
                committed += _decimal(created.get("credits"))
                if created.get("status") != "pending":
                    counts["already"] += 1
                    continue
            else:
                committed += credits
                credits_by_asset[asset] += credits
                counts["capped"] += 1 if was_capped else 0

            outcome, paid = _pay_or_leave_pending(created, user_id=user_id)
            counts[outcome] += 1
            credits_paid += paid
        except Exception as e:  # noqa: BLE001 - one bad wallet must not sink the run
            counts["errors"] += 1
            logger.warning("delegation_rewards: %s accrual failed: %s", asset, type(e).__name__)

    retried_paid, retried_credits = _sweep_pending(effective, paused)
    credits_paid += retried_credits

    return {
        "reward_date": day,
        "pairs": len(pairs),
        **counts,
        "skipped": dict(skipped),
        "skipped_total": sum(skipped.values()),
        "paused_assets": sorted(paused),
        "budget_exhausted": budget_exhausted,
        "required_measurements": required,
        "credits_granted": {a: str(v) for a, v in credits_by_asset.items()},
        "credits_paid": str(credits_paid),
        "retried_paid": retried_paid,
        "duration": (datetime.now(UTC) - started).total_seconds(),
    }


def _sweep_pending(effective: date, paused: set[str]) -> tuple[int, Decimal]:
    """Retry pending accruals from the last 30 days (failed credit writes,
    wallets linked since), skipping paused assets."""
    floor = (effective - timedelta(days=_PENDING_RETRY_WINDOW_DAYS)).isoformat()
    day = effective.isoformat()
    paid_count = 0
    paid_credits = Decimal(0)
    for accrual in list_pending_accruals_since(floor):
        if _day_str(accrual.get("reward_date")) == day or accrual.get("asset") in paused:
            continue
        outcome, credits = _pay_or_leave_pending(accrual)
        if outcome == "paid":
            paid_count += 1
            paid_credits += credits
    return paid_count, paid_credits


def pay_pending_delegation_for_wallet(address: str, user_id: int) -> None:
    """Pay any pending accruals for a wallet that was just linked. Called
    inline from the link request path, so it never raises."""
    if not Config.DELEGATED_STAKING_ENABLED:
        return
    try:
        paused = paused_assets(get_controls())
        for accrual in list_pending_accruals(address):
            if accrual.get("asset") in paused:
                continue
            _pay_or_leave_pending(accrual, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("pay_pending_delegation_for_wallet failed: %s", type(e).__name__)


def estimate_daily_credits(
    positions: list[tuple[str, Decimal]], rates: dict[str, dict[str, Any]]
) -> Decimal:
    """Credits per day the given (asset, usd_value) positions would earn at
    the current rates, capped at the per-account daily cap -- that ceiling
    always applies, so a number above it is not an estimate."""
    total = sum((credits_for(usd, rate_for(rates, asset)) for asset, usd in positions), Decimal(0))
    return min(total, daily_cap())
