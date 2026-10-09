"""DB access for delegated staking -- delegation_measurements,
delegation_allowance_rates, delegation_accruals, delegation_revenue and
delegation_controls (supabase/migrations/20261008230000_delegated_staking.sql).

Mirrors src/db/holdings.py: try/except + logger.warning + a safe default,
never a raise. Where a safe default would be UNSAFE for money the function
says so and returns None instead, so the caller can fail closed:

* the reconciliation sums return None (not 0) on a failed read -- a 0 for
  "credits granted" would make an overspent asset look healthy;
* get_controls returns None on a failed read -- the accrual treats that as
  "every asset paused", never as "nothing paused".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_MEASUREMENTS_TABLE = "delegation_measurements"
_RATES_TABLE = "delegation_allowance_rates"
_ACCRUALS_TABLE = "delegation_accruals"
_REVENUE_TABLE = "delegation_revenue"
_CONTROLS_TABLE = "delegation_controls"

ASSETS: tuple[str, ...] = ("eth", "ada")

_ROW_CAP = 10000
# Page size for reads that must be complete (reconciliation sums). PostgREST
# caps a response at 1000 rows by default, so a single select would silently
# truncate -- and a truncated "credits granted" understates the cost.
_PAGE = 1000
_MAX_PAGES = 1000


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _day_str(day: date | str) -> str:
    return day if isinstance(day, str) else day.isoformat()


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _select_all(build: Callable[[Any], Any]) -> list[dict[str, Any]]:
    """Every row of a query, page by page. Raises on any failure -- callers
    that need completeness catch it and fail closed."""
    client = get_supabase_client()
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        start = page * _PAGE
        result = build(client).range(start, start + _PAGE - 1).execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
    raise RuntimeError("delegation read exceeded the page limit")


# -- measurements ---------------------------------------------------------------


def record_measurement(
    wallet_address: str,
    asset: str,
    amount_raw: int,
    usd_value: Decimal,
    taken_at: datetime,
) -> bool:
    """Write one measurement row (zero included). False on any failure."""
    try:
        client = get_supabase_client()
        client.table(_MEASUREMENTS_TABLE).insert(
            {
                "wallet_address": wallet_address.lower(),
                "asset": asset,
                "amount_raw": str(int(amount_raw)),
                "usd_value": str(usd_value),
                "taken_at": taken_at.isoformat(),
            }
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"delegation_measurements insert failed ({asset}): {e}")
        return False


def get_measurements_for_date(wallet_address: str, asset: str, day: date) -> list[Decimal] | None:
    """The USD value of every measurement of (wallet, asset) on `day`. None
    on a failed read, so "could not read" is never mistaken for "no
    measurements"."""
    start, end = _day_bounds(day)
    try:
        client = get_supabase_client()
        result = (
            client.table(_MEASUREMENTS_TABLE)
            .select("usd_value, taken_at")
            .eq("wallet_address", wallet_address.lower())
            .eq("asset", asset)
            .gte("taken_at", start)
            .lt("taken_at", end)
            .limit(_ROW_CAP)
            .execute()
        )
        values = []
        for row in result.data or []:
            value = _decimal(row.get("usd_value"))
            if value is None:
                return None
            values.append(value)
        return values
    except Exception as e:
        logger.warning(f"delegation_measurements lookup failed ({asset}): {e}")
        return None


def list_measured_pairs_for_date(day: date) -> list[tuple[str, str]]:
    """Distinct (wallet, asset) pairs measured on `day`. Empty on error."""
    start, end = _day_bounds(day)
    try:
        rows = _select_all(
            lambda c: c.table(_MEASUREMENTS_TABLE)
            .select("wallet_address, asset")
            .gte("taken_at", start)
            .lt("taken_at", end)
            .order("id", desc=False)
        )
        return sorted({(str(r["wallet_address"]).lower(), str(r["asset"])) for r in rows})
    except Exception as e:
        logger.warning(f"delegation_measurements pair listing failed: {e}")
        return []


def get_latest_measurements(wallet_address: str) -> list[dict[str, Any]]:
    """The most recent measurement row per asset for one wallet."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_MEASUREMENTS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .order("taken_at", desc=True)
            .limit(50)
            .execute()
        )
        latest: dict[str, dict[str, Any]] = {}
        for row in result.data or []:
            latest.setdefault(str(row.get("asset")), row)
        return list(latest.values())
    except Exception as e:
        logger.warning(f"delegation_measurements latest lookup failed: {e}")
        return []


def get_latest_measurement_taken_at() -> datetime | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_MEASUREMENTS_TABLE)
            .select("taken_at")
            .order("taken_at", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        return datetime.fromisoformat(str(result.data[0]["taken_at"]).replace("Z", "+00:00"))
    except Exception as e:
        logger.warning(f"delegation_measurements latest taken_at failed: {e}")
        return None


# -- allowance rates --------------------------------------------------------------


def get_active_rates() -> dict[str, dict[str, Any]]:
    """{asset: active rate row}. An asset with no active row is absent,
    which callers read as "earns nothing". Empty on error."""
    try:
        client = get_supabase_client()
        result = client.table(_RATES_TABLE).select("*").eq("is_active", True).execute()
        return {str(r["asset"]): r for r in result.data or []}
    except Exception as e:
        logger.warning(f"delegation_allowance_rates lookup failed: {e}")
        return {}


def replace_active_rate(
    asset: str, credits_per_1k_usd_per_day: Decimal, note: str | None
) -> dict[str, Any] | None:
    """Deactivate the asset's active rate and insert a new active one. Rows
    are never edited in place, so every accrual's rate stays traceable."""
    try:
        client = get_supabase_client()
        client.table(_RATES_TABLE).update(
            {"is_active": False, "updated_at": datetime.now(UTC).isoformat()}
        ).eq("asset", asset).eq("is_active", True).execute()
        result = (
            client.table(_RATES_TABLE)
            .insert(
                {
                    "asset": asset,
                    "credits_per_1k_usd_per_day": str(credits_per_1k_usd_per_day),
                    "is_active": True,
                    "note": note,
                }
            )
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_allowance_rates replace failed ({asset}): {e}")
        return None


def deactivate_rate(asset: str) -> bool:
    try:
        client = get_supabase_client()
        client.table(_RATES_TABLE).update(
            {"is_active": False, "updated_at": datetime.now(UTC).isoformat()}
        ).eq("asset", asset).eq("is_active", True).execute()
        return True
    except Exception as e:
        logger.warning(f"delegation_allowance_rates deactivate failed ({asset}): {e}")
        return False


# -- accruals ------------------------------------------------------------------------


def get_accrual(wallet_address: str, asset: str, reward_date: date | str) -> dict[str, Any] | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("asset", asset)
            .eq("reward_date", _day_str(reward_date))
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_accruals lookup failed ({asset}): {e}")
        return None


def list_accruals_for_wallet_date(
    wallet_address: str, reward_date: date | str
) -> list[dict[str, Any]] | None:
    """Every asset's accrual for one wallet-day. None on error, because the
    per-account cap is computed from it and must not read a failure as zero."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("reward_date", _day_str(reward_date))
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_accruals wallet-day lookup failed: {e}")
        return None


def list_accruals_for_date(reward_date: date | str) -> list[dict[str, Any]] | None:
    """Every accrual already decided for a date (budget accounting). None on
    error."""
    day = _day_str(reward_date)
    try:
        return _select_all(
            lambda c: c.table(_ACCRUALS_TABLE).select("*").eq("reward_date", day).order("id")
        )
    except Exception as e:
        logger.warning(f"delegation_accruals date listing failed: {e}")
        return None


def create_accrual(
    wallet_address: str,
    asset: str,
    reward_date: date,
    usd_basis: Decimal,
    credits: Decimal,
    rate: Decimal,
    user_id: int | None,
) -> dict[str, Any] | None:
    """Insert the wallet-asset-day accrual as 'pending', before any credit
    is granted. None on any failure, including the UNIQUE conflict a
    concurrent run produces -- callers re-read with get_accrual()."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .insert(
                {
                    "wallet_address": wallet_address.lower(),
                    "asset": asset,
                    "reward_date": _day_str(reward_date),
                    "usd_basis": str(usd_basis),
                    "credits": str(credits),
                    "rate_credits_per_1k_usd": str(rate),
                    "user_id": user_id,
                    "status": "pending",
                    "ledger_request_id": None,
                }
            )
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_accruals insert failed ({asset}): {e}")
        return None


def mark_accrual_paid(accrual_id: int, ledger_request_id: str) -> dict[str, Any] | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .update(
                {
                    "status": "paid",
                    "ledger_request_id": ledger_request_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", accrual_id)
            .eq("status", "pending")
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_accruals mark-paid failed for id={accrual_id}: {e}")
        return None


def list_pending_accruals_since(min_reward_date: date | str) -> list[dict[str, Any]]:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("status", "pending")
            .gte("reward_date", _day_str(min_reward_date))
            .order("reward_date", desc=False)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_accruals pending listing failed: {e}")
        return []


def list_pending_accruals(wallet_address: str) -> list[dict[str, Any]]:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("status", "pending")
            .order("reward_date", desc=False)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_accruals pending lookup failed: {e}")
        return []


def list_accruals_for_wallet(wallet_address: str, limit: int = 30) -> list[dict[str, Any]]:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .order("reward_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_accruals history lookup failed: {e}")
        return []


def sum_granted_credits(asset: str) -> Decimal | None:
    """All credits ever committed for `asset` (pending + paid). None -- never
    0 -- on a failed read: reconciliation must not see an unknown cost as
    free."""
    try:
        rows = _select_all(
            lambda c: c.table(_ACCRUALS_TABLE)
            .select("credits")
            .eq("asset", asset)
            .in_("status", ["pending", "paid"])
            .order("id")
        )
        total = Decimal(0)
        for row in rows:
            value = _decimal(row.get("credits"))
            if value is None:
                return None
            total += value
        return total
    except Exception as e:
        logger.warning(f"delegation_accruals sum failed ({asset}): {e}")
        return None


# -- revenue -------------------------------------------------------------------------


def get_latest_revenue(asset: str) -> dict[str, Any] | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_REVENUE_TABLE)
            .select("*")
            .eq("asset", asset)
            .order("revenue_date", desc=True)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_revenue latest lookup failed ({asset}): {e}")
        return None


def list_revenue_period_keys(asset: str) -> set[str] | None:
    try:
        rows = _select_all(
            lambda c: c.table(_REVENUE_TABLE).select("period_key").eq("asset", asset).order("id")
        )
        return {str(r["period_key"]) for r in rows}
    except Exception as e:
        logger.warning(f"delegation_revenue period listing failed ({asset}): {e}")
        return None


def insert_revenue(
    asset: str,
    revenue_date: date,
    period_key: str,
    revenue_native: Decimal,
    revenue_usd: Decimal,
    source: str,
    raw_amount: int | None = None,
) -> dict[str, Any] | None:
    """Record one revenue period. None on failure, including the
    (asset, period_key) UNIQUE conflict of a re-run -- which is the point:
    a period is counted once."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_REVENUE_TABLE)
            .insert(
                {
                    "asset": asset,
                    "revenue_date": _day_str(revenue_date),
                    "period_key": period_key,
                    "revenue_native": str(revenue_native),
                    "revenue_usd": str(revenue_usd),
                    "raw_amount": None if raw_amount is None else str(int(raw_amount)),
                    "source": source,
                }
            )
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_revenue insert failed ({asset}/{period_key}): {e}")
        return None


def sum_revenue_usd(asset: str) -> Decimal | None:
    """All recorded revenue for `asset`, in USD. None on a failed read."""
    try:
        rows = _select_all(
            lambda c: c.table(_REVENUE_TABLE).select("revenue_usd").eq("asset", asset).order("id")
        )
        total = Decimal(0)
        for row in rows:
            value = _decimal(row.get("revenue_usd"))
            if value is None:
                return None
            total += value
        return total
    except Exception as e:
        logger.warning(f"delegation_revenue sum failed ({asset}): {e}")
        return None


def list_recent_revenue(limit: int = 30) -> list[dict[str, Any]]:
    try:
        client = get_supabase_client()
        result = (
            client.table(_REVENUE_TABLE)
            .select("*")
            .order("revenue_date", desc=True)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_revenue recent listing failed: {e}")
        return []


# -- controls ------------------------------------------------------------------------


def get_controls() -> dict[str, dict[str, Any]] | None:
    """{asset: control row}. None on a failed read -- callers treat that as
    every asset paused."""
    try:
        client = get_supabase_client()
        result = client.table(_CONTROLS_TABLE).select("*").execute()
        return {str(r["asset"]): r for r in result.data or []}
    except Exception as e:
        logger.warning(f"delegation_controls lookup failed: {e}")
        return None


def pause_accruals(asset: str, reason: str) -> bool:
    now = datetime.now(UTC).isoformat()
    try:
        client = get_supabase_client()
        client.table(_CONTROLS_TABLE).upsert(
            {
                "asset": asset,
                "accruals_paused": True,
                "paused_reason": reason[:500],
                "paused_at": now,
                "updated_at": now,
            },
            on_conflict="asset",
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"delegation_controls pause failed ({asset}): {e}")
        return False


def resume_accruals(asset: str, resumed_by: str) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    try:
        client = get_supabase_client()
        result = (
            client.table(_CONTROLS_TABLE)
            .upsert(
                {
                    "asset": asset,
                    "accruals_paused": False,
                    "resumed_at": now,
                    "resumed_by": resumed_by[:200],
                    "updated_at": now,
                },
                on_conflict="asset",
            )
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_controls resume failed ({asset}): {e}")
        return None
