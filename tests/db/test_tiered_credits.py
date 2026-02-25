#!/usr/bin/env python3
"""
Tests for tiered subscription credit tracking system.

Tests cover:
- Credit deduction priority (allowance first, then purchased)
- Subscription allowance reset on renewal
- Subscription allowance forfeiture on cancellation
- Edge cases (partial deductions, zero balances, etc.)
"""

from unittest.mock import MagicMock, patch

import pytest


class TestTieredCreditDeduction:
    """Test credit deduction logic with tiered subscription tracking."""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_deduct_from_allowance_first(self, mock_log_tx, mock_invalidate, mock_client):
        """Verify allowance is consumed before purchased credits."""
        from src.db.users import deduct_credits

        # Setup: User has $10 allowance and $5 purchased
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # API key lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"user_id": 123}]
        )

        # User lookup with tiered credits
        user_data = {
            "id": 123,
            "subscription_allowance": 10.0,
            "purchased_credits": 5.0,
            "tier": "pro",
        }
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[user_data]
        )

        # Update should succeed
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Deduct $3 - should come entirely from allowance
        with patch("src.db.users.track_database_query"):
            with patch("src.db.plans.is_admin_tier_user", return_value=False):
                with patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"):
                    deduct_credits("test_api_key", 3.0, "Test deduction")

        # Verify transaction was logged with correct breakdown
        call_args = mock_log_tx.call_args
        assert call_args is not None
        metadata = call_args.kwargs.get("metadata", {})
        assert metadata.get("from_allowance") == 3.0
        assert metadata.get("from_purchased") == 0.0

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_deduct_split_across_both(self, mock_log_tx, mock_invalidate, mock_client):
        """Test deduction that spans both allowance and purchased credits."""
        from src.db.users import deduct_credits

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # API key lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"user_id": 123}]
        )

        # User has $3 allowance and $10 purchased
        user_data = {
            "id": 123,
            "subscription_allowance": 3.0,
            "purchased_credits": 10.0,
            "tier": "pro",
        }
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[user_data]
        )

        # Update should succeed
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Deduct $5 - should take $3 from allowance and $2 from purchased
        with patch("src.db.users.track_database_query"):
            with patch("src.db.plans.is_admin_tier_user", return_value=False):
                with patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"):
                    deduct_credits("test_api_key", 5.0, "Test deduction")

        # Verify transaction was logged with correct breakdown
        call_args = mock_log_tx.call_args
        assert call_args is not None
        metadata = call_args.kwargs.get("metadata", {})
        assert metadata.get("from_allowance") == 3.0
        assert metadata.get("from_purchased") == 2.0

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_deduct_from_purchased_when_no_allowance(
        self, mock_log_tx, mock_invalidate, mock_client
    ):
        """Test deduction when allowance is 0 (basic tier or depleted)."""
        from src.db.users import deduct_credits

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # API key lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"user_id": 123}]
        )

        # User has $0 allowance and $10 purchased
        user_data = {
            "id": 123,
            "subscription_allowance": 0.0,
            "purchased_credits": 10.0,
            "tier": "basic",
        }
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[user_data]
        )

        # Update should succeed
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Deduct $5 - should come entirely from purchased
        with patch("src.db.users.track_database_query"):
            with patch("src.db.plans.is_admin_tier_user", return_value=False):
                with patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"):
                    deduct_credits("test_api_key", 5.0, "Test deduction")

        # Verify transaction was logged with correct breakdown
        call_args = mock_log_tx.call_args
        assert call_args is not None
        metadata = call_args.kwargs.get("metadata", {})
        assert metadata.get("from_allowance") == 0.0
        assert metadata.get("from_purchased") == 5.0

    @patch("src.db.users.get_supabase_client")
    def test_insufficient_total_credits_error(self, mock_client):
        """Verify error when total balance is insufficient."""
        from src.db.users import deduct_credits

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # API key lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"user_id": 123}]
        )

        # User has $2 allowance and $1 purchased = $3 total
        user_data = {
            "id": 123,
            "subscription_allowance": 2.0,
            "purchased_credits": 1.0,
            "tier": "pro",
        }
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[user_data]
        )

        # Try to deduct $5 - should fail
        with pytest.raises(RuntimeError) as exc_info:
            with patch("src.db.users.track_database_query"):
                with patch("src.db.plans.is_admin_tier_user", return_value=False):
                    with patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"):
                        deduct_credits("test_api_key", 5.0, "Test deduction")

        assert "Insufficient credits" in str(exc_info.value)


class TestAllowanceReset:
    """Test subscription allowance reset on renewal."""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache_by_id")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_reset_sets_correct_amount_pro(self, mock_log_tx, mock_invalidate, mock_client):
        """PRO tier should get $15 allowance on reset."""
        from src.db.users import reset_subscription_allowance

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Current user has $5 allowance remaining
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"subscription_allowance": 5.0, "purchased_credits": 10.0}]
        )

        # Update should succeed
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Reset to $15 for PRO tier
        result = reset_subscription_allowance(123, 15.0, "pro")

        assert result is True

        # Verify transaction logged the forfeited amount
        call_args = mock_log_tx.call_args
        assert call_args is not None
        metadata = call_args.kwargs.get("metadata", {})
        assert metadata.get("forfeited_allowance") == 5.0
        assert metadata.get("new_allowance") == 15.0

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache_by_id")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_reset_sets_correct_amount_max(self, mock_log_tx, mock_invalidate, mock_client):
        """MAX tier should get $150 allowance on reset."""
        from src.db.users import reset_subscription_allowance

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"subscription_allowance": 50.0, "purchased_credits": 0.0}]
        )

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Reset to $150 for MAX tier
        result = reset_subscription_allowance(123, 150.0, "max")

        assert result is True


class TestSubscriptionCancellation:
    """Test cancellation behavior - allowance forfeited, purchased preserved."""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache_by_id")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_allowance_forfeited_on_cancel(self, mock_log_tx, mock_invalidate, mock_client):
        """Verify allowance is zeroed on cancellation."""
        from src.db.users import forfeit_subscription_allowance

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # User has $8 allowance and $20 purchased
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"subscription_allowance": 8.0, "purchased_credits": 20.0}]
        )

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        # Forfeit allowance
        result = forfeit_subscription_allowance(123)

        assert result["forfeited_allowance"] == 8.0
        assert result["retained_purchased_credits"] == 20.0

        # Verify update set allowance to 0
        update_call = mock_supabase.table.return_value.update.call_args
        assert update_call is not None
        update_data = (
            update_call.args[0] if update_call.args else update_call.kwargs.get("data", {})
        )
        assert update_data.get("subscription_allowance") == 0

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.invalidate_user_cache_by_id")
    @patch("src.db.credit_transactions.log_credit_transaction")
    def test_purchased_credits_preserved_on_cancel(self, mock_log_tx, mock_invalidate, mock_client):
        """Verify purchased credits remain after cancellation."""
        from src.db.users import forfeit_subscription_allowance

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # User has $8 allowance and $20 purchased
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"subscription_allowance": 8.0, "purchased_credits": 20.0}]
        )

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 123}]
        )

        mock_log_tx.return_value = {"id": "tx_123"}

        forfeit_subscription_allowance(123)

        # Verify transaction metadata shows purchased credits preserved
        call_args = mock_log_tx.call_args
        assert call_args is not None
        metadata = call_args.kwargs.get("metadata", {})
        assert metadata.get("retained_purchased_credits") == 20.0


class TestAllowanceFromTier:
    """Test getting allowance amounts from tier configuration."""

    @patch("src.db.subscription_products.get_supabase_client")
    def test_get_pro_allowance(self, mock_client):
        """PRO tier should return $15 allowance."""
        from src.db.subscription_products import get_allowance_from_tier

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"allowance_per_month": 15.0}]
        )

        allowance = get_allowance_from_tier("pro")

        assert allowance == 15.0

    @patch("src.db.subscription_products.get_supabase_client")
    def test_get_max_allowance(self, mock_client):
        """MAX tier should return $150 allowance."""
        from src.db.subscription_products import get_allowance_from_tier

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"allowance_per_month": 150.0}]
        )

        allowance = get_allowance_from_tier("max")

        assert allowance == 150.0

    @patch("src.db.subscription_products.get_supabase_client")
    def test_get_basic_allowance(self, mock_client):
        """Basic tier should return $0 allowance."""
        from src.db.subscription_products import get_allowance_from_tier

        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Basic tier not in subscription_products or has no allowance
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[]
        )

        allowance = get_allowance_from_tier("basic")

        assert allowance == 0.0
