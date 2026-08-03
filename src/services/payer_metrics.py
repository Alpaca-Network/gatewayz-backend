"""Payer-cohort metrics -- the numbers the raise is built on.

The existing admin analytics are request-centric: volume by model, by provider,
by status. Useful for operating the gateway, useless for answering the only
four questions an investor asks about a usage-based business:

1. How many accounts actually paid?
2. How much did they pay, and is that number growing week over week?
3. Do they come back and pay again? (second top-up rate)
4. How much usage is flowing through?

None of those were queryable, so this module adds them. It reads payments and
usage directly rather than deriving from signups, because signup counts are the
metric that got contaminated in the first place and nothing here should depend
on them.

Definitions live in ``docs/METRIC_DEFINITIONS.md`` and are mirrored in the
docstrings below. Investors will ask what a "paying account" means; there needs
to be exactly one answer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Any

logger = logging.getLogger(__name__)

# Statuses that count as money actually received.
SETTLED_STATUSES = frozenset({"succeeded", "completed", "paid"})


def metrics_epoch() -> datetime | None:
    """Clean Day 0 for reported metrics.

    Everything before this date is excluded from every figure. Set
    ``METRICS_EPOCH`` (ISO date) to the day the payment gate and zeroed signup
    credits went live. The point is that a single dashboard cannot mix
    pre- and post-cleanup data — that mixing is what made the old numbers
    unusable, and a per-chart date filter is too easy to forget.

    Returns None when unset, in which case all history is included.
    """
    raw = os.getenv("METRICS_EPOCH")
    if not raw:
        return None
    parsed = _parse_ts(raw)
    if parsed is None:
        logger.warning("METRICS_EPOCH=%r is not a parseable date; ignoring it", raw)
    return parsed


def apply_epoch(payments: list[dict]) -> tuple[list[dict], str | None]:
    """Drop payments before the metrics epoch.

    Returns ``(filtered, note)`` where the note names how many rows were
    excluded — silently dropping data is how a dashboard ends up disagreeing
    with the database.
    """
    epoch = metrics_epoch()
    if epoch is None:
        return payments, None

    kept: list[dict] = []
    for payment in payments:
        ts = _parse_ts(payment.get("created_at"))
        if ts is None or ts >= epoch:
            kept.append(payment)

    dropped = len(payments) - len(kept)
    note = (
        f"Excluded {dropped} payment(s) before the metrics epoch "
        f"({epoch.date().isoformat()})."
        if dropped
        else None
    )
    return kept, note


@dataclass
class WeeklyScorecard:
    """One week of the metrics in the operating cadence (GTM plan section 6)."""

    week_start: str
    week_end: str
    new_paying_accounts: int = 0
    new_paying_accounts_wow_pct: float | None = None
    credit_revenue_usd: float = 0.0
    credit_revenue_wow_pct: float | None = None
    second_topup_rate_pct: float | None = None
    total_paying_accounts: int = 0
    tokens_through_gateway: int = 0
    top_referral_source: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_start": self.week_start,
            "week_end": self.week_end,
            "new_paying_accounts": self.new_paying_accounts,
            "new_paying_accounts_wow_pct": self.new_paying_accounts_wow_pct,
            "credit_revenue_usd": round(self.credit_revenue_usd, 2),
            "credit_revenue_wow_pct": self.credit_revenue_wow_pct,
            "second_topup_rate_pct": self.second_topup_rate_pct,
            "total_paying_accounts": self.total_paying_accounts,
            "tokens_through_gateway": self.tokens_through_gateway,
            "top_referral_source": self.top_referral_source,
            "notes": self.notes,
        }


def _amount_usd(payment: dict) -> float:
    """Dollars for a payment row (``amount_usd`` dollars, ``amount`` cents)."""
    usd = payment.get("amount_usd")
    if usd is not None:
        try:
            return float(usd)
        except (TypeError, ValueError):
            return 0.0
    cents = payment.get("amount")
    try:
        return float(cents) / 100.0 if cents is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _is_settled(payment: dict) -> bool:
    return str(payment.get("status", "")).lower() in SETTLED_STATUSES


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _wow_pct(current: float, previous: float) -> float | None:
    """Week-over-week percentage change.

    Returns None rather than infinity when the prior week was zero -- "up
    infinity percent from nothing" is not a number anyone should put in a deck.
    """
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def fetch_settled_payments(since: datetime | None = None) -> list[dict]:
    """All settled payments, optionally since a cutoff.

    Kept as its own function so the metric functions below can be tested
    against fixtures without a database.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        query = client.table("payments").select("user_id, amount_usd, amount, status, created_at")
        if since:
            query = query.gte("created_at", since.isoformat())
        result = query.execute()
        return [p for p in (result.data or []) if _is_settled(p)]
    except Exception as e:
        logger.error("Failed to fetch payments for payer metrics: %s", e)
        return []


def compute_paying_accounts(payments: list[dict]) -> set:
    """Accounts with at least one settled payment.

    **Definition.** A "paying account" is a user_id with >= 1 settled payment of
    any amount, ever. Not a signup, not a key holder, not a trial. This is the
    only definition used anywhere in the metrics.
    """
    return {p["user_id"] for p in payments if _is_settled(p) and p.get("user_id") is not None}


def compute_new_paying_accounts(payments: list[dict], window_start: datetime, window_end: datetime) -> int:
    """Accounts whose **first** settled payment falls inside the window.

    First-payment date, not any-payment date -- otherwise a returning customer
    would be counted as a new one and the growth curve would be a lie.
    """
    first_payment: dict[Any, datetime] = {}
    for payment in payments:
        if not _is_settled(payment):
            continue
        user_id = payment.get("user_id")
        ts = _parse_ts(payment.get("created_at"))
        if user_id is None or ts is None:
            continue
        if user_id not in first_payment or ts < first_payment[user_id]:
            first_payment[user_id] = ts

    return sum(1 for ts in first_payment.values() if window_start <= ts < window_end)


def compute_revenue(payments: list[dict], window_start: datetime, window_end: datetime) -> float:
    """Settled payment dollars inside the window."""
    total = 0.0
    for payment in payments:
        if not _is_settled(payment):
            continue
        ts = _parse_ts(payment.get("created_at"))
        if ts and window_start <= ts < window_end:
            total += _amount_usd(payment)
    return total


def compute_second_topup_rate(payments: list[dict], as_of: datetime | None = None) -> float | None:
    """Share of paying accounts that have topped up at least twice.

    **Definition.** Of all accounts with >= 1 settled payment, the percentage
    with >= 2 settled payments. This is the retention number in the GTM plan;
    the target is 40%+.

    Returns None when there are no payers at all, rather than 0% -- an empty
    denominator is "no data", not "nobody came back".
    """
    counts: dict[Any, int] = {}
    for payment in payments:
        if not _is_settled(payment):
            continue
        user_id = payment.get("user_id")
        if user_id is None:
            continue
        if as_of:
            ts = _parse_ts(payment.get("created_at"))
            if ts and ts > as_of:
                continue
        counts[user_id] = counts.get(user_id, 0) + 1

    if not counts:
        return None
    repeat = sum(1 for n in counts.values() if n >= 2)
    return round((repeat / len(counts)) * 100, 1)


def fetch_token_volume(window_start: datetime, window_end: datetime) -> int:
    """Total tokens served through the gateway inside the window."""
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        result = (
            client.table("chat_completion_requests")
            .select("input_tokens, output_tokens")
            .gte("created_at", window_start.isoformat())
            .lt("created_at", window_end.isoformat())
            .execute()
        )
        return sum(
            (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0)
            for row in (result.data or [])
        )
    except Exception as e:
        logger.error("Failed to fetch token volume: %s", e)
        return 0


def build_weekly_scorecard(
    as_of: datetime | None = None,
    payments: list[dict] | None = None,
) -> WeeklyScorecard:
    """Assemble the weekly scorecard from the GTM plan's operating cadence.

    Args:
        as_of: End of the reporting week (defaults to now, UTC).
        payments: Injected payment rows; fetched from the database when omitted.
    """
    end = as_of or datetime.now(UTC)
    week_start = end - timedelta(days=7)
    prior_start = end - timedelta(days=14)

    if payments is None:
        payments = fetch_settled_payments()

    payments, epoch_note = apply_epoch(payments)

    new_this_week = compute_new_paying_accounts(payments, week_start, end)
    new_prior_week = compute_new_paying_accounts(payments, prior_start, week_start)

    revenue_this_week = compute_revenue(payments, week_start, end)
    revenue_prior_week = compute_revenue(payments, prior_start, week_start)

    scorecard = WeeklyScorecard(
        week_start=week_start.isoformat(),
        week_end=end.isoformat(),
        new_paying_accounts=new_this_week,
        new_paying_accounts_wow_pct=_wow_pct(new_this_week, new_prior_week),
        credit_revenue_usd=revenue_this_week,
        credit_revenue_wow_pct=_wow_pct(revenue_this_week, revenue_prior_week),
        second_topup_rate_pct=compute_second_topup_rate(payments, as_of=end),
        total_paying_accounts=len(compute_paying_accounts(payments)),
        tokens_through_gateway=fetch_token_volume(week_start, end),
    )

    if epoch_note:
        scorecard.notes.append(epoch_note)

    # Surface the "growing from nothing" case explicitly instead of printing a
    # blank cell that reads as a bug.
    if scorecard.new_paying_accounts_wow_pct is None and new_this_week > 0:
        scorecard.notes.append(
            f"No paying accounts in the prior week, so WoW growth is undefined "
            f"({new_this_week} new this week)."
        )
    if not payments:
        scorecard.notes.append(
            "No settled payments found. If this is unexpected, check the payments "
            "table and the Stripe webhook before reporting a zero."
        )

    return scorecard
