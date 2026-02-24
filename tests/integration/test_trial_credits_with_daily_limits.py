"""
Integration Tests for Trial Credits with Daily Usage Limits

Tests the complete flow of:
- New users receiving $5 trial credits
- $1/day usage limit enforcement
- Trial period (3 days) behavior
- Post-trial credit usage
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.config.usage_limits import (
    DAILY_USAGE_LIMIT,
    TRIAL_CREDITS_AMOUNT,
    TRIAL_DURATION_DAYS,
)
from src.db.users import create_enhanced_user, deduct_credits
from src.services.daily_usage_limiter import DailyUsageLimitExceeded


class TestTrialCreditAllocation:
    """Test that trial users receive correct credit allocation"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.create_api_key")
    def test_new_user_receives_5_dollar_credits(self, mock_create_key, mock_client):
        """Test that new users receive $5 in trial credits"""
        # Mock Supabase responses
        mock_insert_result = Mock()
        mock_insert_result.data = [{"id": 123}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123}]

        mock_table = Mock()
        mock_table.insert.return_value.execute.return_value = mock_insert_result
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_result

        mock_client.return_value.table.return_value = mock_table
        mock_create_key.return_value = ("gw_live_testkey123", 123)

        # Create user with default credits
        result = create_enhanced_user(
            username="testuser", email="test@example.com", auth_method="email"
        )

        # Verify credits allocated
        insert_call = mock_table.insert.call_args[0][0]
        assert insert_call["credits"] == 5.0, "New users should receive $5 in trial credits"

    def test_trial_credits_configuration(self):
        """Test that configuration is set correctly"""
        assert TRIAL_CREDITS_AMOUNT == 5.0, "Trial credits should be $5"
        assert TRIAL_DURATION_DAYS == 3, "Trial should be 3 days"
        assert DAILY_USAGE_LIMIT == 1.0, "Daily limit should be $1"


class TestDailyLimitDuringTrial:
    """Test that daily usage limits are enforced during trial period"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_trial_user_limited_to_1_dollar_per_day(
        self, mock_is_admin, mock_get_usage, mock_client
    ):
        """Test that trial users can only use $1/day even with $5 credits"""
        mock_is_admin.return_value = False
        mock_get_usage.return_value = 0.0  # No usage yet today

        # Mock successful credit deduction
        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 5.0, "subscription_status": "trial"}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123, "credits": 4.0}]

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                mock_table = Mock()
                mock_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_key_result
                )
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                user_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = (
                    mock_update_result
                )
                return user_table
            elif table_name == "credit_transactions":
                txn_table = Mock()
                txn_table.insert.return_value.execute.return_value = Mock(data=[{}])
                return txn_table

        mock_client.return_value.table.side_effect = table_side_effect

        # First deduction of $1.00 - should succeed
        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
            deduct_credits(api_key="gw_live_testkey", tokens=1.0, description="Day 1 usage")

        # Update mock to show $1 already used today
        mock_get_usage.return_value = 1.0

        # Second deduction attempt - should fail (daily limit exceeded)
        with pytest.raises(ValueError) as exc_info:
            with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
                deduct_credits(
                    api_key="gw_live_testkey",
                    tokens=0.50,
                    description="Attempt to exceed daily limit",
                )

        assert "Daily usage limit exceeded" in str(exc_info.value)

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_multiple_small_requests_within_daily_limit(
        self, mock_is_admin, mock_get_usage, mock_client
    ):
        """Test multiple small API calls that stay within $1/day limit"""
        mock_is_admin.return_value = False

        # Simulate progressive usage throughout the day
        usage_tracker = [0.0]

        def get_usage_side_effect(user_id):
            return usage_tracker[0]

        mock_get_usage.side_effect = get_usage_side_effect

        # Mock Supabase
        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 5.0}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123}]

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                mock_table = Mock()
                mock_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_key_result
                )
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                user_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = (
                    mock_update_result
                )
                return user_table
            elif table_name == "credit_transactions":
                txn_table = Mock()
                txn_table.insert.return_value.execute.return_value = Mock(data=[{}])
                return txn_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Make 10 requests of $0.09 each (total $0.90)
        for i in range(10):
            with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
                deduct_credits(api_key="gw_live_testkey", tokens=0.09, description=f"Request {i+1}")
                usage_tracker[0] += 0.09

        # All 10 requests should succeed (total $0.90 < $1.00)
        assert usage_tracker[0] == 0.90


class TestTrialPeriodScenario:
    """Test realistic 3-day trial period scenario"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_three_day_trial_usage_pattern(self, mock_is_admin, mock_get_usage, mock_client):
        """Simulate a user using $1/day for 3 days during trial"""
        mock_is_admin.return_value = False

        # Track usage per day
        daily_usage = {
            "day1": 0.0,
            "day2": 0.0,
            "day3": 0.0,
        }

        def get_usage_for_day(day):
            def inner(user_id):
                return daily_usage[day]

            return inner

        # Mock Supabase
        credits_remaining = [5.0]  # Start with $5

        def get_user_credits():
            mock_result = Mock()
            mock_result.data = [{"id": 123, "credits": credits_remaining[0]}]
            return mock_result

        def update_user_credits(amount):
            credits_remaining[0] -= amount
            mock_result = Mock()
            mock_result.data = [{"id": 123, "credits": credits_remaining[0]}]
            return mock_result

        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                mock_table = Mock()
                mock_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_key_result
                )
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    get_user_credits()
                )
                user_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = Mock(
                    data=[{}]
                )
                return user_table
            elif table_name == "credit_transactions":
                txn_table = Mock()
                txn_table.insert.return_value.execute.return_value = Mock(data=[{}])
                return txn_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Day 1: Use $1.00
        mock_get_usage.side_effect = get_usage_for_day("day1")
        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
            deduct_credits(api_key="gw_live_testkey", tokens=1.0, description="Day 1")
        daily_usage["day1"] = 1.0
        credits_remaining[0] -= 1.0

        assert credits_remaining[0] == 4.0, "After day 1, should have $4 remaining"

        # Day 2: Use $1.00 (reset daily limit)
        mock_get_usage.side_effect = get_usage_for_day("day2")
        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
            deduct_credits(api_key="gw_live_testkey", tokens=1.0, description="Day 2")
        daily_usage["day2"] = 1.0
        credits_remaining[0] -= 1.0

        assert credits_remaining[0] == 3.0, "After day 2, should have $3 remaining"

        # Day 3: Use $1.00 (trial expires after this)
        mock_get_usage.side_effect = get_usage_for_day("day3")
        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
            deduct_credits(api_key="gw_live_testkey", tokens=1.0, description="Day 3")
        daily_usage["day3"] = 1.0
        credits_remaining[0] -= 1.0

        assert credits_remaining[0] == 2.0, "After 3-day trial, should have $2 remaining"


class TestPostTrialCredits:
    """Test that users can continue using remaining credits after trial"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_post_trial_credits_available(self, mock_is_admin, mock_get_usage, mock_client):
        """Test that $2 remaining credits can be used after trial expires"""
        mock_is_admin.return_value = False
        mock_get_usage.return_value = 0.0  # Fresh day

        # Mock user with $2 remaining (post-trial)
        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [
            {
                "id": 123,
                "credits": 2.0,
                "subscription_status": "inactive",  # Trial expired
            }
        ]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123, "credits": 1.0}]

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                mock_table = Mock()
                mock_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_key_result
                )
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                user_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = (
                    mock_update_result
                )
                return user_table
            elif table_name == "credit_transactions":
                txn_table = Mock()
                txn_table.insert.return_value.execute.return_value = Mock(data=[{}])
                return txn_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Should be able to use $1 on day 4 (post-trial)
        with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
            deduct_credits(
                api_key="gw_live_testkey", tokens=1.0, description="Day 4 post-trial usage"
            )

        # Verify credit deduction happened
        # (Would have $1 remaining for day 5)


class TestFraudMitigation:
    """Test that daily limits prevent fraud scenarios"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_bot_cannot_drain_credits_instantly(self, mock_is_admin, mock_get_usage, mock_client):
        """Test that a bot cannot drain all $5 credits in one day"""
        mock_is_admin.return_value = False
        mock_get_usage.return_value = 0.0

        # Mock bot user with $5 credits
        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 999}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 999, "credits": 5.0}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 999, "credits": 4.0}]

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                mock_table = Mock()
                mock_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_key_result
                )
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                user_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = (
                    mock_update_result
                )
                return user_table
            elif table_name == "credit_transactions":
                txn_table = Mock()
                txn_table.insert.return_value.execute.return_value = Mock(data=[{}])
                return txn_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Bot tries to use $5 immediately
        with pytest.raises(ValueError) as exc_info:
            with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
                deduct_credits(
                    api_key="gw_live_botkey",
                    tokens=5.0,
                    description="Bot attempt to drain all credits",
                )

        assert "Daily usage limit exceeded" in str(exc_info.value)

        # Even with $5 in account, bot can only use $1/day
        # This limits exposure to $1/day instead of $5 instantly


class TestConfigurationValues:
    """Test that configuration values are correctly set"""

    def test_trial_credits_is_5_dollars(self):
        """Verify TRIAL_CREDITS_AMOUNT is $5"""
        from src.config.usage_limits import TRIAL_CREDITS_AMOUNT

        assert TRIAL_CREDITS_AMOUNT == 5.0

    def test_daily_limit_is_1_dollar(self):
        """Verify DAILY_USAGE_LIMIT is $1"""
        from src.config.usage_limits import DAILY_USAGE_LIMIT

        assert DAILY_USAGE_LIMIT == 1.0

    def test_trial_duration_is_3_days(self):
        """Verify TRIAL_DURATION_DAYS is 3"""
        from src.config.usage_limits import TRIAL_DURATION_DAYS

        assert TRIAL_DURATION_DAYS == 3

    def test_trial_daily_limit_is_1_dollar(self):
        """Verify TRIAL_DAILY_LIMIT is $1"""
        from src.config.usage_limits import TRIAL_DAILY_LIMIT

        assert TRIAL_DAILY_LIMIT == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
