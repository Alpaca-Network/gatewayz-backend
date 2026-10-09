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
  uuid5("delegation:{asset}:{wallet}:{date}")), then marked paid.
  Idempotent twice over: UNIQUE (wallet_address, asset, reward_date), and
  the credit ledger's unique (UUID) request_id. The account cap is checked
  again at payment time, so pending rows paid on link cannot stack.
* **Two ceilings, enforced atomically in SQL**: DELEGATION_DAILY_CAP_CREDITS
  per account per day (all its wallets and assets together, attributed by
  decision-time account, paid account and currently linked wallets) and
  DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS per day across every account and
  asset. delegation_reserve_accrual (insert) and delegation_claim_accrual
  (pay) check and write under one per-date advisory lock, so two runs, or a
  run and a pay-on-link, can never both see "under cap" -- there is no
  read-then-write in Python. Budget order rotates daily (holdings'
  budget_order); everything already reserved for the date counts first.
* **Fail closed per asset.** An asset whose accruals reconciliation paused
  (delegation_controls) gets no new accrual and no pending payout until an
  admin resumes it; an unreadable controls table pauses everything.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import TransactionType
from src.db.delegation import (
    ASSETS,
    claim_accrual,
    get_accrual,
    get_active_rates,
    get_controls,
    get_measurements_for_date,
    list_measured_pairs_for_date,
    list_pending_accruals,
    list_pending_accruals_since,
    mark_accrual_paid,
    release_claim,
    reserve_accrual,
)
from src.db.user_wallets import get_wallet
from src.db.users import add_credits_to_user
from src.services.delegation.measurements import ada_configured, eth_configured
from src.services.holdings.rewards import budget_order

logger = logging.getLogger(__name__)

_CREDITS_DP = Decimal("0.000001")
_PENDING_RETRY_WINDOW_DAYS = 30
# credit_transactions.request_id (and atomic_add_credits.p_request_id) is a
# UUID column, so the logical grant key is mapped to a deterministic UUIDv5 --
# the same pattern as src/services/billing/payments.py. A non-UUID string
# would be rejected by both the atomic RPC and the ledger insert, silently
# losing the ledger-side idempotency guard. Never change this namespace, or
# existing keys shift.
_LEDGER_KEY_NAMESPACE = uuid.UUID("2b0f4a4e-6f1d-5c1e-9a57-5d3c8e0a7d21")


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


def grant_key_for(asset: str, wallet_address: str, reward_date: date | str) -> str:
    """The human-readable logical grant key, kept in the ledger metadata."""
    return f"delegation:{asset}:{wallet_address.lower()}:{_day_str(reward_date)}"


def request_id_for(asset: str, wallet_address: str, reward_date: date | str) -> str:
    """The ledger idempotency key: a deterministic UUIDv5 of the grant key."""
    return str(uuid.uuid5(_LEDGER_KEY_NAMESPACE, grant_key_for(asset, wallet_address, reward_date)))


def daily_cap() -> Decimal:
    return _quantize(_decimal(Config.DELEGATION_DAILY_CAP_CREDITS))


def required_measurements() -> int:
    per_day = max(1, min(int(Config.DELEGATION_MEASUREMENTS_PER_DAY or 1), 24))
    return max(1, min(int(Config.DELEGATION_MIN_MEASUREMENTS_PER_DAY or 1), per_day))


def asset_configured(asset: str) -> bool:
    return {"eth": eth_configured, "ada": ada_configured}[asset]()


def paused_assets(controls: dict[str, dict[str, Any]] | None) -> set[str]:
    """Assets whose new accruals and pending payouts are blocked. Fails
    closed: an unreadable controls table, a missing row for an asset, or a
    row without an explicit ``accruals_paused = false`` all count as paused.
    (The SQL reserve/claim functions apply the same rule themselves.)"""
    if not isinstance(controls, dict):
        return set(ASSETS)
    return {a for a in ASSETS if (controls.get(a) or {}).get("accruals_paused") is not False}


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
    """The account the wallet is linked to right now, or None when it is
    unlinked, inactive, or the lookup failed in any way -- callers never pay
    without an account."""
    try:
        row = get_wallet(wallet_address)
    except Exception:  # noqa: BLE001 - a failed lookup is "no account"
        return None
    if not isinstance(row, dict) or row.get("is_active") is False:
        return None
    user_id = row.get("user_id")
    return int(user_id) if isinstance(user_id, int) and not isinstance(user_id, bool) else None


def _pay_or_leave_pending(
    accrual: dict[str, Any], user_id: int | None = None
) -> tuple[str, Decimal]:
    """Pay one accrual. Returns (outcome, credits_paid); outcome is one of
    paid | pending | void | paused | already. Never raises.

    A 'pending' row is first CLAIMED for the wallet's current account by
    delegation_claim_accrual, which -- atomically, under the reward date's
    advisory lock -- re-checks the asset pause and the account's daily cap
    (claimed + paid rows only) and voids a row that no longer fits. Only then
    do credits move, to the claimed account, with the deterministic UUID
    request_id, and the row is marked paid. A row already 'claimed' (a crash
    between claim and pay) is paid to its claimed account; the ledger's
    request_id makes that retry idempotent.
    """
    status = accrual.get("status")
    if status == "pending":
        if user_id is None:
            user_id = _resolve_user_id(str(accrual["wallet_address"]).lower())
        if user_id is None:
            return "pending", Decimal(0)
        result = claim_accrual(accrual["id"], user_id, daily_cap())
        if result is None:
            return "pending", Decimal(0)
        claim_status = result.get("status")
        if claim_status == "void":
            logger.info("delegation_rewards: voided an accrual over the account cap")
            return "void", Decimal(0)
        if claim_status == "paused":
            return "paused", Decimal(0)
        if claim_status == "paid":
            return "already", Decimal(0)
        if claim_status == "not_linked":
            # The SQL re-check found the wallet is not linked to this account
            # right now (unlinked or moved since we looked). Never pay.
            return "pending", Decimal(0)
        if claim_status != "claimed" or not result.get("accrual"):
            return "pending", Decimal(0)
        accrual = result["accrual"]
    elif status != "claimed":
        return "already", Decimal(0)

    wallet = str(accrual["wallet_address"]).lower()
    asset = str(accrual["asset"])
    reward_date_str = _day_str(accrual["reward_date"])
    payee = accrual.get("paid_user_id")
    credits = _decimal(accrual.get("credits"))
    if payee is None or credits <= 0:
        return "pending", Decimal(0)
    # Re-verify immediately before the credit write: the claim checked the
    # link under the lock, but an unlink or a move to another account may
    # have landed since. Anything other than "still linked to the payee"
    # -- including a failed lookup -- releases the claim and pays nothing.
    if _resolve_user_id(wallet) != payee:
        release_claim(accrual["id"], payee)
        logger.info("delegation_rewards: payee no longer owns the wallet; claim released")
        return "pending", Decimal(0)

    request_id = request_id_for(asset, wallet, reward_date_str)
    try:
        add_credits_to_user(
            user_id=int(payee),
            credits=float(credits),
            transaction_type=TransactionType.DELEGATION_REWARD,
            description=f"Delegated staking inference allowance ({asset.upper()}) for "
            f"{reward_date_str}",
            metadata={
                "wallet": wallet,
                "asset": asset,
                "reward_date": reward_date_str,
                "usd_basis": str(accrual.get("usd_basis")),
                "grant_key": grant_key_for(asset, wallet, reward_date_str),
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
    UTC). Idempotent. ``{"skipped": "disabled"}`` while the feature is off.

    The cap and budget decision is never made here: each wallet-asset-day is
    RESERVED by delegation_reserve_accrual, which computes the account's spend
    and the date's committed total and inserts the pending row in one
    transaction under the date's advisory lock. Python only decides the
    uncapped amount (lowest-of-day x rate) and skips what obviously cannot
    pay (unconfigured, paused, no rate, too few measurements)."""
    started = datetime.now(UTC)
    if not Config.DELEGATED_STAKING_ENABLED:
        return {"skipped": "disabled"}

    effective = reward_date or (_today() - timedelta(days=1))
    day = effective.isoformat()

    pairs = list_measured_pairs_for_date(effective)
    if not pairs:
        raise DelegationMeasurementsMissingError(f"no delegation measurements for {day}")

    paused = paused_assets(get_controls())
    rates = get_active_rates()
    configured = {a: asset_configured(a) for a in ASSETS}
    cap = daily_cap()
    budget = _decimal(Config.DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS)
    required = required_measurements()

    counts = {
        "paid": 0,
        "pending": 0,
        "void": 0,
        "already": 0,
        "errors": 0,
        "capped": 0,
    }
    skipped = {
        "unconfigured": 0,
        "paused": 0,
        "no_rate": 0,
        "no_measurements": 0,
        "too_few_measurements": 0,
        "over_cap": 0,
        "zero_credits": 0,
        "budget_exhausted": 0,
    }
    credits_paid = Decimal(0)
    credits_granted = {a: Decimal(0) for a in ASSETS}
    budget_exhausted = False

    for key in budget_order([f"{asset}:{wallet}" for wallet, asset in pairs], day):
        asset, wallet = key.split(":", 1)
        try:
            existing = get_accrual(wallet, asset, day)
            if existing is not None:
                outcome, paid = _pay_or_leave_pending(existing)
                if outcome == "paused":
                    skipped["paused"] += 1
                else:
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
            if credits <= 0:
                skipped["zero_credits"] += 1
                continue
            if budget_exhausted:
                # Once one wallet did not fit, stop granting, so who gets paid
                # never depends on how much budget happened to be left.
                skipped["budget_exhausted"] += 1
                continue

            user_id = _resolve_user_id(wallet)
            result = reserve_accrual(
                wallet, asset, effective, basis, credits, rate, user_id, cap, budget
            )
            status = (result or {}).get("status")
            if status == "budget_exhausted":
                budget_exhausted = True
                logger.warning("delegation_rewards: global daily budget %s exhausted", budget)
                skipped["budget_exhausted"] += 1
                continue
            if status in ("over_cap", "paused"):
                skipped[status] += 1
                continue
            if status not in ("created", "exists") or not result.get("accrual"):
                counts["errors"] += 1
                continue
            accrual = result["accrual"]
            if status == "created":
                credits_granted[asset] += _decimal(accrual.get("credits"))
                counts["capped"] += 1 if result.get("capped") else 0

            outcome, paid = _pay_or_leave_pending(accrual, user_id=user_id)
            if outcome == "paused":
                skipped["paused"] += 1
            else:
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
        "credits_granted": {a: str(v) for a, v in credits_granted.items()},
        "credits_paid": str(credits_paid),
        "retried_paid": retried_paid,
        "duration": (datetime.now(UTC) - started).total_seconds(),
    }


def _sweep_pending(effective: date, paused: set[str]) -> tuple[int, Decimal]:
    """Retry pending/claimed accruals from the last 30 days (failed credit
    writes, wallets linked since). The pause and cap are re-checked by the
    claim itself; skipping paused assets here only saves the round trip."""
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
    inline from the link request path, so it never raises. Each row goes
    through the atomic claim, so relinking several wallets to one account
    can never pay more than its daily cap."""
    if not Config.DELEGATED_STAKING_ENABLED:
        return
    try:
        for accrual in list_pending_accruals(address):
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
