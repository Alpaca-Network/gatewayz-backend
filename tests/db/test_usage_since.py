#!/usr/bin/env python3
"""
Tests for the `since` usage aggregation backing GET /user/api-keys/usage?since=

Covers:
- Aggregation math (requests / tokens / cost) from activity_log rows
- Inclusive lower bound passed through to the query
- Empty window returns zeros, not None
- DB error returns None (failure is distinguishable from zero usage)
- `since` value normalization (ISO-8601, epoch seconds, epoch ms, invalid)
"""

from unittest.mock import Mock, patch

import pytest

from src.db.activity import get_user_usage_since
from src.routes.api_keys import _parse_since

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def sb():
    """In-memory Supabase stub (named `sb` per conftest convention —
    tests using it are exempt from the database-availability skip)."""
    client = Mock()
    table_mock = Mock()

    client.table.return_value = table_mock
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.gte.return_value = table_mock
    table_mock.execute.return_value = Mock(data=[])

    return client, table_mock


SINCE = "2026-08-20T00:00:00+00:00"


# ============================================================
# AGGREGATION
# ============================================================


class TestGetUserUsageSince:
    def test_aggregates_requests_tokens_and_cost(self, sb):
        client, table_mock = sb
        table_mock.execute.return_value = Mock(
            data=[
                {"tokens": 100, "cost": 0.001},
                {"tokens": 250, "cost": 0.0025},
                {"tokens": 0, "cost": 0.0},
            ]
        )
        with patch("src.db.activity.get_supabase_client", return_value=client):
            result = get_user_usage_since(123, SINCE)

        assert result["total_requests"] == 3
        assert result["total_tokens"] == 350
        assert result["total_cost_usd"] == pytest.approx(0.0035)
        assert result["from"] == SINCE

    def test_lower_bound_is_passed_to_query(self, sb):
        client, table_mock = sb
        with patch("src.db.activity.get_supabase_client", return_value=client):
            get_user_usage_since(123, SINCE)
        table_mock.gte.assert_called_once_with("timestamp", SINCE)
        table_mock.eq.assert_called_once_with("user_id", 123)

    def test_empty_window_returns_zeros_not_none(self, sb):
        client, _ = sb
        with patch("src.db.activity.get_supabase_client", return_value=client):
            result = get_user_usage_since(123, SINCE)
        assert result == {
            "from": SINCE,
            "to": result["to"],
            "total_requests": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }

    def test_null_fields_in_rows_are_tolerated(self, sb):
        client, table_mock = sb
        table_mock.execute.return_value = Mock(
            data=[{"tokens": None, "cost": None}, {"tokens": 5, "cost": 0.1}]
        )
        with patch("src.db.activity.get_supabase_client", return_value=client):
            result = get_user_usage_since(123, SINCE)
        assert result["total_tokens"] == 5
        assert result["total_cost_usd"] == pytest.approx(0.1)

    @pytest.mark.usefixtures("sb")
    def test_db_error_returns_none(self):
        with patch("src.db.activity.get_supabase_client", side_effect=RuntimeError("db down")):
            assert get_user_usage_since(123, SINCE) is None


# ============================================================
# SINCE VALUE NORMALIZATION
# ============================================================


@pytest.mark.usefixtures("sb")
class TestParseSince:
    def test_iso8601_with_z_suffix(self):
        assert _parse_since("2026-08-27T00:00:00Z") == "2026-08-27T00:00:00+00:00"

    def test_iso8601_naive_treated_as_utc(self):
        assert _parse_since("2026-08-27T12:30:00") == "2026-08-27T12:30:00+00:00"

    def test_epoch_seconds(self):
        assert _parse_since("1787580035") == "2026-08-24T14:00:35+00:00"

    def test_epoch_milliseconds(self):
        assert _parse_since("1787580035000") == "2026-08-24T14:00:35+00:00"

    @pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "", "1e99"])
    def test_invalid_values_raise(self, bad):
        with pytest.raises((ValueError, OverflowError, OSError)):
            _parse_since(bad)
