"""The delegated-staking measurement sweep.

Runs DELEGATION_MEASUREMENTS_PER_DAY times a day and records, for every
linked wallet, how much it has delegated to us right now -- the same
semantics as the holdings sweep (src/services/holdings/snapshots.py):

* **A measured zero is recorded.** A wallet we read and found nothing
  delegated gets a zero row, so the day's minimum sees it. Without it, a
  wallet that staked for two sweeps would look like it staked all day.
* **"Could not measure" records nothing.** A failed RPC/Koios read or a
  missing price writes no row at all for the affected wallets, never a zero:
  a zero we did not observe would underpay under lowest-of-day, and the
  accrual's minimum-measurements rule turns a sparse day into no payout
  rather than a payout on a single farmable reading.

ETH: each linked EVM wallet's shares in the StakeWise V3 vault, converted to
ETH with the vault's own convertToAssets. ADA: one Koios pool_delegators read
for the whole pool, matched against linked stake addresses; a delegation only
counts once its ``active_epoch_no`` has been reached (Cardano stake becomes
active ~2 epochs after the delegation certificate).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.config.config import Config
from src.db.delegation import record_measurement
from src.db.user_wallets import is_cardano_wallet, is_evm_wallet, list_all_wallets
from src.services.delegation import koios, stakewise
from src.services.holdings.prices import get_usd_prices

logger = logging.getLogger(__name__)

PRICE_IDS = {"eth": "ethereum", "ada": "cardano"}
_WEI_PER_ETH = Decimal(10) ** 18
_LOVELACE_PER_ADA = Decimal(10) ** 6


def eth_configured() -> bool:
    return stakewise.vault_address() is not None


def cardano_pool_id() -> str | None:
    pool = (Config.CARDANO_POOL_ID or "").strip().lower()
    return pool if pool.startswith("pool1") else None


def ada_configured() -> bool:
    return cardano_pool_id() is not None


def _price(asset: str) -> Decimal | None:
    price_id = PRICE_IDS[asset]
    point = get_usd_prices([price_id]).get(price_id)
    if point is None or point.price <= 0:
        return None
    return point.price


def _measure_eth(taken_at: datetime) -> dict[str, Any]:
    if not eth_configured():
        return {"skipped": "unconfigured"}
    wallets = [w for w in list_all_wallets() if is_evm_wallet(w) and w.get("wallet_address")]
    summary: dict[str, Any] = {
        "wallets": len(wallets),
        "recorded": 0,
        "zero": 0,
        "read_failed": 0,
        "write_failed": 0,
    }
    if not wallets:
        return summary
    price = _price("eth")
    if price is None:
        summary["skipped"] = "no_price"
        return summary

    for row in wallets:
        address = str(row["wallet_address"]).lower()
        try:
            shares = stakewise.read_shares(address)
            assets = stakewise.convert_to_assets(shares)
        except Exception as e:  # noqa: BLE001 - VaultReadError is pre-redacted
            summary["read_failed"] += 1
            logger.warning("delegation ETH read failed: %s", e)
            continue
        usd = (Decimal(assets) / _WEI_PER_ETH) * price
        if record_measurement(address, "eth", assets, usd, taken_at):
            summary["recorded"] += 1
            summary["zero"] += 1 if assets == 0 else 0
        else:
            summary["write_failed"] += 1
    return summary


def _measure_ada(taken_at: datetime) -> dict[str, Any]:
    pool_id = cardano_pool_id()
    if pool_id is None:
        return {"skipped": "unconfigured"}
    wallets = [w for w in list_all_wallets() if is_cardano_wallet(w) and w.get("wallet_address")]
    summary: dict[str, Any] = {
        "wallets": len(wallets),
        "recorded": 0,
        "zero": 0,
        "not_yet_active": 0,
        "write_failed": 0,
    }
    if not wallets:
        return summary
    price = _price("ada")
    if price is None:
        summary["skipped"] = "no_price"
        return summary
    try:
        epoch = koios.get_tip_epoch()
        delegators = koios.list_pool_delegators(pool_id)
    except koios.KoiosError as e:
        # All or nothing: a partial delegator list would value the missing
        # delegators at zero.
        logger.warning("delegation ADA read failed: %s", e)
        summary["skipped"] = "koios_unavailable"
        return summary

    by_address: dict[str, dict[str, Any]] = {}
    for d in delegators:
        stake = str(d.get("stake_address") or "").lower()
        if stake:
            by_address[stake] = d

    for row in wallets:
        address = str(row["wallet_address"]).lower()
        entry = by_address.get(address)
        lovelace = 0
        if entry is not None:
            try:
                active_epoch = int(entry.get("active_epoch_no"))
                amount = int(str(entry.get("amount") or "0"))
            except (TypeError, ValueError):
                summary.setdefault("unparseable", 0)
                summary["unparseable"] += 1
                continue
            if active_epoch <= epoch:
                lovelace = max(amount, 0)
            else:
                summary["not_yet_active"] += 1
        usd = (Decimal(lovelace) / _LOVELACE_PER_ADA) * price
        if record_measurement(address, "ada", lovelace, usd, taken_at):
            summary["recorded"] += 1
            summary["zero"] += 1 if lovelace == 0 else 0
        else:
            summary["write_failed"] += 1
    summary["epoch"] = epoch
    return summary


def run_delegation_measurements_once(now: datetime | None = None) -> dict[str, Any]:
    """One measurement sweep. ``{"skipped": "disabled"}`` while
    DELEGATED_STAKING_ENABLED is off; an unconfigured asset reports
    ``{"skipped": "unconfigured"}`` and makes no external call."""
    if not Config.DELEGATED_STAKING_ENABLED:
        return {"skipped": "disabled"}
    taken_at = (now or datetime.now(UTC)).replace(microsecond=0)
    started = datetime.now(UTC)
    result: dict[str, Any] = {"taken_at": taken_at.isoformat()}
    for asset, measure in (("eth", _measure_eth), ("ada", _measure_ada)):
        try:
            result[asset] = measure(taken_at)
        except Exception as e:  # noqa: BLE001 - one asset must not sink the other
            logger.warning("delegation %s measurement failed: %s", asset, type(e).__name__)
            result[asset] = {"skipped": "error", "error": type(e).__name__}
    result["duration"] = (datetime.now(UTC) - started).total_seconds()
    return result
