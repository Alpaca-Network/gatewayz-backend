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
_ATTRIBUTIONS_TABLE = "delegation_reward_attributions"

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


def get_measurement_amounts_for_date(asset: str, day: date) -> dict[str, list[int]] | None:
    """{wallet: [amount_raw of every measurement on `day`]} for one asset --
    wei for ETH, lovelace for ADA. None on a failed or unparseable read, so
    "could not read" is never mistaken for "no stake"."""
    start, end = _day_bounds(day)
    try:
        rows = _select_all(
            lambda c: c.table(_MEASUREMENTS_TABLE)
            .select("wallet_address, amount_raw")
            .eq("asset", asset)
            .gte("taken_at", start)
            .lt("taken_at", end)
            .order("id", desc=False)
        )
        amounts: dict[str, list[int]] = {}
        for row in rows:
            value = _decimal(row.get("amount_raw"))
            if value is None or not value.is_finite():
                return None
            amounts.setdefault(str(row["wallet_address"]).lower(), []).append(int(value))
        return amounts
    except Exception as e:
        logger.warning(f"delegation_measurements amount lookup failed ({asset}): {e}")
        return None


def get_latest_sweep_rows(asset: str) -> list[dict[str, Any]] | None:
    """Every row of the most recent measurement sweep for `asset` -- one per
    linked wallet measured, zero included: [{wallet_address, amount_raw,
    usd_value, taken_at}]. Empty when there is no sweep yet; None on a failed
    read."""
    try:
        client = get_supabase_client()
        latest = (
            client.table(_MEASUREMENTS_TABLE)
            .select("taken_at")
            .eq("asset", asset)
            .order("taken_at", desc=True)
            .limit(1)
            .execute()
        )
        if not latest.data:
            return []
        taken_at = latest.data[0]["taken_at"]
        return _select_all(
            lambda c: c.table(_MEASUREMENTS_TABLE)
            .select("wallet_address, amount_raw, usd_value, taken_at")
            .eq("asset", asset)
            .eq("taken_at", taken_at)
            .order("id")
        )
    except Exception as e:
        logger.warning(f"delegation_measurements latest sweep failed ({asset}): {e}")
        return None


def get_user_emails(user_ids: list[int]) -> dict[int, str | None]:
    """{user_id: email} for the admin staking view. Missing ids are absent;
    empty on error."""
    emails: dict[int, str | None] = {}
    ids = sorted({int(i) for i in user_ids})
    try:
        client = get_supabase_client()
        for start in range(0, len(ids), 200):
            result = (
                client.table("users")
                .select("id, email")
                .in_("id", ids[start : start + 200])
                .execute()
            )
            for row in result.data or []:
                emails[int(row["id"])] = row.get("email")
        return emails
    except Exception as e:
        logger.warning(f"users email lookup failed: {e}")
        return emails


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


def _rpc_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and len(data) == 1 and not data.get("status"):
        # Some PostgREST versions wrap a scalar jsonb return as {fn_name: value}.
        data = next(iter(data.values()))
    return data if isinstance(data, dict) else None


def reserve_accrual(
    wallet_address: str,
    asset: str,
    reward_date: date,
    usd_basis: Decimal,
    credits: Decimal,
    rate: Decimal,
    user_id: int | None,
    account_cap: Decimal,
    global_budget: Decimal,
) -> dict[str, Any] | None:
    """Atomically reserve one wallet-asset-day accrual (supabase function
    delegation_reserve_accrual): under the date's advisory lock it caps the
    credits at the account's remaining headroom, refuses when the global
    budget would be exceeded or the asset is paused, and inserts the row as
    'pending'. Returns {status, accrual?, capped?}; None on any failure,
    which callers treat as "nothing reserved"."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "delegation_reserve_accrual",
            {
                "p_wallet_address": wallet_address.lower(),
                "p_asset": asset,
                "p_reward_date": _day_str(reward_date),
                "p_usd_basis": str(usd_basis),
                "p_credits": str(credits),
                "p_rate": str(rate),
                "p_user_id": user_id,
                "p_account_cap": str(account_cap),
                "p_global_budget": str(global_budget),
            },
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"delegation_reserve_accrual failed ({asset}): {e}")
        return None


def claim_accrual(accrual_id: int, user_id: int, account_cap: Decimal) -> dict[str, Any] | None:
    """Atomically claim a pending accrual for payment to `user_id`
    (supabase function delegation_claim_accrual): re-checks the pause and the
    account cap under the date's advisory lock, voids a row that no longer
    fits, else flips it to 'claimed'. Returns {status, accrual?}; None on any
    failure (the row stays pending)."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "delegation_claim_accrual",
            {"p_accrual_id": accrual_id, "p_user_id": user_id, "p_account_cap": str(account_cap)},
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"delegation_claim_accrual failed for id={accrual_id}: {e}")
        return None


def release_claim(accrual_id: int, user_id: int) -> bool:
    """Put a claimed accrual back to pending (supabase function
    delegation_release_claim) when its payee could not be re-verified as the
    wallet's current account. False on failure -- the row then stays claimed
    and unpaid, and is re-verified on the next attempt."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "delegation_release_claim", {"p_accrual_id": accrual_id, "p_user_id": user_id}
        ).execute()
        return bool(result.data)
    except Exception as e:
        logger.warning(f"delegation_release_claim failed for id={accrual_id}: {e}")
        return False


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
            .eq("status", "claimed")
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
            .in_("status", ["pending", "claimed"])
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
            .in_("status", ["pending", "claimed"])
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
    """All credits ever committed for `asset` (pending + claimed + paid). None -- never
    0 -- on a failed read: reconciliation must not see an unknown cost as
    free."""
    try:
        rows = _select_all(
            lambda c: c.table(_ACCRUALS_TABLE)
            .select("credits")
            .eq("asset", asset)
            .in_("status", ["pending", "claimed", "paid"])
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
    stake_total_native: Decimal | None = None,
) -> dict[str, Any] | None:
    """Record one revenue period. None on failure, including the
    (asset, period_key) UNIQUE conflict of a re-run -- which is the point:
    a period is counted once. `stake_total_native` is the attribution
    denominator (the vault's / pool's total stake for the period); the key is
    only sent when known."""
    row: dict[str, Any] = {
        "asset": asset,
        "revenue_date": _day_str(revenue_date),
        "period_key": period_key,
        "revenue_native": str(revenue_native),
        "revenue_usd": str(revenue_usd),
        "raw_amount": None if raw_amount is None else str(int(raw_amount)),
        "source": source,
    }
    if stake_total_native is not None:
        row["stake_total_native"] = str(stake_total_native)
    try:
        client = get_supabase_client()
        result = client.table(_REVENUE_TABLE).insert(row).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_revenue insert failed ({asset}/{period_key}): {e}")
        return None


def sum_revenue_usd(asset: str, since: date | str | None = None) -> Decimal | None:
    """All recorded revenue for `asset` (revenue_date >= `since` when given),
    in USD. None on a failed read."""

    def build(c: Any) -> Any:
        query = c.table(_REVENUE_TABLE).select("revenue_usd").eq("asset", asset)
        if since is not None:
            query = query.gte("revenue_date", _day_str(since))
        return query.order("id")

    try:
        rows = _select_all(build)
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


def list_revenue_since(asset: str, min_revenue_date: date | str) -> list[dict[str, Any]] | None:
    """Every revenue row for `asset` recorded on or after `min_revenue_date`,
    oldest first. None on a failed read."""
    try:
        return _select_all(
            lambda c: c.table(_REVENUE_TABLE)
            .select("*")
            .eq("asset", asset)
            .gte("revenue_date", _day_str(min_revenue_date))
            .order("revenue_date", desc=False)
            .order("id", desc=False)
        )
    except Exception as e:
        logger.warning(f"delegation_revenue listing failed ({asset}): {e}")
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


# -- reward-share attributions (v3) ---------------------------------------------------


def get_attribution(wallet_address: str, asset: str, period_key: str) -> dict[str, Any] | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_ATTRIBUTIONS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("asset", asset)
            .eq("period_key", period_key)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"delegation_reward_attributions lookup failed ({asset}): {e}")
        return None


def reserve_attribution(
    wallet_address: str,
    asset: str,
    period_key: str,
    reward_date: date | str,
    user_id: int | None,
    stake_native: Decimal,
    stake_total_native: Decimal,
    attributed_usd: Decimal,
    reward_share: Decimal,
    request_id: str,
    account_cap: Decimal,
    global_budget: Decimal,
) -> dict[str, Any] | None:
    """Atomically reserve one wallet-asset-period attribution (supabase
    function delegation_reserve_attribution): under the reward date's advisory
    lock it caps the attribution at the period's unattributed revenue and the
    discount at the account's remaining daily headroom, refuses on a paused
    asset or an exhausted global budget, and inserts the row 'pending'.
    Returns {status, attribution?, capped?}; None on any failure, which
    callers treat as "nothing reserved"."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "delegation_reserve_attribution",
            {
                "p_wallet_address": wallet_address.lower(),
                "p_asset": asset,
                "p_period_key": period_key,
                "p_reward_date": _day_str(reward_date),
                "p_user_id": user_id,
                "p_stake_native": str(stake_native),
                "p_stake_total_native": str(stake_total_native),
                "p_attributed_usd": str(attributed_usd),
                "p_reward_share": str(reward_share),
                "p_request_id": request_id,
                "p_account_cap": str(account_cap),
                "p_global_budget": str(global_budget),
            },
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"delegation_reserve_attribution failed ({asset}): {e}")
        return None


def grant_attribution(
    attribution_id: int, user_id: int, account_cap: Decimal
) -> dict[str, Any] | None:
    """Atomically grant a pending attribution to `user_id` (supabase function
    delegation_grant_attribution): re-checks the pause, the wallet link, the
    account and the cap under the date's advisory lock, credits the discount
    balance and marks the row granted in one transaction. Returns {status,
    attribution?, grant?}; None on any failure (the row stays pending)."""
    try:
        client = get_supabase_client()
        result = client.rpc(
            "delegation_grant_attribution",
            {
                "p_attribution_id": attribution_id,
                "p_user_id": user_id,
                "p_account_cap": str(account_cap),
            },
        ).execute()
        return _rpc_payload(result.data)
    except Exception as e:
        logger.warning(f"delegation_grant_attribution failed for id={attribution_id}: {e}")
        return None


def list_pending_attributions(
    wallet_address: str | None = None, min_reward_date: date | str | None = None
) -> list[dict[str, Any]]:
    """Pending attributions, oldest first -- for one wallet (pay on link) or
    since a date (the job's retry sweep). Empty on error."""
    try:
        client = get_supabase_client()
        query = client.table(_ATTRIBUTIONS_TABLE).select("*").eq("status", "pending")
        if wallet_address is not None:
            query = query.eq("wallet_address", wallet_address.lower())
        if min_reward_date is not None:
            query = query.gte("reward_date", _day_str(min_reward_date))
        result = query.order("reward_date", desc=False).limit(_ROW_CAP).execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"delegation_reward_attributions pending listing failed: {e}")
        return []


def list_attributions_for_account(
    user_id: int, wallet_addresses: list[str], limit: int = 90
) -> list[dict[str, Any]]:
    """The account's attributions, newest first: rows granted to / decided
    for it, plus rows for wallets it has linked now (pending ones included).
    Empty on error."""
    try:
        client = get_supabase_client()
        rows: dict[Any, dict[str, Any]] = {}
        by_user = (
            client.table(_ATTRIBUTIONS_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        for row in by_user.data or []:
            rows[row.get("id")] = row
        wallets = [w.lower() for w in wallet_addresses if w]
        if wallets:
            by_wallet = (
                client.table(_ATTRIBUTIONS_TABLE)
                .select("*")
                .in_("wallet_address", wallets)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            for row in by_wallet.data or []:
                rows[row.get("id")] = row
        ordered = sorted(
            rows.values(),
            key=lambda r: (str(r.get("created_at") or ""), int(r.get("id") or 0)),
            reverse=True,
        )
        return ordered[:limit]
    except Exception as e:
        logger.warning(f"delegation_reward_attributions account listing failed: {e}")
        return []


def list_attribution_user_ids() -> set[int] | None:
    """Every account an attribution was decided for or granted to. None on
    a failed read."""
    try:
        rows = _select_all(lambda c: c.table(_ATTRIBUTIONS_TABLE).select("user_id").order("id"))
        return {int(r["user_id"]) for r in rows if r.get("user_id") is not None}
    except Exception as e:
        logger.warning(f"delegation_reward_attributions user listing failed: {e}")
        return None


def sum_attributed_discount(
    asset: str, statuses: tuple[str, ...] = ("pending", "granted")
) -> Decimal | None:
    """Discount committed for `asset` (pending + granted by default). None --
    never 0 -- on a failed read: reconciliation must not see an unknown
    grant total as nothing granted."""
    try:
        rows = _select_all(
            lambda c: c.table(_ATTRIBUTIONS_TABLE)
            .select("discount_usd")
            .eq("asset", asset)
            .in_("status", list(statuses))
            .order("id")
        )
        total = Decimal(0)
        for row in rows:
            value = _decimal(row.get("discount_usd"))
            if value is None:
                return None
            total += value
        return total
    except Exception as e:
        logger.warning(f"delegation_reward_attributions sum failed ({asset}): {e}")
        return None
