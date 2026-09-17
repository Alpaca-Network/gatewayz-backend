"""Tests for src/db/coupons.py.

Lives under tests/unit/ rather than tests/db/ on purpose: tests/conftest.py
skips anything whose path contains "db" when no live database is reachable,
and every test here mocks the Supabase client outright, so there is nothing
to skip. The filename avoids the substring "db" for the same reason.

The two behaviours worth pinning are both incident-driven:

* reads raise instead of returning an empty list, so a broken query cannot
  render as "no coupons";
* redemption totals are paged explicitly, because PostgREST caps a response
  at 1000 rows and aggregate functions are disabled on this project
  (verified: PGRST123 "Use of aggregate functions is not allowed"), so a
  single select would silently sum an arbitrary slice.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db.coupons import (
    COUPON_COLUMNS,
    MAX_REDEMPTION_SCAN,
    RedemptionScanTooLarge,
    _sanitize_search,
    count_redemptions,
    get_coupon,
    get_coupon_by_code,
    get_coupon_counts,
    get_redemption_stats,
    list_coupons,
    update_coupon,
)

# public.coupons as it exists in prod (ynleroehyrmaafkgjgmr, 2026-09-16).
# Pulled from the live schema, not written from memory -- the embedded-column
# guard in tests/schema/test_embedded_models_columns.py exists because a
# hand-written list threw five false positives.
PROD_COUPON_COLUMNS = {
    "id",
    "code",
    "description",
    "coupon_type",
    "coupon_scope",
    "value_usd",
    "assigned_to_user_id",
    "max_uses",
    "times_used",
    "valid_from",
    "valid_until",
    "is_active",
    "created_by",
    "created_by_type",
    "created_at",
    "updated_at",
}


def _result(data=None, count=None):
    return SimpleNamespace(data=data if data is not None else [], count=count)


def _client(execute_returns):
    """A Supabase client whose builder chain is fluent and whose execute()
    returns the given value (or walks the given list across calls)."""
    query = MagicMock()
    for method in (
        "select",
        "eq",
        "neq",
        "or_",
        "ilike",
        "order",
        "range",
        "limit",
        "insert",
        "update",
        "delete",
    ):
        getattr(query, method).return_value = query

    if isinstance(execute_returns, list):
        query.execute.side_effect = execute_returns
    else:
        query.execute.return_value = execute_returns

    client = MagicMock()
    client.table.return_value = query
    return client, query


class TestColumnList:
    def test_selects_only_columns_that_exist_in_prod(self):
        """Seven production outages came from selecting a column the table
        does not have. Every name in COUPON_COLUMNS must be real."""
        selected = {c.strip() for c in COUPON_COLUMNS.split(",")}
        assert selected == PROD_COUPON_COLUMNS

    def test_never_selects_star(self):
        assert "*" not in COUPON_COLUMNS


class TestSanitizeSearch:
    def test_plain_term_survives(self):
        assert _sanitize_search("welcome 50") == "welcome 50"

    @pytest.mark.parametrize(
        "raw",
        [
            "a,is_active.eq.false",  # a second or= term
            "a)or(id.gt.0",  # closing the or= group
            "a*b",  # the ilike wildcard
            'a"b',
        ],
    )
    def test_filter_syntax_is_stripped(self, raw):
        cleaned = _sanitize_search(raw)
        assert not any(ch in cleaned for ch in ',()*"')

    def test_length_capped(self):
        assert len(_sanitize_search("x" * 500)) == 100


class TestListCoupons:
    def test_returns_rows_and_exact_total(self):
        client, query = _client(_result([{"id": 1}], count=42))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            rows, total = list_coupons(limit=10, offset=0)
        assert rows == [{"id": 1}]
        assert total == 42
        query.select.assert_called_once_with(COUPON_COLUMNS, count="exact")

    def test_filters_applied(self):
        client, query = _client(_result([], count=0))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            list_coupons(scope="global", coupon_type="referral", is_active=True, search="gate")

        eq_calls = {args for args, _ in query.eq.call_args_list}
        assert ("coupon_scope", "global") in eq_calls
        assert ("coupon_type", "referral") in eq_calls
        assert ("is_active", True) in eq_calls
        query.or_.assert_called_once_with("code.ilike.*gate*,description.ilike.*gate*")

    def test_blank_search_adds_no_filter(self):
        client, query = _client(_result([], count=0))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            list_coupons(search="   ")
        query.or_.assert_not_called()

    def test_pagination_range_is_inclusive(self):
        client, query = _client(_result([], count=0))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            list_coupons(limit=25, offset=50)
        query.range.assert_called_once_with(50, 74)

    def test_db_failure_raises_instead_of_returning_empty(self):
        client, query = _client(_result())
        query.execute.side_effect = Exception("42703")
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(Exception, match="42703"):
                list_coupons()


class TestGetCoupon:
    def test_returns_row(self):
        client, _ = _client(_result([{"id": 19}]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            assert get_coupon(19) == {"id": 19}

    def test_missing_row_is_none(self):
        client, _ = _client(_result([]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            assert get_coupon(19) is None

    def test_db_failure_raises_rather_than_looking_like_a_404(self):
        client, query = _client(_result())
        query.execute.side_effect = Exception("boom")
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(Exception, match="boom"):
                get_coupon(19)


class TestGetCouponByCode:
    def test_matches_case_insensitively(self):
        client, query = _client(_result([{"id": 19}]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            get_coupon_by_code("GATEWAYZ")
        query.ilike.assert_called_once_with("code", "GATEWAYZ")

    def test_excludes_self_on_rename(self):
        client, query = _client(_result([]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            get_coupon_by_code("GATEWAYZ", exclude_id=19)
        query.neq.assert_called_once_with("id", 19)


class TestUpdateCoupon:
    def test_stamps_updated_at(self):
        client, query = _client(_result([{"id": 19}]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            update_coupon(19, {"is_active": False})
        payload = query.update.call_args.args[0]
        assert payload["is_active"] is False
        assert "updated_at" in payload

    def test_empty_patch_is_a_read(self):
        client, query = _client(_result([{"id": 19}]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            assert update_coupon(19, {}) == {"id": 19}
        query.update.assert_not_called()


class TestCountRedemptions:
    def test_uses_head_count_not_a_row_fetch(self):
        """count="exact", head=True is what makes the number immune to
        PostgREST's 1000-row ceiling."""
        client, query = _client(_result([], count=1234))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            assert count_redemptions(19) == 1234
        query.select.assert_called_once_with("id", count="exact", head=True)

    def test_raises_on_failure(self):
        client, query = _client(_result())
        query.execute.side_effect = Exception("boom")
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(Exception, match="boom"):
                count_redemptions(19)


class TestRedemptionStats:
    def test_aggregates_a_single_short_page(self):
        rows = [
            {"user_id": 1, "value_applied": 20.0},
            {"user_id": 1, "value_applied": 5.0},
            {"user_id": 2, "value_applied": 15.0},
        ]
        client, _ = _client(_result(rows))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            stats = get_redemption_stats(19)
        assert stats == {
            "total_redemptions": 3,
            "unique_users": 2,
            "total_value_distributed": 40.0,
        }

    def test_pages_past_the_1000_row_ceiling(self):
        """A full page must not be mistaken for the whole table -- that is
        the exact shape of the /plot-data defect."""
        full_page = [{"user_id": i, "value_applied": 1.0} for i in range(1000)]
        tail = [{"user_id": 10_000, "value_applied": 2.0}]
        client, query = _client([_result(full_page), _result(tail)])
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            stats = get_redemption_stats(19)

        assert stats["total_redemptions"] == 1001
        assert stats["total_value_distributed"] == 1002.0
        assert query.range.call_args_list[0].args == (0, 999)
        assert query.range.call_args_list[1].args == (1000, 1999)

    def test_oversized_scan_raises_rather_than_under_reporting(self):
        pages = [_result([{"user_id": 1, "value_applied": 1.0}] * 1000)] * (
            MAX_REDEMPTION_SCAN // 1000 + 1
        )
        client, _ = _client(pages)
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(RedemptionScanTooLarge):
                get_redemption_stats(None)

    def test_empty_ledger_is_zero_not_an_error(self):
        client, _ = _client(_result([]))
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            stats = get_redemption_stats(19)
        assert stats["total_redemptions"] == 0
        assert stats["total_value_distributed"] == 0.0

    def test_db_failure_raises(self):
        client, query = _client(_result())
        query.execute.side_effect = Exception("42703")
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(Exception, match="42703"):
                get_redemption_stats(19)


class TestCouponCounts:
    def test_four_exact_counts(self):
        client, query = _client(
            [_result(count=3), _result(count=2), _result(count=2), _result(count=1)]
        )
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            counts = get_coupon_counts()
        assert counts == {
            "total_coupons": 3,
            "active_coupons": 2,
            "global_coupons": 2,
            "user_specific_coupons": 1,
        }
        query.select.assert_called_with("id", count="exact", head=True)

    def test_db_failure_raises_instead_of_reporting_zeroes(self):
        client, query = _client(_result())
        query.execute.side_effect = Exception("42703")
        with patch("src.db.coupons.get_supabase_client", return_value=client):
            with pytest.raises(Exception, match="42703"):
                get_coupon_counts()
