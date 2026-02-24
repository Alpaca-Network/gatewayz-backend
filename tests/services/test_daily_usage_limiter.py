"""
Tests for Daily Usage Limiter Service
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.config.usage_limits import DAILY_USAGE_LIMIT
from src.services.daily_usage_limiter import (
    DailyUsageLimitExceeded,
    check_daily_usage_limit,
    enforce_daily_usage_limit,
    get_daily_reset_time,
    get_daily_usage,
)


class TestDailyResetTime:
    """Test daily reset time calculation"""

    def test_get_daily_reset_time_future(self):
        """Test that reset time is always in the future"""
        reset_time = get_daily_reset_time()
        now = datetime.now(UTC)

        assert reset_time > now
        assert reset_time.hour == 0
        assert reset_time.minute == 0
        assert reset_time.second == 0

    def test_get_daily_reset_time_tomorrow_if_past_midnight(self):
        """Test that reset time is tomorrow if we're past midnight"""
        with patch("src.services.daily_usage_limiter.datetime") as mock_datetime:
            # Mock current time as 1 AM UTC
            mock_now = datetime(2026, 1, 5, 1, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = datetime

            reset_time = get_daily_reset_time()

            # Should be tomorrow at midnight
            assert reset_time.day == 6
            assert reset_time.hour == 0


class TestDailyUsageTracking:
    """Test daily usage tracking"""

    @patch("src.services.daily_usage_limiter.get_supabase_client")
    def test_get_daily_usage_no_transactions(self, mock_client):
        """Test get_daily_usage when user has no transactions"""
        mock_result = Mock()
        mock_result.data = []

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value = (
            mock_result
        )

        mock_client.return_value.table.return_value = mock_table

        usage = get_daily_usage(user_id=123)

        assert usage == 0.0

    @patch("src.services.daily_usage_limiter.get_supabase_client")
    def test_get_daily_usage_with_transactions(self, mock_client):
        """Test get_daily_usage with multiple transactions"""
        mock_result = Mock()
        mock_result.data = [
            {"amount": -0.25},
            {"amount": -0.30},
            {"amount": -0.15},
        ]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value = (
            mock_result
        )

        mock_client.return_value.table.return_value = mock_table

        usage = get_daily_usage(user_id=123)

        assert usage == 0.70  # 0.25 + 0.30 + 0.15

    @patch("src.services.daily_usage_limiter.get_supabase_client")
    def test_get_daily_usage_error_handling(self, mock_client):
        """Test that errors are handled gracefully"""
        mock_client.side_effect = Exception("Database error")

        usage = get_daily_usage(user_id=123)

        # Should fail open and return 0
        assert usage == 0.0


class TestDailyUsageLimitCheck:
    """Test daily usage limit checking"""

    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_daily_usage_limit_within_limit(self, mock_get_usage):
        """Test check when usage is within limit"""
        mock_get_usage.return_value = 0.50  # $0.50 used

        result = check_daily_usage_limit(user_id=123, requested_amount=0.30)

        assert result["allowed"] is True
        assert result["used"] == 0.50
        assert result["remaining"] == 0.50  # $1.00 - $0.50
        assert result["limit"] == DAILY_USAGE_LIMIT
        assert result["warning_level"] == "ok"

    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_daily_usage_limit_would_exceed(self, mock_get_usage):
        """Test check when request would exceed limit"""
        mock_get_usage.return_value = 0.90  # $0.90 used

        result = check_daily_usage_limit(user_id=123, requested_amount=0.20)

        assert result["allowed"] is False
        assert result["used"] == 0.90
        assert result["remaining"] == 0.10
        assert result["warning_level"] in ("critical", "exceeded")

    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_daily_usage_limit_warning_threshold(self, mock_get_usage):
        """Test warning level at 80% usage"""
        mock_get_usage.return_value = 0.80  # Exactly at warning threshold

        result = check_daily_usage_limit(user_id=123, requested_amount=0.01)

        assert result["allowed"] is True
        assert result["warning_level"] in ("warning", "critical")

    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_daily_usage_limit_critical_threshold(self, mock_get_usage):
        """Test warning level at 95% usage"""
        mock_get_usage.return_value = 0.95  # At critical threshold

        result = check_daily_usage_limit(user_id=123, requested_amount=0.01)

        assert result["allowed"] is True
        assert result["warning_level"] == "critical"

    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_daily_usage_limit_exceeded(self, mock_get_usage):
        """Test when limit is already exceeded"""
        mock_get_usage.return_value = 1.10  # Already over limit

        result = check_daily_usage_limit(user_id=123, requested_amount=0.01)

        assert result["allowed"] is False
        assert result["warning_level"] == "exceeded"
        assert result["remaining"] == 0


class TestEnforceDailyUsageLimit:
    """Test daily usage limit enforcement"""

    @patch("src.services.daily_usage_limiter.check_daily_usage_limit")
    def test_enforce_daily_usage_limit_allowed(self, mock_check):
        """Test that enforcement passes when within limit"""
        mock_check.return_value = {
            "allowed": True,
            "used": 0.50,
            "remaining": 0.50,
            "limit": 1.0,
            "reset_time": datetime.now(UTC),
            "warning_level": "ok",
        }

        # Should not raise exception
        enforce_daily_usage_limit(user_id=123, requested_amount=0.30)

    @patch("src.services.daily_usage_limiter.check_daily_usage_limit")
    def test_enforce_daily_usage_limit_exceeded_raises_exception(self, mock_check):
        """Test that enforcement raises exception when limit exceeded"""
        reset_time = datetime.now(UTC) + timedelta(hours=5)
        mock_check.return_value = {
            "allowed": False,
            "used": 0.95,
            "remaining": 0.05,
            "limit": 1.0,
            "reset_time": reset_time,
            "warning_level": "exceeded",
        }

        with pytest.raises(DailyUsageLimitExceeded) as exc_info:
            enforce_daily_usage_limit(user_id=123, requested_amount=0.20)

        error_msg = str(exc_info.value)
        assert "Daily usage limit exceeded" in error_msg
        assert "Used: $0.95" in error_msg
        assert "Limit: $1.00" in error_msg


class TestDailyUsageLimitIntegration:
    """Integration tests for daily usage limiter"""

    @patch("src.services.daily_usage_limiter.get_supabase_client")
    def test_full_workflow_within_limit(self, mock_client):
        """Test full workflow when staying within limit"""
        # Mock transactions showing $0.60 used
        mock_result = Mock()
        mock_result.data = [
            {"amount": -0.30},
            {"amount": -0.30},
        ]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value = (
            mock_result
        )
        mock_client.return_value.table.return_value = mock_table

        # Check if $0.25 request is allowed
        result = check_daily_usage_limit(user_id=123, requested_amount=0.25)

        assert result["allowed"] is True
        assert result["used"] == 0.60
        assert result["remaining"] == 0.40

    @patch("src.services.daily_usage_limiter.get_supabase_client")
    def test_full_workflow_exceeds_limit(self, mock_client):
        """Test full workflow when exceeding limit"""
        # Mock transactions showing $0.95 used
        mock_result = Mock()
        mock_result.data = [
            {"amount": -0.45},
            {"amount": -0.50},
        ]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value = (
            mock_result
        )
        mock_client.return_value.table.return_value = mock_table

        # Try to deduct $0.10 (would be $1.05 total)
        with pytest.raises(DailyUsageLimitExceeded):
            enforce_daily_usage_limit(user_id=123, requested_amount=0.10)


class TestDisabledLimits:
    """Test behavior when limits are disabled"""

    @patch("src.config.usage_limits.ENFORCE_DAILY_LIMITS", False)
    @patch("src.services.daily_usage_limiter.get_daily_usage")
    def test_check_with_limits_disabled(self, mock_get_usage):
        """Test that checks pass when limits are disabled"""
        # Even with high usage, should allow if enforcement is disabled
        mock_get_usage.return_value = 50.0

        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", False):
            result = check_daily_usage_limit(user_id=123, requested_amount=10.0)

            assert result["allowed"] is True
            assert result["limit"] == float("inf")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
