"""The daily reward-share attribution (delegated staking v3): each linked
wallet's share of the staking revenue that actually reached us, granted to
its account as an inference discount.

For every revenue period reconciliation recorded (delegation_revenue -- an
ETH fee-recipient day, a Cardano epoch):

    attributed_usd = revenue_usd x stake / total stake
    discount_usd   = trunc(DELEGATION_REWARD_SHARE x attributed_usd, 6 dp)

with revenue valued at the price when it was recorded, and

* **ETH** -- stake = the wallet's LOWEST measurement (delegation_measurements,
  wei) on the UTC day before the revenue row's date, with at least
  DELEGATION_MIN_MEASUREMENTS_PER_DAY measurements, exactly the v2 accrual's
  basis. The row for day D is the fee-recipient share growth read at
  ~00:30 D, i.e. rewards earned over D-1. Total = vault totalAssets minus the
  fee recipient's own assets at that reading (delegation_revenue.
  stake_total_native) -- or at the previous day's reading if that was larger,
  so a stake that exits just before a reading cannot shrink the denominator
  of the period it earned in.
* **ADA** -- stake = the wallet's ACTIVE stake in the epoch's snapshot (Koios
  ``pool_delegators_history?_epoch_no=``, the stake the epoch's rewards were
  earned on). Total = the pool's ``active_stake`` for the epoch (Koios
  ``pool_history``).

The total is never smaller than the sum of the wallets' stakes, so no wallet
is attributed more than its share; and the SQL reserve caps every period's
attributions at the revenue recorded for it -- the discount granted can never
exceed what we received.

The payout half is v2's (rewards.py), reused:

* **Pending before grant.** Each wallet-asset-period is RESERVED 'pending' by
  delegation_reserve_attribution -- per-account daily cap
  (DELEGATION_DAILY_CAP_CREDITS, in discount USD) and global daily budget
  (DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS) enforced there, under the reward
  date's advisory lock, never read-then-write here.
* **Grant only to a verified, active, linked account** -- _payee_verified, and
  again under the lock inside delegation_grant_attribution, which credits the
  balance (discount_grant, idempotent on request_id =
  uuid5("delegation_share:{asset}:{wallet}:{period_key}")) and marks the row
  granted in ONE transaction. A wallet with no account stays pending and is
  retried for 30 days, and paid on link.
* **Fail closed.** A paused asset (delegation_controls), or an unreadable
  controls table, attributes and grants nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import reward_request_uuid
from src.db.delegation import (
    ASSETS,
    get_attribution,
    get_controls,
    get_measurement_amounts_for_date,
    grant_attribution,
    list_pending_attributions,
    list_revenue_since,
    reserve_attribution,
)
from src.db.user_wallets import is_cardano_wallet, list_all_wallets
from src.services.delegation import koios
from src.services.delegation.discount import payout_model, reward_share
from src.services.delegation.measurements import cardano_pool_id
from src.services.delegation.rewards import (
    _payee_verified,
    _resolve_user_id,
    asset_configured,
    daily_cap,
    paused_assets,
    required_measurements,
)
from src.services.holdings.rewards import budget_order

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
_WEI_PER_ETH = Decimal(10) ** 18
_LOVELACE_PER_ADA = Decimal(10) ** 6
_DISCOUNT_DP = Decimal("0.000001")
_NATIVE_DP = Decimal(1).scaleb(-18)


def _decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def _day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def request_key_for(asset: str, wallet_address: str, period_key: str) -> str:
    return f"delegation_share:{asset}:{wallet_address.lower()}:{period_key}"


def request_id_for(asset: str, wallet_address: str, period_key: str) -> str:
    """The attribution's idempotency key -- a UUID, because the discount
    ledger's request_id is a UUID column."""
    return reward_request_uuid(request_key_for(asset, wallet_address, period_key))


def attribute(
    revenue_usd: Decimal, stake: Decimal, total: Decimal, share: Decimal
) -> tuple[Decimal, Decimal]:
    """(attributed_usd, discount_usd) for one wallet. attributed is the
    wallet's pro-rata share of the revenue (18 dp, rounded down); the discount
    is `share` of that, truncated to 6 dp. Zero for a non-positive input."""
    if revenue_usd <= 0 or stake <= 0 or total <= 0:
        return Decimal(0), Decimal(0)
    attributed = (revenue_usd * min(stake, total) / total).quantize(_NATIVE_DP, rounding=ROUND_DOWN)
    discount = (attributed * min(max(share, Decimal(0)), Decimal(1))).quantize(
        _DISCOUNT_DP, rounding=ROUND_DOWN
    )
    return attributed, discount


# -- stakes per period ----------------------------------------------------------------


class _PeriodUnreadable(Exception):
    """The period's stakes could not be read -- skip it this run; it is
    retried tomorrow (the window is 30 days)."""


def _eth_stakes(row: dict[str, Any], required: int) -> dict[str, Decimal]:
    """{wallet: lowest-of-day stake in ETH} for the day the revenue row
    covers (the day before its revenue_date). Wallets with too few
    measurements that day are left out."""
    day = _day(row["revenue_date"]) - timedelta(days=1)
    amounts = get_measurement_amounts_for_date("eth", day)
    if amounts is None:
        raise _PeriodUnreadable("eth measurements unreadable")
    stakes: dict[str, Decimal] = {}
    for wallet, values in amounts.items():
        if len(values) < required:
            continue
        lowest = min(values)
        if lowest > 0:
            stakes[wallet] = Decimal(lowest) / _WEI_PER_ETH
    return stakes


def _ada_stakes(row: dict[str, Any], pool_id: str, linked: set[str]) -> dict[str, Decimal]:
    """{linked stake address: active stake in ADA} in the epoch's snapshot."""
    try:
        epoch = int(str(row["period_key"]).split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        raise _PeriodUnreadable("unparseable epoch period_key") from None
    try:
        delegators = koios.list_pool_delegators_history(pool_id, epoch)
    except koios.KoiosError as e:
        raise _PeriodUnreadable(str(e)) from None
    stakes: dict[str, Decimal] = {}
    for entry in delegators:
        address = str(entry.get("stake_address") or "").lower()
        if address not in linked:
            continue
        try:
            lovelace = int(str(entry.get("amount") or "0"))
        except (TypeError, ValueError):
            raise _PeriodUnreadable("unparseable delegator amount") from None
        if lovelace > 0:
            stakes[address] = Decimal(lovelace) / _LOVELACE_PER_ADA
    return stakes


def _ada_total_from_koios(pool_id: str, epoch_key: str, cache: dict[str, Any]) -> Decimal | None:
    """The pool's active stake for an epoch, for revenue rows recorded
    before stake_total_native existed. One pool_history read per run."""
    if "history" not in cache:
        try:
            cache["history"] = {
                f"epoch:{int(e.get('epoch_no'))}": e for e in koios.get_pool_history(pool_id)
            }
        except (koios.KoiosError, TypeError, ValueError):
            cache["history"] = None
    history = cache.get("history")
    if not history or epoch_key not in history:
        return None
    try:
        lovelace = int(str(history[epoch_key].get("active_stake")))
    except (TypeError, ValueError):
        return None
    return Decimal(lovelace) / _LOVELACE_PER_ADA if lovelace > 0 else None


def _previous_eth_totals(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    """{period_key: stake_total_native of the reading the day before} for ETH
    revenue rows (oldest first). A missing or unreadable previous reading is
    absent -- the period then divides by its own reading alone."""
    by_date = {_day(r["revenue_date"]): _decimal(r.get("stake_total_native")) for r in rows}
    out: dict[str, Decimal] = {}
    for r in rows:
        prev = by_date.get(_day(r["revenue_date"]) - timedelta(days=1))
        if prev is not None and prev > 0:
            out[str(r.get("period_key"))] = prev
    return out


# -- grant ----------------------------------------------------------------------------


def _grant_or_leave_pending(
    attribution: dict[str, Any], user_id: int | None = None
) -> tuple[str, Decimal]:
    """Grant one pending attribution. Returns (outcome, discount_granted);
    outcome is one of granted | pending | void | paused | already. Never
    raises. Nothing moves unless the wallet is, right now, linked to an
    existing, active account -- checked here and again under the lock."""
    if attribution.get("status") != "pending":
        return "already", Decimal(0)
    wallet = str(attribution["wallet_address"]).lower()
    if user_id is None:
        user_id = _resolve_user_id(wallet)
    if user_id is None or not _payee_verified(wallet, user_id):
        return "pending", Decimal(0)
    result = grant_attribution(attribution["id"], user_id, daily_cap())
    status = (result or {}).get("status")
    if status == "granted":
        granted = (result.get("attribution") or {}).get("discount_usd")
        return "granted", _decimal(granted) or Decimal(0)
    if status == "already":
        return "already", Decimal(0)
    if status == "void":
        logger.info("delegation_attribution: voided an attribution over the account cap")
        return "void", Decimal(0)
    if status == "paused":
        return "paused", Decimal(0)
    # not_linked / inactive_user / grant_failed / not_found / RPC failure.
    return "pending", Decimal(0)


def grant_pending_for_wallet(address: str, user_id: int) -> None:
    """Grant any pending attributions for a wallet that was just linked.
    Called inline from the link request path, so it never raises. Each grant
    goes through the atomic SQL re-check, so linking several wallets to one
    account can never exceed its daily cap."""
    if not Config.DELEGATED_STAKING_ENABLED or payout_model() != "reward_share":
        return
    try:
        for attribution in list_pending_attributions(wallet_address=address):
            _grant_or_leave_pending(attribution, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("grant_pending_for_wallet failed: %s", type(e).__name__)


# -- the job --------------------------------------------------------------------------


def run_delegation_attribution_once(today: date | None = None) -> dict[str, Any]:
    """One pass over the last 30 days of recorded revenue. Idempotent:
    UNIQUE (wallet, asset, period_key) and the ledger's UNIQUE request_id.
    ``{"skipped": "disabled"}`` while DELEGATED_STAKING_ENABLED is off and
    ``{"skipped": "payout_model"}`` in the v2 allowance model."""
    if not Config.DELEGATED_STAKING_ENABLED:
        return {"skipped": "disabled"}
    if payout_model() != "reward_share":
        return {"skipped": "payout_model", "payout_model": payout_model()}
    started = datetime.now(UTC)
    today = today or started.date()
    floor = today - timedelta(days=WINDOW_DAYS)

    paused = paused_assets(get_controls())
    share = reward_share()
    cap = daily_cap()
    budget = _decimal(Config.DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS) or Decimal(0)
    required = required_measurements()

    counts = {"granted": 0, "pending": 0, "void": 0, "already": 0, "errors": 0, "capped": 0}
    skipped = {
        "unconfigured": 0,
        "paused": 0,
        "zero_revenue": 0,
        "no_denominator": 0,
        "unreadable_period": 0,
        "zero_discount": 0,
        "over_cap": 0,
        "budget_exhausted": 0,
        "revenue_exhausted": 0,
        "no_revenue": 0,
    }
    discount_granted = {a: Decimal(0) for a in ASSETS}
    discount_reserved = {a: Decimal(0) for a in ASSETS}
    exhausted_dates: set[str] = set()
    periods = 0
    koios_cache: dict[str, Any] = {}
    linked_cardano: set[str] | None = None

    for asset in ASSETS:
        if not asset_configured(asset):
            skipped["unconfigured"] += 1
            continue
        if asset in paused:
            skipped["paused"] += 1
            continue
        # One extra day so the window's first ETH row has its previous reading.
        rows = list_revenue_since(asset, floor - timedelta(days=1))
        if rows is None:
            counts["errors"] += 1
            continue
        previous_total = _previous_eth_totals(rows) if asset == "eth" else {}
        rows = [r for r in rows if _day(r["revenue_date"]) >= floor]
        pool_id = cardano_pool_id() if asset == "ada" else None
        if asset == "ada" and linked_cardano is None:
            linked_cardano = {
                str(w.get("wallet_address") or "").lower()
                for w in list_all_wallets()
                if is_cardano_wallet(w) and w.get("wallet_address")
            }

        for row in rows:
            revenue_usd = _decimal(row.get("revenue_usd")) or Decimal(0)
            if revenue_usd <= 0:
                skipped["zero_revenue"] += 1
                continue
            period_key = str(row.get("period_key"))
            reward_date = _day(row["revenue_date"]).isoformat()
            periods += 1
            try:
                if asset == "eth":
                    stakes = _eth_stakes(row, required)
                else:
                    stakes = _ada_stakes(row, pool_id or "", linked_cardano or set())
            except _PeriodUnreadable as e:
                skipped["unreadable_period"] += 1
                logger.warning("delegation_attribution: %s %s skipped: %s", asset, period_key, e)
                continue
            if not stakes:
                continue

            total = _decimal(row.get("stake_total_native"))
            if (total is None or total <= 0) and asset == "ada" and pool_id:
                total = _ada_total_from_koios(pool_id, period_key, koios_cache)
            if total is None or total <= 0:
                skipped["no_denominator"] += 1
                continue
            # ETH: the period runs from the previous reading to this one, and a
            # stake that left the vault just before this reading earned in the
            # period yet is missing from its total -- which would shrink the
            # denominator and inflate everyone else's share. Use the larger of
            # the two bracketing readings.
            total = max(total, previous_total.get(period_key, Decimal(0)))
            # Never let a wallet's share exceed its stake's: if the total is
            # still below the sum of the stakes we measured, divide by the larger.
            total = max(total, sum(stakes.values(), Decimal(0)))

            for key in budget_order(list(stakes), f"{reward_date}:{asset}:{period_key}"):
                wallet = key
                try:
                    outcome = _attribute_one(
                        asset,
                        wallet,
                        row,
                        period_key,
                        reward_date,
                        stakes[wallet],
                        total,
                        revenue_usd,
                        share,
                        cap,
                        budget,
                        exhausted_dates,
                    )
                except Exception as e:  # noqa: BLE001 - one wallet must not sink the run
                    counts["errors"] += 1
                    logger.warning("delegation_attribution: %s failed: %s", asset, type(e).__name__)
                    continue
                kind, value, reserved, capped = outcome
                if kind in skipped:
                    skipped[kind] += 1
                else:
                    counts[kind] += 1
                counts["capped"] += 1 if capped else 0
                discount_granted[asset] += value
                discount_reserved[asset] += reserved

    retried = _sweep_pending(floor, paused)

    return {
        "date": today.isoformat(),
        "payout_model": "reward_share",
        "reward_share": str(share),
        "periods": periods,
        **counts,
        "skipped": dict(skipped),
        "skipped_total": sum(skipped.values()),
        "paused_assets": sorted(paused),
        "budget_exhausted_dates": sorted(exhausted_dates),
        "required_measurements": required,
        "discount_reserved": {a: str(v) for a, v in discount_reserved.items()},
        "discount_granted": {a: str(v) for a, v in discount_granted.items()},
        "retried_granted": retried,
        "duration": (datetime.now(UTC) - started).total_seconds(),
    }


def _attribute_one(
    asset: str,
    wallet: str,
    row: dict[str, Any],
    period_key: str,
    reward_date: str,
    stake: Decimal,
    total: Decimal,
    revenue_usd: Decimal,
    share: Decimal,
    cap: Decimal,
    budget: Decimal,
    exhausted_dates: set[str],
) -> tuple[str, Decimal, Decimal, bool]:
    """Reserve and grant one wallet's attribution for one period. Returns
    (outcome, discount_granted, discount_reserved, capped)."""
    existing = get_attribution(wallet, asset, period_key)
    if existing is not None:
        outcome, granted = _grant_or_leave_pending(existing)
        return outcome, granted, Decimal(0), False

    attributed, discount = attribute(revenue_usd, stake, total, share)
    if discount <= 0:
        return "zero_discount", Decimal(0), Decimal(0), False
    if reward_date in exhausted_dates:
        # Once one wallet did not fit the date's budget, stop granting on it,
        # so who gets paid never depends on how much budget happened to be left.
        return "budget_exhausted", Decimal(0), Decimal(0), False

    user_id = _resolve_user_id(wallet)
    result = reserve_attribution(
        wallet,
        asset,
        period_key,
        reward_date,
        user_id,
        stake,
        total,
        attributed,
        share,
        request_id_for(asset, wallet, period_key),
        cap,
        budget,
    )
    status = (result or {}).get("status")
    if status == "budget_exhausted":
        exhausted_dates.add(reward_date)
        logger.warning("delegation_attribution: global daily budget %s exhausted", budget)
        return "budget_exhausted", Decimal(0), Decimal(0), False
    if status in ("over_cap", "paused", "revenue_exhausted", "no_revenue"):
        return str(status), Decimal(0), Decimal(0), False
    if status not in ("created", "exists") or not result.get("attribution"):
        return "errors", Decimal(0), Decimal(0), False
    attribution = result["attribution"]
    reserved = (
        (_decimal(attribution.get("discount_usd")) or Decimal(0))
        if status == "created"
        else Decimal(0)
    )
    outcome, granted = _grant_or_leave_pending(attribution, user_id=user_id)
    return outcome, granted, reserved, bool(result.get("capped"))


def _sweep_pending(floor: date, paused: set[str]) -> int:
    """Retry pending attributions from the window (wallets linked since,
    failed grants). The pause and the cap are re-checked by the grant."""
    granted = 0
    for attribution in list_pending_attributions(min_reward_date=floor):
        if attribution.get("asset") in paused:
            continue
        outcome, _ = _grant_or_leave_pending(attribution)
        granted += 1 if outcome == "granted" else 0
    return granted
