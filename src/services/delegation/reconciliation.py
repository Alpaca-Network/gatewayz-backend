"""Daily revenue reconciliation for delegated staking -- and the fail-closed
switch.

Credits are only sustainable while the staking revenue that actually reached
us covers what they cost. This job records that revenue and compares:

* **ETH** -- StakeWise mints the vault fee as new vault shares to the vault's
  ``feeRecipient``. Revenue for a day is the growth in the fee recipient's
  shares since the previous reading, valued with ``convertToAssets`` and the
  ETH price. The first reading (or a reading after feeRecipient changed) is a
  zero-revenue baseline: revenue before it is never counted, which can only
  make the comparison stricter. A share balance that fell (the operator
  moved fee shares) records zero, never negative -- same direction.
* **ADA** -- Koios ``pool_history.pool_fees``: the operator's take (fixed
  cost + margin) per epoch, recorded once per epoch and only for epochs at
  least two behind the tip, when the epoch's rewards are final.
  Valued at the ADA price when recorded.

Each revenue row also records the period's total stake (``stake_total_native``)
-- the denominator the v3 reward-share attribution divides by: ETH = vault
``totalAssets`` minus the fee recipient's own assets at the reading; ADA = the
pool's ``active_stake`` for the epoch (Koios ``pool_history``).

Then, per asset, depending on DELEGATION_PAYOUT_MODEL:

* **allowance** (v2): cost = credits granted (pending + paid) x (1 - margin);
  overspent when ``cost > revenue x (1 + DELEGATION_RECONCILIATION_TOLERANCE)
  + DELEGATION_RECONCILIATION_GRACE_USD``.
* **reward_share** (v3, default): committed = discount granted (pending +
  granted attributions, at face value) + any v2 credits' cost; overspent when
  ``committed > reward_share x revenue x (1 + tolerance) + grace``. The
  attribution can never grant more than that by construction; this is the
  safety net. The view also reports the discount's cost to us across assets:
  discount used x (1 - margin) plus the outstanding balance liability.

Overspent -> the asset's new accruals / attributions are PAUSED
(delegation_controls) and ops is alerted. Nothing automatic resumes it -- an
admin does, via POST /admin/delegation/resume. A sum that cannot be read is
not treated as zero: the asset is reported unknown and left as it is.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.config import Config
from src.db.delegation import (
    ASSETS,
    get_controls,
    get_latest_revenue,
    insert_revenue,
    list_revenue_period_keys,
    pause_accruals,
    sum_attributed_discount,
    sum_granted_credits,
    sum_revenue_usd,
)
from src.db.inference_discount import list_balances
from src.services.delegation import alerts, koios, stakewise
from src.services.delegation.discount import payout_model, reward_share
from src.services.delegation.measurements import (
    ada_configured,
    cardano_pool_id,
    eth_configured,
)
from src.services.holdings.prices import get_usd_prices

logger = logging.getLogger(__name__)

_WEI_PER_ETH = Decimal(10) ** 18
_LOVELACE_PER_ADA = Decimal(10) ** 6
# Koios pool_history: an epoch's rewards are final two epochs later.
_EPOCH_FINALITY_LAG = 2


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _price(price_id: str) -> Decimal | None:
    point = get_usd_prices([price_id]).get(price_id)
    return point.price if point is not None and point.price > 0 else None


def record_eth_revenue(today: date) -> dict[str, Any]:
    if not eth_configured():
        return {"skipped": "unconfigured"}
    price = _price("ethereum")
    if price is None:
        return {"skipped": "no_price"}
    info = stakewise.read_vault_info()
    recipient = info.fee_recipient.lower()
    shares = stakewise.read_shares(recipient)
    source = f"stakewise:fee_recipient:{recipient}"

    previous = get_latest_revenue("eth")
    previous_shares: int | None = None
    if previous is not None and previous.get("source") == source:
        parsed = _decimal(previous.get("raw_amount"))
        previous_shares = int(parsed) if parsed is not None and parsed.is_finite() else None

    # No comparable previous reading -> a zero-revenue baseline. `source`
    # names the fee recipient, so a changed recipient starts a new baseline
    # rather than diffing two unrelated balances.
    delta_shares = 0 if previous_shares is None else max(shares - previous_shares, 0)
    wei = stakewise.convert_to_assets(delta_shares) if delta_shares > 0 else 0
    native = Decimal(wei) / _WEI_PER_ETH
    # The attribution denominator: everyone's stake but our own fee shares.
    recipient_wei = stakewise.convert_to_assets(shares) if shares > 0 else 0
    staked_wei = info.total_assets_wei - recipient_wei
    stake_total = Decimal(staked_wei) / _WEI_PER_ETH if staked_wei > 0 else None
    row = insert_revenue(
        "eth",
        today,
        today.isoformat(),
        native,
        native * price,
        source,
        raw_amount=shares,
        stake_total_native=stake_total,
    )
    return {
        "recorded": row is not None,
        "baseline": previous_shares is None,
        "revenue_eth": str(native),
        "revenue_usd": str(native * price),
        "stake_total_eth": None if stake_total is None else str(stake_total),
        "fee_percent_bps": info.fee_percent_bps,
    }


def record_ada_revenue(today: date) -> dict[str, Any]:
    pool_id = cardano_pool_id()
    if pool_id is None:
        return {"skipped": "unconfigured"}
    price = _price("cardano")
    if price is None:
        return {"skipped": "no_price"}
    tip = koios.get_tip_epoch()
    history = koios.get_pool_history(pool_id)
    recorded_keys = list_revenue_period_keys("ada")
    if recorded_keys is None:
        return {"skipped": "lookup_failed"}

    recorded = 0
    total_ada = Decimal(0)
    for entry in history:
        try:
            epoch = int(entry.get("epoch_no"))
            fees = int(str(entry.get("pool_fees") or "0"))
        except (TypeError, ValueError):
            continue
        key = f"epoch:{epoch}"
        if epoch > tip - _EPOCH_FINALITY_LAG or fees <= 0 or key in recorded_keys:
            continue
        ada = Decimal(fees) / _LOVELACE_PER_ADA
        if insert_revenue(
            "ada",
            today,
            key,
            ada,
            ada * price,
            "koios:pool_history.pool_fees",
            stake_total_native=_active_stake_ada(entry),
        ):
            recorded += 1
            total_ada += ada
    return {"recorded_epochs": recorded, "revenue_ada": str(total_ada), "tip_epoch": tip}


def _active_stake_ada(entry: dict[str, Any]) -> Decimal | None:
    """pool_history.active_stake (lovelace, string) in ADA; None when absent
    or not a positive integer."""
    try:
        lovelace = int(str(entry.get("active_stake")))
    except (TypeError, ValueError):
        return None
    return Decimal(lovelace) / _LOVELACE_PER_ADA if lovelace > 0 else None


def compare_asset(asset: str) -> dict[str, Any]:
    """What was granted vs what reached us, for one asset, under the current
    payout model."""
    if payout_model() == "reward_share":
        return _compare_reward_share(asset)
    granted = sum_granted_credits(asset)
    revenue = sum_revenue_usd(asset)
    if granted is None or revenue is None:
        return {"status": "unknown"}
    margin = _decimal(Config.DELEGATION_INFERENCE_MARGIN) or Decimal(0)
    tolerance = _decimal(Config.DELEGATION_RECONCILIATION_TOLERANCE) or Decimal(0)
    grace = _decimal(Config.DELEGATION_RECONCILIATION_GRACE_USD) or Decimal(0)
    cost = granted * (Decimal(1) - margin)
    limit = revenue * (Decimal(1) + tolerance) + grace
    return {
        "status": "overspent" if cost > limit else "ok",
        "credits_granted": str(granted),
        "cost_usd": str(cost),
        "revenue_usd": str(revenue),
        "limit_usd": str(limit),
        "coverage": str(revenue / cost) if cost > 0 else None,
    }


def _compare_reward_share(asset: str) -> dict[str, Any]:
    discount = sum_attributed_discount(asset)
    credits = sum_granted_credits(asset)
    revenue = sum_revenue_usd(asset)
    if discount is None or credits is None or revenue is None:
        return {"status": "unknown"}
    margin = _decimal(Config.DELEGATION_INFERENCE_MARGIN) or Decimal(0)
    tolerance = _decimal(Config.DELEGATION_RECONCILIATION_TOLERANCE) or Decimal(0)
    grace = _decimal(Config.DELEGATION_RECONCILIATION_GRACE_USD) or Decimal(0)
    share = reward_share()
    # Credits a v2 allowance run granted earlier still spent this revenue.
    legacy_cost = credits * (Decimal(1) - margin)
    committed = discount + legacy_cost
    limit = share * revenue * (Decimal(1) + tolerance) + grace
    return {
        "status": "overspent" if committed > limit else "ok",
        "payout_model": "reward_share",
        "discount_granted_usd": str(discount),
        "legacy_credits_cost_usd": str(legacy_cost),
        "committed_usd": str(committed),
        # The pause reason and the ops alert read cost_usd in either model.
        "cost_usd": str(committed),
        "revenue_usd": str(revenue),
        "reward_share": str(share),
        "limit_usd": str(limit),
        "coverage": str(share * revenue / committed) if committed > 0 else None,
    }


def discount_liability() -> dict[str, Any]:
    """The discount's cost to us across assets: used x (1 - margin) plus the
    outstanding balance (a liability until used), from inference_discount_
    balances. {"status": "unknown"} when the balances cannot be read."""
    rows = list_balances()
    if rows is None:
        return {"status": "unknown"}
    used = Decimal(0)
    outstanding = Decimal(0)
    accounts = 0
    for row in rows:
        balance = _decimal(row.get("balance_usd"))
        spent = _decimal(row.get("lifetime_used_usd"))
        if balance is None or spent is None:
            return {"status": "unknown"}
        used += spent
        outstanding += balance
        accounts += 1 if balance > 0 else 0
    margin = _decimal(Config.DELEGATION_INFERENCE_MARGIN) or Decimal(0)
    return {
        "status": "ok",
        "discount_used_usd": str(used),
        "outstanding_balance_usd": str(outstanding),
        "accounts_with_balance": accounts,
        "cost_usd": str(used * (Decimal(1) - margin) + outstanding),
    }


def run_delegation_reconciliation_once(today: date | None = None) -> dict[str, Any]:
    """Record revenue, compare, pause + alert on overspend. Idempotent: a
    revenue period is recorded once (UNIQUE (asset, period_key))."""
    if not Config.DELEGATED_STAKING_ENABLED:
        return {"skipped": "disabled"}
    started = datetime.now(UTC)
    today = today or started.date()
    configured = {"eth": eth_configured(), "ada": ada_configured()}
    recorders = {"eth": record_eth_revenue, "ada": record_ada_revenue}
    controls = get_controls() or {}

    result: dict[str, Any] = {"date": today.isoformat()}
    for asset in ASSETS:
        block: dict[str, Any] = {}
        if not configured[asset]:
            result[asset] = {"skipped": "unconfigured"}
            continue
        try:
            block["revenue"] = recorders[asset](today)
        except Exception as e:  # noqa: BLE001 - VaultReadError/KoiosError are pre-redacted
            reason = str(e) if isinstance(e, stakewise.VaultReadError | koios.KoiosError) else ""
            block["revenue"] = {"error": type(e).__name__}
            logger.warning(
                "delegation %s revenue read failed: %s %s", asset, type(e).__name__, reason
            )
            alerts.alert_revenue_read_failed(asset, type(e).__name__)

        comparison = compare_asset(asset)
        block["comparison"] = comparison
        already_paused = bool((controls.get(asset) or {}).get("accruals_paused"))
        if comparison.get("status") == "overspent":
            if not already_paused:
                reason = (
                    f"cost ${comparison['cost_usd']} exceeds revenue ${comparison['revenue_usd']} "
                    f"(limit ${comparison['limit_usd']})"
                )
                block["paused_now"] = pause_accruals(asset, reason)
            alerts.alert_overspent(asset, comparison)
        block["paused"] = already_paused or bool(block.get("paused_now"))
        result[asset] = block

    result["duration"] = (datetime.now(UTC) - started).total_seconds()
    return result


def reconciliation_view() -> dict[str, Any]:
    """Read-only reconciliation state for the admin API and /admin/status."""
    from src.services.delegation.rewards import paused_assets

    controls = get_controls()
    paused = paused_assets(controls)  # the same fail-closed rule payouts use
    view: dict[str, Any] = {
        "enabled": bool(Config.DELEGATED_STAKING_ENABLED),
        "payout_model": payout_model(),
    }
    for asset in ASSETS:
        control = (controls or {}).get(asset) or {}
        view[asset] = {
            "configured": {"eth": eth_configured, "ada": ada_configured}[asset](),
            "paused": asset in paused,
            "paused_reason": control.get("paused_reason"),
            "paused_at": control.get("paused_at"),
            **compare_asset(asset),
        }
    if payout_model() == "reward_share":
        view["discount"] = discount_liability()
    return view
