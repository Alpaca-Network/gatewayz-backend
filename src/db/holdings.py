"""DB access for the holdings-rewards tables -- holdings_tokens,
wallet_holdings_snapshots, holdings_reward_rates and
holdings_reward_accruals (see
supabase/migrations/20260915000000_holdings_rewards.sql).

Holdings rewards pay inference credits for HOLDING top-20 tokens in a
wallet the user has proven they control (src/db/user_wallets.py). It is
non-custodial -- we never take a deposit, we only read balances -- so the
input is an observed USD valuation rather than a staked amount. Everything
downstream of that input is the staking-rewards payout half reused
verbatim, so this module mirrors src/db/staking_rewards.py: try/except +
logger.warning + a safe default, never a raise. Callers (the daily job in
src/services/holdings/, and the admin/user routes) must treat a lookup
failure as "no data," never as a hard failure.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_TOKENS_TABLE = "holdings_tokens"
_SNAPSHOTS_TABLE = "wallet_holdings_snapshots"
_SWEEPS_TABLE = "wallet_holdings_sweeps"
_RATES_TABLE = "holdings_reward_rates"
_ACCRUALS_TABLE = "holdings_reward_accruals"

# Row cap for snapshot/accrual reads -- same convention as
# src/db/staking_rewards.py's _ROW_CAP: a real bound rather than an
# unbounded select. A day of snapshots for one wallet is (tokens x sweeps),
# far under this.
_ROW_CAP = 10000

# Rows read back when resolving a wallet's most recent sweep. One sweep is
# (enabled tokens) rows, so this covers many sweeps' worth of history and
# still bounds the read.
_LATEST_SWEEP_ROW_CAP = 200


def _day_bounds(day: date) -> tuple[str, str]:
    """The half-open UTC [start, end) ISO bounds of one reward day, so a
    midnight snapshot belongs to exactly one day."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _day_str(day: date | str) -> str:
    return day if isinstance(day, str) else day.isoformat()


def list_enabled_tokens() -> list[dict[str, Any]]:
    """Every enabled row of the token registry. Empty list on any lookup
    error -- and empty is also the normal state until ops seeds the
    registry, which the migration deliberately does not do."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TOKENS_TABLE)
            .select("*")
            .eq("is_enabled", True)
            .order("chain_id", desc=False)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_tokens lookup failed: {e}")
        return []


def record_snapshot(
    wallet_address: str,
    token_id: int,
    raw_amount: int,
    usd_value: Decimal,
    taken_at: datetime,
) -> dict[str, Any] | None:
    """Insert one observed balance. Every row of a single sweep must share
    one `taken_at` -- that shared timestamp is what identifies a batch to
    get_min_usd_for_date(), so the caller generates it once per sweep and
    passes the same value for every token.

    raw_amount (numeric(78,0), uint256-safe) and usd_value (numeric(38,18))
    are sent as strings so a big int or a Decimal never round-trips through
    a float. Returns the created row, or None on any failure.
    """
    payload = {
        "wallet_address": wallet_address.lower(),
        "token_id": token_id,
        "raw_amount": str(raw_amount),
        "usd_value": str(usd_value),
        "taken_at": taken_at.isoformat(),
    }
    try:
        client = get_supabase_client()
        result = client.table(_SNAPSHOTS_TABLE).insert(payload).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(
            f"wallet_holdings_snapshots insert failed for {wallet_address}/token {token_id}: {e}"
        )
        return None


def get_min_usd_for_date(wallet_address: str, day: date) -> Decimal | None:
    """The wallet's LOWEST per-token-derived total across that UTC day.

    DETAIL ONLY -- the daily job does NOT use this. wallet_holdings_snapshots
    holds no rows for a wallet that held nothing, so a day's minimum derived
    from it silently omits every sweep in which the wallet was empty. The
    payout reads get_sweep_totals_for_date() instead, which has a real zero
    row for those sweeps. Kept for per-token audit and analysis, where "the
    lowest total across the sweeps that had holdings" is the intended
    question.

    Returns None when the day has no per-token rows at all, or when a stored
    usd_value cannot be parsed.
    """
    start, end = _day_bounds(day)
    try:
        client = get_supabase_client()
        result = (
            client.table(_SNAPSHOTS_TABLE)
            .select("taken_at,usd_value")
            .eq("wallet_address", wallet_address.lower())
            .gte("taken_at", start)
            .lt("taken_at", end)
            .limit(_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(
            f"wallet_holdings_snapshots lookup failed for {wallet_address}/{day.isoformat()}: {e}"
        )
        return None

    if not rows:
        return None

    batch_totals: dict[str, Decimal] = {}
    for row in rows:
        batch = str(row.get("taken_at"))
        try:
            value = Decimal(str(row.get("usd_value", "0")))
        except (InvalidOperation, ValueError):
            logger.warning(
                f"wallet_holdings_snapshots has unparseable usd_value "
                f"{row.get('usd_value')!r} for {wallet_address}/{day.isoformat()}"
            )
            return None
        batch_totals[batch] = batch_totals.get(batch, Decimal(0)) + value

    return min(batch_totals.values())


def list_wallets_with_snapshots_for_date(day: date) -> list[str]:
    """Every distinct wallet address with at least one snapshot on that UTC
    day, lowercased and sorted -- the daily job's work list. Empty list on
    any lookup error."""
    start, end = _day_bounds(day)
    try:
        client = get_supabase_client()
        result = (
            client.table(_SNAPSHOTS_TABLE)
            .select("wallet_address")
            .gte("taken_at", start)
            .lt("taken_at", end)
            .limit(_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"wallet_holdings_snapshots wallet list failed for {day.isoformat()}: {e}")
        return []

    return sorted({str(row["wallet_address"]).lower() for row in rows if row.get("wallet_address")})


def record_sweep(
    wallet_address: str,
    taken_at: datetime,
    usd_total: Decimal,
) -> dict[str, Any] | None:
    """Record that one COMPLETED observation sweep valued this wallet at
    `usd_total`.

    Exactly one row per wallet per sweep, written whenever the sweep was
    fully measurable -- **including a zero row when the wallet held
    nothing**. That zero is the whole point: per-token rows do not exist for
    an empty wallet, so without this the sweep would leave no trace and an
    emptied wallet would be invisible to the day's minimum rather than
    dragging it to zero. A sweep that could NOT be measured (an unread chain,
    a held token with no fresh price) must not call this at all -- "we do not
    know" is not "they held zero".

    usd_total is sent as a string so a Decimal never round-trips through a
    float. Returns the created row, or None on any failure -- including the
    (wallet_address, taken_at) UNIQUE conflict, which means this sweep was
    already recorded.
    """
    payload = {
        "wallet_address": wallet_address.lower(),
        "taken_at": taken_at.isoformat(),
        "usd_total": str(usd_total),
    }
    try:
        client = get_supabase_client()
        result = client.table(_SWEEPS_TABLE).insert(payload).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"wallet_holdings_sweeps insert failed for {wallet_address}: {e}")
        return None


def get_sweep_totals_for_date(wallet_address: str, day: date) -> dict[str, Decimal] | None:
    """Every completed sweep's USD total for one wallet on one UTC day,
    keyed by `taken_at`.

    This is what the daily accrual pays on. It answers both of the job's
    questions from one read: how many times the wallet was successfully
    observed (the anti-farm coverage check) and the LOWEST total among those
    observations (the basis). A sweep in which the wallet held nothing is
    present here with a total of Decimal(0) and pulls the minimum down,
    which is precisely what stops a wallet being funded just before a couple
    of sweeps and paid as though it held that balance all day.

    Returns None when the day has no completed sweeps at all, or when a
    stored usd_total cannot be parsed. An empty dict is never returned.
    """
    start, end = _day_bounds(day)
    try:
        client = get_supabase_client()
        result = (
            client.table(_SWEEPS_TABLE)
            .select("taken_at,usd_total")
            .eq("wallet_address", wallet_address.lower())
            .gte("taken_at", start)
            .lt("taken_at", end)
            .limit(_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(
            f"wallet_holdings_sweeps lookup failed for {wallet_address}/{day.isoformat()}: {e}"
        )
        return None

    if not rows:
        return None

    totals: dict[str, Decimal] = {}
    for row in rows:
        try:
            totals[str(row.get("taken_at"))] = Decimal(str(row.get("usd_total", "0")))
        except (InvalidOperation, ValueError):
            logger.warning(
                f"wallet_holdings_sweeps has unparseable usd_total "
                f"{row.get('usd_total')!r} for {wallet_address}/{day.isoformat()}"
            )
            return None
    return totals


def count_sweeps_for_date(wallet_address: str, day: date) -> int:
    """How many completed observation sweeps the wallet has on that UTC day.

    The daily accrual will not pay a day with too few: "lowest value seen
    that day" only has force if several values were seen. 0 when nothing was
    observed, and 0 on any lookup error -- which reads as "not enough sweeps
    to pay", the safe direction. The accrual uses get_sweep_totals_for_date()
    instead, since it needs the minimum as well and one read gives it both;
    this is the standalone count for callers that only want coverage.
    """
    totals = get_sweep_totals_for_date(wallet_address, day)
    return len(totals) if totals else 0


def list_wallets_with_sweeps_for_date(day: date) -> list[str]:
    """Every distinct wallet with at least one completed sweep on that UTC
    day, lowercased and sorted -- the daily job's work list. Includes a
    wallet observed holding nothing, which must still be considered so its
    zero counts toward the day. Empty list on any lookup error."""
    start, end = _day_bounds(day)
    try:
        client = get_supabase_client()
        result = (
            client.table(_SWEEPS_TABLE)
            .select("wallet_address")
            .gte("taken_at", start)
            .lt("taken_at", end)
            .limit(_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"wallet_holdings_sweeps wallet list failed for {day.isoformat()}: {e}")
        return []

    return sorted({str(row["wallet_address"]).lower() for row in rows if row.get("wallet_address")})


def list_all_tokens() -> list[dict[str, Any]]:
    """The whole token registry, enabled or not -- the admin view, where a
    disabled row has to stay visible so it can be re-enabled. Empty list on
    any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TOKENS_TABLE)
            .select("*")
            .order("chain_id", desc=False)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_tokens full list failed: {e}")
        return []


def create_token(
    chain_id: int,
    contract_address: str | None,
    symbol: str,
    decimals: int,
    price_id: str,
    is_enabled: bool = True,
) -> dict[str, Any] | None:
    """Add one asset to the registry. `contract_address` is None for a
    chain's native coin. Returns the created row, or None on any failure --
    including the (chain_id, contract) UNIQUE conflict, which means the
    asset is already registered and should be updated instead."""
    payload = {
        "chain_id": chain_id,
        "contract_address": contract_address.lower() if contract_address else None,
        "symbol": symbol,
        "decimals": decimals,
        "price_id": price_id,
        "is_enabled": is_enabled,
    }
    try:
        client = get_supabase_client()
        result = client.table(_TOKENS_TABLE).insert(payload).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"holdings_tokens insert failed for chain {chain_id}/{symbol}: {e}")
        return None


def update_token(token_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Patch one registry row. Only the caller-supplied fields are written,
    so disabling an asset does not have to restate its address or decimals.
    Returns the updated row, or None on any failure (including no such
    row)."""
    if not updates:
        return None
    payload = dict(updates)
    if payload.get("contract_address"):
        payload["contract_address"] = str(payload["contract_address"]).lower()
    try:
        client = get_supabase_client()
        result = client.table(_TOKENS_TABLE).update(payload).eq("id", token_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"holdings_tokens update failed for id={token_id}: {e}")
        return None


def get_latest_snapshot_usd(wallet_address: str) -> Decimal | None:
    """The wallet's total USD value at its most recent observation.

    Rows sharing a `taken_at` are one sweep, so this takes the newest
    `taken_at` present and sums only that sweep -- never the sum of every
    recent row, which would multiply the wallet by the number of sweeps
    read back. Returns None when the wallet has never been observed (or on
    error); a wallet observed holding nothing returns Decimal(0).

    This is the "what am I holding right now" figure the user-facing view
    shows. It is deliberately NOT what the payout uses: the payout uses the
    day's LOWEST sweep (get_min_usd_for_date), which is usually smaller.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_SNAPSHOTS_TABLE)
            .select("taken_at,usd_value")
            .eq("wallet_address", wallet_address.lower())
            .order("taken_at", desc=True)
            .limit(_LATEST_SWEEP_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"wallet_holdings_snapshots latest lookup failed for {wallet_address}: {e}")
        return None

    if not rows:
        return None

    newest = max(str(row.get("taken_at")) for row in rows)
    total = Decimal(0)
    for row in rows:
        if str(row.get("taken_at")) != newest:
            continue
        try:
            total += Decimal(str(row.get("usd_value", "0")))
        except (InvalidOperation, ValueError):
            logger.warning(
                f"wallet_holdings_snapshots has unparseable usd_value "
                f"{row.get('usd_value')!r} for {wallet_address}"
            )
            return None
    return total


def list_holdings_accruals_for_wallet(wallet_address: str, limit: int = 30) -> list[dict[str, Any]]:
    """One wallet's accruals, newest reward_date first, for the user-facing
    history. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .order("reward_date", desc=True)
            .limit(max(1, min(int(limit), _ROW_CAP)))
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_reward_accruals history failed for {wallet_address}: {e}")
        return []


def list_holdings_accruals_since(min_reward_date: date | str) -> list[dict[str, Any]]:
    """Every accrual on or after `min_reward_date`, across all wallets,
    newest first -- the admin summary's input. Empty list on any lookup
    error."""
    day = _day_str(min_reward_date)
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .gte("reward_date", day)
            .order("reward_date", desc=True)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_reward_accruals summary failed since {day}: {e}")
        return []


def replace_active_holdings_rates(rates: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Deactivate every currently-active tier and insert `rates` as the new
    active set.

    Existing tier rows are never mutated in place -- a tier that an accrual
    was already computed against must not change out from under it -- so a
    rate change always means "deactivate old, insert new." Not transactional
    (an UPDATE then an INSERT); a failure between the two leaves zero active
    tiers, which the caller surfaces as a 500 and which a retry fixes, since
    regenerating the set is itself idempotent.

    The table's mandatory min_usd = 0 row is unaffected: deactivating a row
    does not delete it, so the constraint trigger still sees a zero tier.

    Returns the newly-inserted rows, or None on any failure.
    """
    try:
        client = get_supabase_client()
        client.table(_RATES_TABLE).update({"is_active": False}).eq("is_active", True).execute()
        rows = [
            {
                "min_usd": str(rate["min_usd"]),
                "credits_per_1k_usd_per_day": str(rate["credits_per_1k_usd_per_day"]),
                "note": rate.get("note"),
                "is_active": True,
            }
            for rate in rates
        ]
        result = client.table(_RATES_TABLE).insert(rows).execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_reward_rates replace failed: {e}")
        return None


def get_active_holdings_rates() -> list[dict[str, Any]]:
    """Active rate tiers, ascending by min_usd. Empty list on any lookup
    error (or in the pathological case that none are active, which the
    migration's mandatory min_usd = 0 tier is there to prevent)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_RATES_TABLE)
            .select("*")
            .eq("is_active", True)
            .order("min_usd", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_reward_rates lookup failed: {e}")
        return []


def select_holdings_rate(rates: list[dict[str, Any]], usd_value: Decimal) -> dict[str, Any] | None:
    """The tier with the largest min_usd <= usd_value, or None when no tier
    covers the value (only reachable with a rates table misconfigured
    without its mandatory min_usd = 0 row).

    Same rule as src/services/staking_rewards.py::_select_rate, kept here
    beside the rate table so the tier boundary is defined and tested in one
    place rather than re-derived by each caller.
    """
    eligible = [r for r in rates if Decimal(str(r["min_usd"])) <= usd_value]
    if not eligible:
        return None
    return max(eligible, key=lambda r: Decimal(str(r["min_usd"])))


def get_holdings_accrual(wallet_address: str, reward_date: date) -> dict[str, Any] | None:
    """The accrual row for one (wallet_address, reward_date), or None if it
    doesn't exist yet (or on error). The caller uses this to decide whether
    this day has already been decided for this wallet -- the core of the
    job's idempotency."""
    day = _day_str(reward_date)
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("wallet_address", wallet_address.lower())
            .eq("reward_date", day)
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"holdings_reward_accruals lookup failed for {wallet_address}/{day}: {e}")
        return None


def create_holdings_accrual(
    wallet_address: str,
    reward_date: date,
    usd_basis: Decimal,
    credits: Decimal,
) -> dict[str, Any] | None:
    """Insert the day's accrual as 'pending', before any credit is granted.

    The status is not a parameter on purpose: a row must exist as pending
    first, so that a crash between "decided" and "paid" leaves a visible,
    retryable row rather than a silent loss or a second payment. Marking it
    paid is mark_holdings_accrual_paid()'s job.

    Returns the created row, or None on any failure -- including the
    (wallet_address, reward_date) UNIQUE conflict. Callers call
    get_holdings_accrual() first and only reach this when no row exists, so
    a conflict here means a concurrent run raced it; re-read via
    get_holdings_accrual() rather than treating None as a hard failure.
    """
    day = _day_str(reward_date)
    payload = {
        "wallet_address": wallet_address.lower(),
        "reward_date": day,
        "usd_basis": str(usd_basis),
        "credits": str(credits),
        "status": "pending",
        "ledger_request_id": None,
    }
    try:
        client = get_supabase_client()
        result = client.table(_ACCRUALS_TABLE).insert(payload).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"holdings_reward_accruals insert failed for {wallet_address}/{day}: {e}")
        return None


def mark_holdings_accrual_paid(accrual_id: int, ledger_request_id: str) -> dict[str, Any] | None:
    """Flip a pending accrual to 'paid', recording the credit ledger's
    request_id. That id is the second, independent idempotency guard (the
    partial unique index on credit_transactions.request_id), so it is
    stored even though the accrual's own unique index already bounds the
    row to one per wallet-day. Returns the updated row, or None on any
    failure."""
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
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"holdings_reward_accruals mark_paid failed for id={accrual_id}: {e}")
        return None


def list_pending_holdings_accruals_since(min_reward_date: date | str) -> list[dict[str, Any]]:
    """Every still-pending accrual on or after `min_reward_date`, across all
    wallets, oldest first.

    This is the daily job's retry sweep: an accrual left pending by a failed
    credit write, or by a wallet that was unlinked when the day was decided,
    would otherwise never be revisited (the pay-on-link hook only covers the
    link event itself). Bounded by a caller-supplied floor rather than
    unbounded, so the sweep's cost does not grow forever. Empty list on any
    lookup error.
    """
    day = _day_str(min_reward_date)
    try:
        client = get_supabase_client()
        result = (
            client.table(_ACCRUALS_TABLE)
            .select("*")
            .eq("status", "pending")
            .gte("reward_date", day)
            .order("reward_date", desc=False)
            .limit(_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"holdings_reward_accruals pending sweep failed since {day}: {e}")
        return []


def list_pending_holdings_accruals(wallet_address: str) -> list[dict[str, Any]]:
    """Every still-pending accrual for one wallet, oldest reward_date first
    (they are paid in order once the wallet is payable). Empty list on any
    lookup error."""
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
        logger.warning(f"holdings_reward_accruals pending lookup failed for {wallet_address}: {e}")
        return []


def get_usage_cost_for_window(user_id: int, since: datetime) -> Decimal:
    """What this account actually spent on inference since ``since``.

    Holdings rewards are capped by this (see
    :mod:`src.services.holdings.rewards`): the programme exists to turn token
    holders into customers, so a wallet that never runs inference earns
    nothing no matter how much it holds. Paying purely for a balance buys a
    screenshot, not a user.

    Reads ``usage_records.cost``, the same rows the status and billing
    reconciliation paths use. A lookup failure returns ``Decimal(0)``, which
    means "no earnings this run" rather than an unbounded grant -- failing
    closed is the right direction when the alternative is paying out on
    unknown data.
    """
    try:
        client = get_supabase_client()
        total = Decimal(0)
        offset = 0
        while True:
            rows = (
                client.table("usage_records")
                .select("cost")
                .eq("user_id", user_id)
                .gte("timestamp", since.isoformat())
                .range(offset, offset + 999)
                .execute()
                .data
                or []
            )
            for row in rows:
                total += Decimal(str(row.get("cost") or 0))
            if len(rows) < 1000:
                break
            offset += 1000
        return total
    except Exception as e:  # noqa: BLE001 - never break the payout run
        logger.warning(
            "holdings: usage lookup failed for user %s (%s) -- treating as no usage",
            user_id,
            type(e).__name__,
        )
        return Decimal(0)


def sum_holdings_credits_since(wallet_addresses: list[str], since_date: date) -> Decimal:
    """Holdings credits these wallets have already been granted (pending or
    paid) on or after ``since_date``.

    The usage allowance is a budget consumed over the lookback window, not a
    fresh ceiling each day. Without this subtraction a single $70 of spend
    would authorise $70 of credits on each of the next seven days -- the same
    spend claimed seven times. Counting pending as spent is deliberate: a
    pending accrual is credit already promised.

    Returns ``Decimal(0)`` for an empty wallet list. A lookup failure raises,
    because treating "unknown" as zero here would open the leak this closes.
    """
    addresses = [a.lower() for a in wallet_addresses if a]
    if not addresses:
        return Decimal(0)
    client = get_supabase_client()
    total = Decimal(0)
    offset = 0
    while True:
        rows = (
            client.table(_ACCRUALS_TABLE)
            .select("credits")
            .in_("wallet_address", addresses)
            .gte("reward_date", _day_str(since_date))
            .in_("status", ["pending", "paid"])
            .range(offset, offset + 999)
            .execute()
            .data
            or []
        )
        for row in rows:
            total += Decimal(str(row.get("credits") or 0))
        if len(rows) < 1000:
            break
        offset += 1000
    return total
