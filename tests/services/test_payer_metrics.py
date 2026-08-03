"""Tests for payer-cohort metrics.

These numbers go in a deck and get checked in diligence, so the tests are about
definitional correctness: a returning customer is never counted as new, an
empty denominator reports "no data" rather than zero, and growth from zero is
undefined rather than infinite.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.services.payer_metrics import (
    PayerMetricsUnavailable,
    _amount_usd,
    _wow_pct,
    apply_epoch,
    build_weekly_scorecard,
    compute_new_paying_accounts,
    compute_paying_accounts,
    compute_revenue,
    compute_second_topup_rate,
    fetch_settled_payments,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)
WEEK_AGO = NOW - timedelta(days=7)
TWO_WEEKS_AGO = NOW - timedelta(days=14)


def _payment(user_id, days_ago, amount_usd=10.0, status="succeeded"):
    return {
        "user_id": user_id,
        "amount_usd": amount_usd,
        "status": status,
        "created_at": (NOW - timedelta(days=days_ago)).isoformat(),
    }


class TestAmountNormalisation:
    def test_amount_usd_preferred(self):
        assert _amount_usd({"amount_usd": 25.0, "amount": 999}) == 25.0

    def test_cents_fallback_divided_by_100(self):
        # The column is amount_cents. This test previously asserted "amount",
        # which is why it passed while production reported zero payers.
        assert _amount_usd({"amount_cents": 2500}) == 25.0

    def test_missing_amount_is_zero(self):
        assert _amount_usd({}) == 0.0

    def test_garbage_amount_is_zero_not_an_exception(self):
        assert _amount_usd({"amount_usd": "lots"}) == 0.0


class TestPayingAccounts:
    def test_counts_distinct_users(self):
        payments = [_payment(1, 1), _payment(1, 2), _payment(2, 3)]
        assert compute_paying_accounts(payments) == {1, 2}

    def test_unsettled_payments_excluded(self):
        payments = [_payment(1, 1, status="pending"), _payment(2, 1, status="failed")]
        assert compute_paying_accounts(payments) == set()

    def test_any_amount_counts(self):
        """A $1 top-up makes someone a paying account."""
        assert compute_paying_accounts([_payment(1, 1, amount_usd=1.0)]) == {1}

    def test_empty_input(self):
        assert compute_paying_accounts([]) == set()


class TestNewPayingAccounts:
    def test_first_payment_inside_window_counts(self):
        payments = [_payment(1, 3)]
        assert compute_new_paying_accounts(payments, WEEK_AGO, NOW) == 1

    def test_returning_customer_is_not_new(self):
        """The metric that would otherwise turn retention into fake acquisition."""
        payments = [_payment(1, 10), _payment(1, 3)]
        assert compute_new_paying_accounts(payments, WEEK_AGO, NOW) == 0

    def test_customer_counted_in_the_week_of_their_first_payment(self):
        payments = [_payment(1, 10), _payment(1, 3)]
        assert compute_new_paying_accounts(payments, TWO_WEEKS_AGO, WEEK_AGO) == 1

    def test_pending_payment_does_not_make_someone_new(self):
        payments = [_payment(1, 3, status="pending")]
        assert compute_new_paying_accounts(payments, WEEK_AGO, NOW) == 0

    def test_rows_with_no_timestamp_are_skipped(self):
        payments = [{"user_id": 1, "amount_usd": 5, "status": "paid", "created_at": None}]
        assert compute_new_paying_accounts(payments, WEEK_AGO, NOW) == 0


class TestRevenue:
    def test_sums_settled_payments_in_window(self):
        payments = [_payment(1, 1, 10.0), _payment(2, 2, 15.0)]
        assert compute_revenue(payments, WEEK_AGO, NOW) == 25.0

    def test_excludes_payments_outside_window(self):
        payments = [_payment(1, 1, 10.0), _payment(2, 20, 500.0)]
        assert compute_revenue(payments, WEEK_AGO, NOW) == 10.0

    def test_excludes_unsettled(self):
        payments = [_payment(1, 1, 10.0, status="failed")]
        assert compute_revenue(payments, WEEK_AGO, NOW) == 0.0


class TestSecondTopupRate:
    def test_half_the_payers_returned(self):
        payments = [_payment(1, 1), _payment(1, 2), _payment(2, 1)]
        assert compute_second_topup_rate(payments) == 50.0

    def test_nobody_returned(self):
        payments = [_payment(1, 1), _payment(2, 1)]
        assert compute_second_topup_rate(payments) == 0.0

    def test_no_payers_returns_none_not_zero(self):
        """Empty denominator is 'no data', not '0% retention'."""
        assert compute_second_topup_rate([]) is None

    def test_all_payers_returned(self):
        payments = [_payment(1, 1), _payment(1, 2), _payment(2, 1), _payment(2, 3)]
        assert compute_second_topup_rate(payments) == 100.0

    def test_as_of_excludes_later_payments(self):
        payments = [_payment(1, 10), _payment(1, 1)]
        # As of a week ago, user 1 had only one payment.
        assert compute_second_topup_rate(payments, as_of=WEEK_AGO) == 0.0


class TestWowPct:
    def test_growth(self):
        assert _wow_pct(150, 100) == 50.0

    def test_decline(self):
        assert _wow_pct(50, 100) == -50.0

    def test_growth_from_zero_is_none_not_infinity(self):
        """'Up infinity percent' is not a number that belongs in a deck."""
        assert _wow_pct(10, 0) is None

    def test_flat(self):
        assert _wow_pct(100, 100) == 0.0


class TestWeeklyScorecard:
    def test_builds_from_injected_payments_without_a_database(self):
        payments = [_payment(1, 1, 20.0), _payment(2, 2, 30.0), _payment(1, 10, 10.0)]
        card = build_weekly_scorecard(as_of=NOW, payments=payments)
        assert card.credit_revenue_usd == 50.0
        assert card.total_paying_accounts == 2

    def test_new_accounts_exclude_the_returning_one(self):
        payments = [_payment(1, 10, 10.0), _payment(1, 1, 20.0), _payment(2, 2, 30.0)]
        card = build_weekly_scorecard(as_of=NOW, payments=payments)
        assert card.new_paying_accounts == 1

    def test_no_payments_adds_an_explanatory_note(self):
        """A bare zero reads as a bug; the note says where to look."""
        card = build_weekly_scorecard(as_of=NOW, payments=[])
        assert any("Stripe webhook" in n for n in card.notes)

    def test_undefined_wow_adds_a_note(self):
        payments = [_payment(1, 1, 20.0)]
        card = build_weekly_scorecard(as_of=NOW, payments=payments)
        assert card.new_paying_accounts_wow_pct is None
        assert any("undefined" in n for n in card.notes)

    def test_serializes_to_dict(self):
        card = build_weekly_scorecard(as_of=NOW, payments=[_payment(1, 1)])
        payload = card.to_dict()
        assert "new_paying_accounts" in payload
        assert "second_topup_rate_pct" in payload

    def test_second_topup_rate_reflected(self):
        payments = [_payment(1, 1), _payment(1, 2), _payment(2, 1)]
        card = build_weekly_scorecard(as_of=NOW, payments=payments)
        assert card.second_topup_rate_pct == 50.0


class TestEpochConsistency:
    """Both payer endpoints must apply the same METRICS_EPOCH cutoff.

    The scorecard applied it and the trend endpoint did not, so the two
    reported different totals for the same metric — exactly the disagreement
    docs/METRIC_DEFINITIONS.md exists to prevent, and the kind a diligence
    question surfaces at the worst possible moment.
    """

    def test_apply_epoch_is_a_no_op_when_unset(self, monkeypatch):
        monkeypatch.delenv("METRICS_EPOCH", raising=False)
        payments = [_payment(1, 1), _payment(2, 30)]
        filtered, note = apply_epoch(payments)
        assert filtered == payments
        assert note is None

    def test_apply_epoch_drops_older_rows_and_says_how_many(self, monkeypatch):
        monkeypatch.setenv("METRICS_EPOCH", (NOW - timedelta(days=5)).date().isoformat())
        payments = [_payment(1, 1), _payment(2, 30)]
        filtered, note = apply_epoch(payments)
        assert len(filtered) == 1
        assert "Excluded 1 payment" in note

    def test_unparseable_epoch_is_ignored_rather_than_dropping_everything(self, monkeypatch):
        """A typo in the env var must not silently zero every metric."""
        monkeypatch.setenv("METRICS_EPOCH", "not-a-date")
        payments = [_payment(1, 1), _payment(2, 30)]
        filtered, note = apply_epoch(payments)
        assert filtered == payments
        assert note is None


class TestSchemaAndFailureVisibility:
    """A broken query must never render as "no customers".

    The payments table column is `amount_cents`, not `amount`. Selecting the
    wrong name made PostgREST reject the query; the except-branch returned an
    empty list; and the scorecard reported 0 paying accounts for a business
    that had 8 payers and $123 of revenue. The number looked like a fact.
    """

    def test_amount_reads_the_real_cents_column(self):
        assert _amount_usd({"amount_cents": 900}) == 9.0

    def test_nonexistent_amount_column_is_not_used(self):
        """Guards the exact regression: `amount` must not be read as cents."""
        assert _amount_usd({"amount": 900}) == 0.0

    def test_amount_usd_still_preferred(self):
        assert _amount_usd({"amount_usd": 9.0, "amount_cents": 100}) == 9.0

    def test_fetch_failure_raises_rather_than_returning_empty(self):
        """An unreadable table must not look like an empty one."""
        with patch(
            "src.config.supabase_config.get_supabase_client",
            side_effect=RuntimeError("column does not exist"),
        ):
            with pytest.raises(PayerMetricsUnavailable, match="NOT the same as having no payers"):
                fetch_settled_payments()
