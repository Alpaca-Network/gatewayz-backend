"""
Tests for User Credit Updates (Trial Credit: $5 total, $1/day usage limit)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.db.users import create_enhanced_user, deduct_credits
from src.services.daily_usage_limiter import DailyUsageLimitExceeded


class TestReducedTrialCredits:
    """Test that new users receive $5 trial credits with $1/day usage limit"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.create_api_key")
    def test_create_enhanced_user_default_credits(self, mock_create_key, mock_client):
        """Test that default credits are $5 with $1/day limit enforced"""
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

        # Verify insert was called with credits=5.0 (total credits with $1/day limit)
        insert_call = mock_table.insert.call_args[0][0]
        assert insert_call["credits"] == 5.0

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.create_api_key")
    def test_create_enhanced_user_custom_credits(self, mock_create_key, mock_client):
        """Test that custom credit amounts can still be specified"""
        mock_insert_result = Mock()
        mock_insert_result.data = [{"id": 123}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123}]

        mock_table = Mock()
        mock_table.insert.return_value.execute.return_value = mock_insert_result
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_result

        mock_client.return_value.table.return_value = mock_table
        mock_create_key.return_value = ("gw_live_testkey123", 123)

        # Create user with custom credits
        result = create_enhanced_user(
            username="testuser",
            email="test@example.com",
            auth_method="email",
            credits=5.0,  # Custom amount
        )

        # Verify insert was called with custom credits
        insert_call = mock_table.insert.call_args[0][0]
        assert insert_call["credits"] == 5.0


class TestDailyLimitEnforcement:
    """Test that daily usage limits are enforced in deduct_credits"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.enforce_daily_usage_limit")
    @patch("src.db.plans.is_admin_tier_user")
    def test_deduct_credits_checks_daily_limit(
        self, mock_is_admin, mock_enforce_limit, mock_client
    ):
        """Test that deduct_credits calls enforce_daily_usage_limit"""
        # Setup mocks
        mock_is_admin.return_value = False

        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 10.0}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123, "credits": 9.75}]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.execute.return_value = mock_key_result

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
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

        # Deduct credits
        deduct_credits(api_key="gw_live_testkey", tokens=0.25, description="Test usage")

        # Verify enforce_daily_usage_limit was called
        mock_enforce_limit.assert_called_once_with(123, 0.25)

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.enforce_daily_usage_limit")
    @patch("src.db.plans.is_admin_tier_user")
    def test_deduct_credits_raises_on_daily_limit_exceeded(
        self, mock_is_admin, mock_enforce_limit, mock_client
    ):
        """Test that DailyUsageLimitExceeded is converted to ValueError"""
        # Setup mocks
        mock_is_admin.return_value = False

        # Simulate daily limit exceeded
        mock_enforce_limit.side_effect = DailyUsageLimitExceeded("Daily usage limit exceeded")

        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 10.0}]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.execute.return_value = mock_key_result

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                return user_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Should raise ValueError (converted from DailyUsageLimitExceeded)
        with pytest.raises(ValueError) as exc_info:
            deduct_credits(api_key="gw_live_testkey", tokens=0.25, description="Test usage")

        assert "Daily usage limit exceeded" in str(exc_info.value)

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.enforce_daily_usage_limit")
    @patch("src.db.plans.is_admin_tier_user")
    def test_admin_users_bypass_daily_limit(self, mock_is_admin, mock_enforce_limit, mock_client):
        """Test that admin users bypass daily usage limits"""
        # Setup mocks - user is admin
        mock_is_admin.return_value = True

        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 10.0}]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.execute.return_value = mock_key_result

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
                return mock_table
            elif table_name == "users":
                user_table = Mock()
                user_table.select.return_value.eq.return_value.execute.return_value = (
                    mock_user_result
                )
                return user_table

        mock_client.return_value.table.side_effect = table_side_effect

        # Deduct credits for admin user
        deduct_credits(
            api_key="gw_live_adminkey", tokens=100.0, description="Admin usage"  # Large amount
        )

        # Verify daily limit check was NOT called (admin bypasses it)
        mock_enforce_limit.assert_not_called()


class TestCreditDeductionWithDailyLimits:
    """Integration tests for credit deduction with daily limits"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.get_daily_usage")
    @patch("src.db.plans.is_admin_tier_user")
    def test_multiple_small_deductions_within_limit(
        self, mock_is_admin, mock_get_usage, mock_client
    ):
        """Test multiple small deductions that stay within $1 daily limit"""
        mock_is_admin.return_value = False

        # Simulate progressive daily usage
        usage_tracker = [0.0]

        def get_usage_side_effect(user_id):
            return usage_tracker[0]

        mock_get_usage.side_effect = get_usage_side_effect

        # Mock Supabase for successful deductions
        mock_key_result = Mock()
        mock_key_result.data = [{"user_id": 123}]

        mock_user_result = Mock()
        mock_user_result.data = [{"id": 123, "credits": 10.0}]

        mock_update_result = Mock()
        mock_update_result.data = [{"id": 123}]

        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.execute.return_value = mock_key_result

        def table_side_effect(table_name):
            if table_name == "api_keys_new":
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

        # Make 4 deductions of $0.20 each (total $0.80, within $1 limit)
        for i in range(4):
            with patch("src.services.daily_usage_limiter.ENFORCE_DAILY_LIMITS", True):
                deduct_credits(
                    api_key="gw_live_testkey", tokens=0.20, description=f"Test usage {i+1}"
                )
                usage_tracker[0] += 0.20

        # All should succeed (total $0.80 < $1.00 limit)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
