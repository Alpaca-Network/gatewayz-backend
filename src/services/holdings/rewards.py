"""The daily holdings-rewards accrual.

We pay inference credits for tokens a user holds in a wallet they have
proven they control. This is non-custodial: we never take a deposit, we
only read balances, and we promise no return of any kind. The payout half
is `src/services/staking_rewards.py` reused verbatim -- the same
insert-pending-then-pay order, the same two independent idempotency guards
-- with only the "how much do you have" input swapped.

**The basis is the day's LOWEST observed value**, never an average and
never the latest sweep. That is the whole anti-farm rule: a wallet funded
for ten minutes and emptied again is worth its empty total, not its peak.

That rule only bites if the day actually has several readings. The
observation sweep drops a wallet's whole batch on an incomplete chain read
or a held token with no fresh price, so a wallet can legitimately end a day
with one recorded sweep -- and then "lowest of the day" degenerates into
"that one moment", which is the hole the rule exists to close. So a day
with fewer than `HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY` distinct sweeps is skipped
outright. Underpaying nobody is acceptable here; paying on a single
farmable reading is not.

Two ceilings apply, in this order:

* `HOLDINGS_DAILY_CAP_CREDITS`, **per account** -- every wallet an account
  has linked shares one daily ceiling, so splitting a balance across
  wallets cannot multiply it. A wallet that is not linked to an account
  when its day is decided has no account to charge the cap against, so it
  is capped on its own; once it links, the accrual is paid as recorded.
* `HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS`, across the run. Wallets are
  processed in an order derived from a hash of (reward date, address): the
  SAME order every time that date is processed, so a re-run pays exactly
  the same wallets, but a DIFFERENT order tomorrow, so no wallet is
  permanently advantaged by its address on the days the budget runs out.
  Accruals that already exist for the date consume budget before any new
  grant is considered, so re-running the job for one date can never hand
  out a second budget's worth.

Idempotent twice over, exactly like staking rewards: the accrual's UNIQUE
(wallet_address, reward_date) index, and the credit ledger's partial unique
index on `credit_transactions.request_id`.

Every wallet the run declines to pay is counted under its own reason in
`summary["skipped"]`, never collapsed into one number: when this
misbehaves in production, which reason grew is the whole diagnosis.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from src.config.config import Config
from src.db.credit_transactions import TransactionType
from src.db.holdings import (
    create_holdings_accrual,
    get_active_holdings_rates,
    get_holdings_accrual,
    get_snapshot_batch_totals,
    list_pending_holdings_accruals,
    list_pending_holdings_accruals_since,
    list_wallets_with_snapshots_for_date,
    mark_holdings_accrual_paid,
    select_holdings_rate,
)
from src.db.user_wallets import get_wallet, get_wallets_for_user
from src.db.users import add_credits_to_user

logger = logging.getLogger(__name__)

_CREDITS_DP = Decimal("0.000001")  # 6 dp, matches credits numeric(18,6)
_PENDING_RETRY_WINDOW_DAYS = 30


class HoldingsSnapshotsMissingError(Exception):
    """Raised when no wallet has a single observed balance for the reward
    date -- the observation sweep did not run, so there is nothing to pay
    on. Callers (the scheduled job, the admin run-now endpoint) turn this
    into a failed job run / a 409, never a crash."""


def _today() -> date:
    """Indirection so tests can pin "today" without patching datetime."""
    return datetime.now(UTC).date()


def _day_str(value: date | str) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _quantize(credits: Decimal) -> Decimal:
    return credits.quantize(_CREDITS_DP, rounding=ROUND_DOWN)


def _credits_for(usd_basis: Decimal, rate: dict[str, Any]) -> Decimal:
    per_1k = Decimal(str(rate["credits_per_1k_usd_per_day"]))
    return _quantize((usd_basis / Decimal(1000)) * per_1k)


def _decimal(value: Any, default: Decimal = Decimal(0)) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _request_id(wallet_address: str, reward_date_str: str) -> str:
    return f"holdings_reward:{wallet_address}:{reward_date_str}"


def _daily_cap() -> Decimal:
    return _quantize(_decimal(Config.HOLDINGS_DAILY_CAP_CREDITS))


def _required_batches() -> int:
    """How many distinct sweeps a day must have before it can be paid.

    Clamped to [1, HOLDINGS_SNAPSHOTS_PER_DAY]: requiring more sweeps than
    the schedule ever takes would silently pay nobody, which is a
    misconfiguration that should not be able to turn the feature off by
    accident.
    """
    per_day = max(1, int(Config.HOLDINGS_SNAPSHOTS_PER_DAY or 1))
    return max(1, min(int(Config.HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY or 1), per_day))


def budget_order(wallets: list[str], reward_date_str: str) -> list[str]:
    """The order wallets are considered in when the global budget is scarce.

    Sorting by address alone is deterministic but permanently unfair: the
    same low addresses would win every single day the budget runs out.
    Seeding the sort with the reward date instead rotates the order daily
    while keeping it fully reproducible for any given date -- a re-run of
    one date pays exactly the wallets the first run paid.

    The address is included as a tiebreaker so two wallets that hash alike
    still order stably.
    """

    def key(address: str) -> tuple[bytes, str]:
        digest = hashlib.sha256(f"{reward_date_str}:{address}".encode()).digest()
        return digest, address

    return sorted(wallets, key=key)


def _account_headroom(user_id: int, reward_date_str: str, this_wallet: str) -> Decimal:
    """Credits this account may still earn on `reward_date_str`, after the
    accruals its OTHER wallets already hold for that day.

    holdings_reward_accruals has no user_id column (it is keyed by wallet,
    because a wallet can accrue before it belongs to anyone), so the
    account's day is assembled from its wallets rather than queried
    directly. Accounts have a handful of wallets, so this is a handful of
    point lookups, not a scan.
    """
    spent = Decimal(0)
    for wallet in get_wallets_for_user(user_id):
        address = str(wallet.get("wallet_address") or "").lower()
        if not address or address == this_wallet:
            continue
        existing = get_holdings_accrual(address, reward_date_str)
        if existing and existing.get("status") in ("pending", "paid"):
            spent += _decimal(existing.get("credits"))
    return _daily_cap() - spent


def _resolve_user_id(wallet_address: str) -> int | None:
    """The account a wallet belongs to, or None when it is not linked (or
    the link row says the wallet is no longer active)."""
    row = get_wallet(wallet_address)
    if row is None or row.get("is_active") is False:
        return None
    return row.get("user_id")


def _pay_or_leave_pending(
    accrual: dict[str, Any], user_id: int | None = None
) -> tuple[str, Decimal]:
    """Pay one pending accrual if its wallet belongs to an account;
    otherwise leave it pending for a later run or for the pay-on-link hook.

    Returns (outcome, credits_paid) where outcome is 'paid' or 'pending'.
    Never raises -- a failed credit write is logged and reported as
    'pending' so the caller's per-wallet loop keeps going. The accrual row
    already exists and stays pending, which is exactly the retryable state
    we want.
    """
    wallet_address = str(accrual["wallet_address"]).lower()
    reward_date_str = _day_str(accrual["reward_date"])

    if user_id is None:
        user_id = _resolve_user_id(wallet_address)
    if user_id is None:
        return "pending", Decimal(0)

    credits = _decimal(accrual.get("credits"))
    if credits <= 0:
        return "pending", Decimal(0)

    request_id = _request_id(wallet_address, reward_date_str)
    try:
        add_credits_to_user(
            user_id=user_id,
            credits=float(credits),
            transaction_type=TransactionType.HOLDINGS_REWARD,
            description=f"Holdings reward for {reward_date_str}",
            metadata={
                "wallet": wallet_address,
                "reward_date": reward_date_str,
                "usd_basis": str(accrual.get("usd_basis")),
            },
            request_id=request_id,
        )
    except Exception as e:  # noqa: BLE001 - one failed grant must not sink the run
        logger.warning(
            "holdings_rewards: credit write failed for %s/%s: %s",
            wallet_address,
            reward_date_str,
            e,
        )
        return "pending", Decimal(0)

    mark_holdings_accrual_paid(accrual["id"], request_id)
    return "paid", credits


def run_holdings_rewards_once(reward_date: date | None = None) -> dict[str, Any]:
    """Run one pass of the daily accrual for `reward_date` (default:
    yesterday UTC).

    Idempotent -- see the module docstring. No-ops with
    ``{"skipped": "disabled"}`` when ``HOLDINGS_REWARDS_ENABLED`` is off.
    Raises :class:`HoldingsSnapshotsMissingError` when the date has no
    observed balances at all.
    """
    started = datetime.now(UTC)

    if not Config.HOLDINGS_REWARDS_ENABLED:
        return {"skipped": "disabled"}

    effective_date = reward_date or (_today() - timedelta(days=1))
    reward_date_str = effective_date.isoformat()

    wallets = list_wallets_with_snapshots_for_date(effective_date)
    if not wallets:
        raise HoldingsSnapshotsMissingError(f"no observed balances for {reward_date_str}")

    rates = get_active_holdings_rates()
    cap = _daily_cap()
    budget = _decimal(Config.HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS)
    required_batches = _required_batches()

    committed = Decimal(0)  # credits this date already owes, existing + new
    credits_paid = Decimal(0)
    counts = {"paid": 0, "pending": 0, "already": 0, "errors": 0}
    # Each reason is counted separately and never folded together: when a
    # run pays less than expected, WHICH reason grew is the diagnosis.
    skipped = {
        "no_snapshots": 0,
        "too_few_batches": 0,
        "no_rate_tier": 0,
        "zero_credits": 0,
        "budget_exhausted": 0,
    }
    capped = 0
    budget_exhausted = False

    for address in budget_order(wallets, reward_date_str):
        try:
            existing = get_holdings_accrual(address, reward_date_str)

            if existing is not None:
                # An accrual already decided for this wallet-day. Its credits
                # are owed whatever happens next, so they consume budget
                # before any new grant is considered -- that is what stops a
                # re-run spending a second budget.
                committed += _decimal(existing.get("credits"))
                if existing.get("status") != "pending":
                    counts["already"] += 1
                    continue
                outcome, paid = _pay_or_leave_pending(existing)
                counts[outcome] += 1
                credits_paid += paid
                continue

            batches = get_snapshot_batch_totals(address, effective_date)
            if not batches:
                skipped["no_snapshots"] += 1
                continue

            if len(batches) < required_batches:
                # "Lowest of the day" is only an anti-farm rule when the day
                # has several readings. With fewer, the minimum is just one
                # moment -- which is exactly what the rule exists to defeat,
                # so pay nothing rather than pay on a farmable reading.
                skipped["too_few_batches"] += 1
                logger.info(
                    "holdings_rewards: skipping %s for %s -- %s observed sweep(s), %s required",
                    address,
                    reward_date_str,
                    len(batches),
                    required_batches,
                )
                continue

            basis = min(batches.values())

            rate = select_holdings_rate(rates, basis)
            if rate is None:
                logger.warning("holdings_rewards: no active tier covers $%s (%s)", basis, address)
                skipped["no_rate_tier"] += 1
                continue

            credits = _credits_for(basis, rate)
            was_capped = False

            user_id = _resolve_user_id(address)
            ceiling = _account_headroom(user_id, reward_date_str, address) if user_id else cap
            if credits > ceiling:
                credits = _quantize(max(ceiling, Decimal(0)))
                was_capped = True

            if credits <= 0:
                skipped["zero_credits"] += 1
                continue

            if budget_exhausted or committed + credits > budget:
                # Once one wallet does not fit, stop granting entirely rather
                # than letting smaller wallets leapfrog it -- which wallets
                # get paid must not depend on how much budget happened to be
                # left. The wallet is still fully evaluated first, above, so
                # one that would have been skipped for its own reason is
                # counted under that reason and does not inflate this one.
                if not budget_exhausted:
                    budget_exhausted = True
                    logger.warning(
                        "holdings_rewards: global daily budget of %s credits exhausted at %s",
                        budget,
                        address,
                    )
                skipped["budget_exhausted"] += 1
                continue

            created = create_holdings_accrual(address, effective_date, basis, credits)
            if created is None:
                # Lost a race with a concurrent run -- re-read rather than
                # assume anything about what the winner decided.
                created = get_holdings_accrual(address, reward_date_str)
                if created is None:
                    counts["errors"] += 1
                    continue
                committed += _decimal(created.get("credits"))
                if created.get("status") != "pending":
                    counts["already"] += 1
                    continue
            else:
                committed += credits
                if was_capped:
                    capped += 1

            outcome, paid = _pay_or_leave_pending(created, user_id=user_id)
            counts[outcome] += 1
            credits_paid += paid

        except Exception as e:  # noqa: BLE001 - one bad wallet must not sink the run
            counts["errors"] += 1
            logger.warning("holdings_rewards: wallet %s failed: %s", address, e)

    retried_paid, retried_credits = _sweep_pending(effective_date, reward_date_str)
    credits_paid += retried_credits

    return {
        "reward_date": reward_date_str,
        "wallets": len(wallets),
        "paid": counts["paid"],
        "pending": counts["pending"],
        "already": counts["already"],
        "errors": counts["errors"],
        "capped": capped,
        # Broken out by reason, never summed into one number -- see the
        # module docstring. skipped_total is the convenience sum.
        "skipped": dict(skipped),
        "skipped_total": sum(skipped.values()),
        # Duplicated out of `skipped` because the budget is the one ceiling
        # an operator watches directly, and it is the number the scheduler
        # logs on every run.
        "budget_skipped": skipped["budget_exhausted"],
        "budget_exhausted": budget_exhausted,
        "required_batches": required_batches,
        "credits_paid": str(credits_paid),
        "retried_paid": retried_paid,
        "duration": (datetime.now(UTC) - started).total_seconds(),
    }


def _sweep_pending(effective_date: date, reward_date_str: str) -> tuple[int, Decimal]:
    """Retry pending accruals from earlier days (bounded to the last 30),
    excluding the date this run just handled. Covers a previously failed
    credit write and a wallet that has linked since its day was decided.

    These are already-decided credits, so they are outside the run's budget
    -- the budget governs what a run may newly grant, not what it already
    owes.
    """
    floor = (effective_date - timedelta(days=_PENDING_RETRY_WINDOW_DAYS)).isoformat()
    paid_count = 0
    paid_credits = Decimal(0)
    for accrual in list_pending_holdings_accruals_since(floor):
        if _day_str(accrual.get("reward_date")) == reward_date_str:
            continue
        try:
            outcome, credits = _pay_or_leave_pending(accrual)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "holdings_rewards: retry failed for accrual id=%s: %s", accrual.get("id"), e
            )
            continue
        if outcome == "paid":
            paid_count += 1
            paid_credits += credits
    return paid_count, paid_credits


def pay_pending_holdings_for_wallet(address: str, user_id: int) -> None:
    """Pay any pending accruals for a wallet that has just been linked to an
    account. Called inline from the wallet-link request path, so it must
    never raise regardless of what goes wrong here."""
    if not Config.HOLDINGS_REWARDS_ENABLED:
        return
    try:
        for accrual in list_pending_holdings_accruals(address):
            try:
                _pay_or_leave_pending(accrual, user_id=user_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "pay_pending_holdings_for_wallet: failed for accrual id=%s: %s",
                    accrual.get("id"),
                    e,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("pay_pending_holdings_for_wallet failed for %s: %s", address, e)


def rate_table_view(rates: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """The active tier table as API-shaped {min_usd,
    credits_per_1k_usd_per_day} string pairs (numbers-as-strings, per API
    convention). Fetches the active tiers itself when not given."""
    rates = rates if rates is not None else get_active_holdings_rates()
    return [
        {
            "min_usd": str(r["min_usd"]),
            "credits_per_1k_usd_per_day": str(r["credits_per_1k_usd_per_day"]),
        }
        for r in rates
    ]


def estimate_daily_credits(
    usd_value: Decimal, rates: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    """What a wallet at `usd_value` would earn in a day.

    `estimated_credits_per_day` is capped at HOLDINGS_DAILY_CAP_CREDITS,
    because that ceiling always applies to what is actually paid and a
    number the holder cannot reach is not an estimate. The uncapped figure
    is returned alongside it so a caller can show the ceiling is biting.
    """
    rates = rates if rates is not None else get_active_holdings_rates()
    rate = select_holdings_rate(rates, usd_value)
    if rate is None:
        return {
            "estimated_credits_per_day": "0",
            "uncapped_credits_per_day": "0",
            "rate_credits_per_1k_usd": "0",
            "min_usd": "0",
        }
    uncapped = _credits_for(usd_value, rate)
    return {
        "estimated_credits_per_day": str(min(uncapped, _daily_cap())),
        "uncapped_credits_per_day": str(uncapped),
        "rate_credits_per_1k_usd": str(rate["credits_per_1k_usd_per_day"]),
        "min_usd": str(rate["min_usd"]),
    }
