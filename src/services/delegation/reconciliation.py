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

Then, per asset: cost = credits granted (pending + paid) x (1 - margin), and
if ``cost > revenue x (1 + DELEGATION_RECONCILIATION_TOLERANCE) +
DELEGATION_RECONCILIATION_GRACE_USD`` the asset's new accruals are PAUSED
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
    sum_granted_credits,
    sum_revenue_usd,
)
from src.services.delegation import alerts, koios, stakewise
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
    row = insert_revenue(
        "eth", today, today.isoformat(), native, native * price, source, raw_amount=shares
    )
    return {
        "recorded": row is not None,
        "baseline": previous_shares is None,
        "revenue_eth": str(native),
        "revenue_usd": str(native * price),
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
        if insert_revenue("ada", today, key, ada, ada * price, "koios:pool_history.pool_fees"):
            recorded += 1
            total_ada += ada
    return {"recorded_epochs": recorded, "revenue_ada": str(total_ada), "tip_epoch": tip}


def compare_asset(asset: str) -> dict[str, Any]:
    """Cost of credits granted vs revenue recorded, for one asset."""
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
    controls = get_controls()
    view: dict[str, Any] = {"enabled": bool(Config.DELEGATED_STAKING_ENABLED)}
    for asset in ASSETS:
        control = (controls or {}).get(asset) or {}
        view[asset] = {
            "configured": {"eth": eth_configured, "ada": ada_configured}[asset](),
            "paused": True if controls is None else bool(control.get("accruals_paused")),
            "paused_reason": control.get("paused_reason"),
            "paused_at": control.get("paused_at"),
            **compare_asset(asset),
        }
    return view
