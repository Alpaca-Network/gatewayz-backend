#!/usr/bin/env python3
"""
Tests for Subscription Management Service
Tests upgrade, downgrade, cancel, and get subscription functionality
"""

import pytest
from datetime import datetime, timezone, UTC
from unittest.mock import MagicMock, patch

from src.schemas.payments import (
    UpgradeSubscriptionRequest,
    DowngradeSubscriptionRequest,
    CancelSubscriptionRequest,
)
from src.services.payments import StripeService


@pytest.fixture
def stripe_service():
    """Create a StripeService instance with mocked Stripe API key"""
    with patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake_key"}):
        with patch("stripe.api_key", "sk_test_fake_key"):
            service = StripeService()
            return service


@pytest.fixture
def mock_user_pro():
    """Mock user with Pro tier subscription"""
    return {
        "id": 123,
        "email": "test@example.com",
        "tier": "pro",
        "subscription_status": "active",
        "stripe_customer_id": "cus_test123",
        "stripe_subscription_id": "sub_test123",
        "stripe_product_id": "prod_TKOqQPhVRxNp4Q",
    }


@pytest.fixture
def mock_user_max():
    """Mock user with Max tier subscription"""
    return {
        "id": 456,
        "email": "max@example.com",
        "tier": "max",
        "subscription_status": "active",
        "stripe_customer_id": "cus_test456",
        "stripe_subscription_id": "sub_test456",
        "stripe_product_id": "prod_TKOraBpWMxMAIu",
    }


@pytest.fixture
def mock_user_no_subscription():
    """Mock user without subscription"""
    return {
        "id": 789,
        "email": "basic@example.com",
        "tier": "basic",
        "subscription_status": "inactive",
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
    }


@pytest.fixture
def mock_stripe_subscription_pro():
    """Mock Stripe subscription object for Pro tier"""
    mock_sub = MagicMock()
    mock_sub.id = "sub_test123"
    mock_sub.status = "active"
    mock_sub.current_period_start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    mock_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())
    mock_sub.cancel_at_period_end = False
    mock_sub.canceled_at = None
    mock_sub.customer = "cus_test123"

    # Mock subscription items
    mock_item = MagicMock()
    mock_item.id = "si_test123"
    mock_price = MagicMock()
    mock_price.id = "price_pro_8"
    mock_price.product = "prod_TKOqQPhVRxNp4Q"
    mock_item.price = mock_price

    mock_items = MagicMock()
    mock_items.data = [mock_item]
    mock_sub.items = mock_items

    mock_sub.metadata = {"user_id": "123", "tier": "pro", "product_id": "prod_TKOqQPhVRxNp4Q"}

    return mock_sub


@pytest.fixture
def mock_stripe_subscription_max():
    """Mock Stripe subscription object for Max tier"""
    mock_sub = MagicMock()
    mock_sub.id = "sub_test456"
    mock_sub.status = "active"
    mock_sub.current_period_start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    mock_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())
    mock_sub.cancel_at_period_end = False
    mock_sub.canceled_at = None
    mock_sub.customer = "cus_test456"

    # Mock subscription items
    mock_item = MagicMock()
    mock_item.id = "si_test456"
    mock_price = MagicMock()
    mock_price.id = "price_max_75"
    mock_price.product = "prod_TKOraBpWMxMAIu"
    mock_item.price = mock_price

    mock_items = MagicMock()
    mock_items.data = [mock_item]
    mock_sub.items = mock_items

    mock_sub.metadata = {"user_id": "456", "tier": "max", "product_id": "prod_TKOraBpWMxMAIu"}

    return mock_sub


class TestGetCurrentSubscription:
    """Tests for get_current_subscription method"""

    def test_get_subscription_with_active_subscription(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test getting subscription for user with active subscription"""
        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                result = stripe_service.get_current_subscription(123)

                assert result.has_subscription is True
                assert result.subscription_id == "sub_test123"
                assert result.status == "active"
                assert result.tier == "pro"
                assert result.cancel_at_period_end is False
                assert result.product_id == "prod_TKOqQPhVRxNp4Q"

    def test_get_subscription_without_subscription(
        self, stripe_service, mock_user_no_subscription
    ):
        """Test getting subscription for user without subscription"""
        with patch("src.services.payments.get_user_by_id", return_value=mock_user_no_subscription):
            result = stripe_service.get_current_subscription(789)

            assert result.has_subscription is False
            assert result.subscription_id is None
            assert result.tier == "basic"

    def test_get_subscription_user_not_found(self, stripe_service):
        """Test getting subscription for non-existent user"""
        with patch("src.services.payments.get_user_by_id", return_value=None):
            with pytest.raises(ValueError, match="User 999 not found"):
                stripe_service.get_current_subscription(999)


class TestUpgradeSubscription:
    """Tests for upgrade_subscription method"""

    def test_upgrade_invalid_product_id_returns_basic(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test upgrade fails when product ID resolves to basic tier"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_invalid",
            new_product_id="prod_invalid_123",
            proration_behavior="create_prorations",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                # get_tier_from_product_id returns "basic" for unknown product
                with patch("src.services.payments.get_tier_from_product_id", return_value="basic"):
                    with pytest.raises(ValueError, match="Invalid product ID for upgrade"):
                        stripe_service.upgrade_subscription(123, upgrade_request)

    def test_upgrade_invalid_product_id_returns_none(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test upgrade fails when product ID resolves to None"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_invalid",
            new_product_id="prod_invalid_456",
            proration_behavior="create_prorations",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                # get_tier_from_product_id returns None for unknown product
                with patch("src.services.payments.get_tier_from_product_id", return_value=None):
                    with pytest.raises(ValueError, match="Invalid product ID for upgrade"):
                        stripe_service.upgrade_subscription(123, upgrade_request)

    def test_upgrade_allowance_reset_failure(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test upgrade fails when allowance reset fails"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test123"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="max"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=2):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=150.0):
                                    # Simulate reset_subscription_allowance returning None (failure)
                                    with patch("src.db.users.reset_subscription_allowance", return_value=None):
                                        with patch("src.db.users.get_user_by_id", return_value={"subscription_allowance": 15.0}):
                                            with pytest.raises(Exception, match="Failed to update subscription allowance"):
                                                stripe_service.upgrade_subscription(123, upgrade_request)

    def test_upgrade_logs_credit_transaction(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test that upgrade logs audit trail via credit transaction"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test123"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="max"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=2):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=150.0):
                                    with patch("src.db.users.reset_subscription_allowance", return_value=True):
                                        with patch("src.db.users.get_user_by_id", return_value={"subscription_allowance": 15.0}):
                                            with patch("src.db.users.invalidate_user_cache_by_id"):
                                                with patch("src.db.credit_transactions.log_credit_transaction") as mock_log:
                                                    result = stripe_service.upgrade_subscription(123, upgrade_request)

                                                    # Verify credit transaction was logged
                                                    assert mock_log.called
                                                    call_kwargs = mock_log.call_args[1]
                                                    assert call_kwargs["user_id"] == 123
                                                    assert call_kwargs["transaction_type"] == "subscription_upgrade"
                                                    assert "from_tier" in call_kwargs["metadata"]
                                                    assert "to_tier" in call_kwargs["metadata"]
                                                    assert call_kwargs["metadata"]["to_tier"] == "max"

    def test_upgrade_pro_to_max(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test upgrading from Pro to Max tier"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test123"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="max"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=2):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=150.0):
                                    with patch("src.db.users.reset_subscription_allowance", return_value=True):
                                        with patch("src.db.users.invalidate_user_cache_by_id"):
                                            result = stripe_service.upgrade_subscription(123, upgrade_request)

                                            assert result.success is True
                                            assert result.subscription_id == "sub_test123"
                                            assert result.status == "active"
                                            assert result.current_tier == "max"
                                            assert "upgraded" in result.message.lower()

    def test_upgrade_without_subscription(
        self, stripe_service, mock_user_no_subscription
    ):
        """Test upgrading when user has no subscription"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_no_subscription):
            with pytest.raises(ValueError, match="does not have an active subscription"):
                stripe_service.upgrade_subscription(789, upgrade_request)

    def test_upgrade_inactive_subscription(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test upgrading when subscription is not active"""
        upgrade_request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
        )

        mock_stripe_subscription_pro.status = "canceled"

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with pytest.raises(ValueError, match="Cannot upgrade subscription with status"):
                    stripe_service.upgrade_subscription(123, upgrade_request)


class TestDowngradeSubscription:
    """Tests for downgrade_subscription method"""

    def test_downgrade_invalid_product_id_returns_basic(
        self, stripe_service, mock_user_max, mock_stripe_subscription_max
    ):
        """Test downgrade fails when product ID resolves to basic tier"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_invalid",
            new_product_id="prod_invalid_123",
            proration_behavior="create_prorations",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_max):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_max):
                with patch("src.services.payments.get_tier_from_product_id", return_value="basic"):
                    with pytest.raises(ValueError, match="Invalid product ID for downgrade"):
                        stripe_service.downgrade_subscription(456, downgrade_request)

    def test_downgrade_invalid_product_id_returns_none(
        self, stripe_service, mock_user_max, mock_stripe_subscription_max
    ):
        """Test downgrade fails when product ID resolves to None"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_invalid",
            new_product_id="prod_invalid_456",
            proration_behavior="create_prorations",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_max):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_max):
                with patch("src.services.payments.get_tier_from_product_id", return_value=None):
                    with pytest.raises(ValueError, match="Invalid product ID for downgrade"):
                        stripe_service.downgrade_subscription(456, downgrade_request)

    def test_downgrade_allowance_reset_failure(
        self, stripe_service, mock_user_max, mock_stripe_subscription_max
    ):
        """Test downgrade fails when allowance reset fails"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_pro_8",
            new_product_id="prod_TKOqQPhVRxNp4Q",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test456"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_max):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_max):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="pro"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=1):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=15.0):
                                    # Simulate reset_subscription_allowance returning None (failure)
                                    with patch("src.db.users.reset_subscription_allowance", return_value=None):
                                        with patch("src.db.users.get_user_by_id", return_value={"subscription_allowance": 150.0}):
                                            with pytest.raises(Exception, match="Failed to update subscription allowance"):
                                                stripe_service.downgrade_subscription(456, downgrade_request)

    def test_downgrade_logs_credit_transaction(
        self, stripe_service, mock_user_max, mock_stripe_subscription_max
    ):
        """Test that downgrade logs audit trail via credit transaction"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_pro_8",
            new_product_id="prod_TKOqQPhVRxNp4Q",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test456"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_max):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_max):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="pro"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=1):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=15.0):
                                    with patch("src.db.users.reset_subscription_allowance", return_value=True):
                                        with patch("src.db.users.get_user_by_id", return_value={"subscription_allowance": 150.0}):
                                            with patch("src.db.users.invalidate_user_cache_by_id"):
                                                with patch("src.db.credit_transactions.log_credit_transaction") as mock_log:
                                                    result = stripe_service.downgrade_subscription(456, downgrade_request)

                                                    # Verify credit transaction was logged
                                                    assert mock_log.called
                                                    call_kwargs = mock_log.call_args[1]
                                                    assert call_kwargs["user_id"] == 456
                                                    assert call_kwargs["transaction_type"] == "subscription_downgrade"
                                                    assert "from_tier" in call_kwargs["metadata"]
                                                    assert "to_tier" in call_kwargs["metadata"]
                                                    assert call_kwargs["metadata"]["to_tier"] == "pro"

    def test_downgrade_max_to_pro(
        self, stripe_service, mock_user_max, mock_stripe_subscription_max
    ):
        """Test downgrading from Max to Pro tier"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_pro_8",
            new_product_id="prod_TKOqQPhVRxNp4Q",
            proration_behavior="create_prorations",
        )

        # Mock updated subscription
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test456"
        mock_updated_sub.status = "active"
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_max):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_max):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.services.payments.get_tier_from_product_id", return_value="pro"):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.insert.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.plans.get_plan_id_by_tier", return_value=1):
                                with patch("src.db.subscription_products.get_allowance_from_tier", return_value=15.0):
                                    with patch("src.db.users.reset_subscription_allowance", return_value=True):
                                        with patch("src.db.users.invalidate_user_cache_by_id"):
                                            result = stripe_service.downgrade_subscription(456, downgrade_request)

                                            assert result.success is True
                                            assert result.subscription_id == "sub_test456"
                                            assert result.status == "active"
                                            assert result.current_tier == "pro"
                                            assert "downgraded" in result.message.lower()

    def test_downgrade_without_subscription(
        self, stripe_service, mock_user_no_subscription
    ):
        """Test downgrading when user has no subscription"""
        downgrade_request = DowngradeSubscriptionRequest(
            new_price_id="price_pro_8",
            new_product_id="prod_TKOqQPhVRxNp4Q",
        )

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_no_subscription):
            with pytest.raises(ValueError, match="does not have an active subscription"):
                stripe_service.downgrade_subscription(789, downgrade_request)


class TestCancelSubscription:
    """Tests for cancel_subscription method"""

    def test_cancel_at_period_end(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test canceling subscription at end of billing period"""
        cancel_request = CancelSubscriptionRequest(
            cancel_at_period_end=True,
            reason="Switching to another service",
        )

        # Mock updated subscription with cancel_at_period_end=True
        mock_updated_sub = MagicMock()
        mock_updated_sub.id = "sub_test123"
        mock_updated_sub.status = "active"
        mock_updated_sub.cancel_at_period_end = True
        mock_updated_sub.current_period_end = int(datetime(2024, 2, 1, tzinfo=UTC).timestamp())

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with patch("stripe.Subscription.modify", return_value=mock_updated_sub):
                    with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                        mock_table = MagicMock()
                        mock_client.return_value.table.return_value = mock_table
                        mock_table.update.return_value = mock_table
                        mock_table.eq.return_value = mock_table
                        mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                        with patch("src.db.users.invalidate_user_cache_by_id"):
                            result = stripe_service.cancel_subscription(123, cancel_request)

                            assert result.success is True
                            assert result.subscription_id == "sub_test123"
                            assert result.status == "cancel_scheduled"
                            assert result.current_tier == "pro"  # Still pro until period ends
                            assert result.effective_date is not None

    def test_cancel_immediately(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test canceling subscription immediately"""
        cancel_request = CancelSubscriptionRequest(
            cancel_at_period_end=False,
            reason="No longer needed",
        )

        # Mock canceled subscription
        mock_canceled_sub = MagicMock()
        mock_canceled_sub.id = "sub_test123"
        mock_canceled_sub.status = "canceled"

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with patch("stripe.Subscription.cancel", return_value=mock_canceled_sub):
                    with patch("src.db.users.forfeit_subscription_allowance", return_value=10.0):
                        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
                            mock_table = MagicMock()
                            mock_client.return_value.table.return_value = mock_table
                            mock_table.update.return_value = mock_table
                            mock_table.eq.return_value = mock_table
                            mock_table.execute.return_value = MagicMock(data=[{"id": 1}])

                            with patch("src.db.users.invalidate_user_cache_by_id"):
                                result = stripe_service.cancel_subscription(123, cancel_request)

                                assert result.success is True
                                assert result.subscription_id == "sub_test123"
                                assert result.status == "canceled"
                                assert result.current_tier == "basic"

    def test_cancel_without_subscription(
        self, stripe_service, mock_user_no_subscription
    ):
        """Test canceling when user has no subscription"""
        cancel_request = CancelSubscriptionRequest(cancel_at_period_end=True)

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_no_subscription):
            with pytest.raises(ValueError, match="does not have an active subscription"):
                stripe_service.cancel_subscription(789, cancel_request)

    def test_cancel_already_canceled_subscription(
        self, stripe_service, mock_user_pro, mock_stripe_subscription_pro
    ):
        """Test canceling already canceled subscription"""
        cancel_request = CancelSubscriptionRequest(cancel_at_period_end=True)

        mock_stripe_subscription_pro.status = "canceled"

        with patch("src.services.payments.get_user_by_id", return_value=mock_user_pro):
            with patch("stripe.Subscription.retrieve", return_value=mock_stripe_subscription_pro):
                with pytest.raises(ValueError, match="Cannot cancel subscription with status"):
                    stripe_service.cancel_subscription(123, cancel_request)


class TestTransactionTypes:
    """Tests for subscription-related transaction types"""

    def test_subscription_upgrade_transaction_type_exists(self):
        """Test that SUBSCRIPTION_UPGRADE transaction type exists"""
        from src.db.credit_transactions import TransactionType
        assert hasattr(TransactionType, "SUBSCRIPTION_UPGRADE")
        assert TransactionType.SUBSCRIPTION_UPGRADE == "subscription_upgrade"

    def test_subscription_downgrade_transaction_type_exists(self):
        """Test that SUBSCRIPTION_DOWNGRADE transaction type exists"""
        from src.db.credit_transactions import TransactionType
        assert hasattr(TransactionType, "SUBSCRIPTION_DOWNGRADE")
        assert TransactionType.SUBSCRIPTION_DOWNGRADE == "subscription_downgrade"


class TestSubscriptionManagementSchemas:
    """Tests for subscription management request/response schemas"""

    def test_upgrade_request_validation(self):
        """Test UpgradeSubscriptionRequest validation"""
        # Valid request
        request = UpgradeSubscriptionRequest(
            new_price_id="price_max_75",
            new_product_id="prod_TKOraBpWMxMAIu",
            proration_behavior="create_prorations",
        )
        assert request.new_price_id == "price_max_75"
        assert request.proration_behavior == "create_prorations"

        # Invalid proration behavior
        with pytest.raises(ValueError):
            UpgradeSubscriptionRequest(
                new_price_id="price_max_75",
                new_product_id="prod_TKOraBpWMxMAIu",
                proration_behavior="invalid_behavior",
            )

    def test_downgrade_request_validation(self):
        """Test DowngradeSubscriptionRequest validation"""
        # Valid request
        request = DowngradeSubscriptionRequest(
            new_price_id="price_pro_8",
            new_product_id="prod_TKOqQPhVRxNp4Q",
            proration_behavior="create_prorations",
        )
        assert request.new_price_id == "price_pro_8"

        # Invalid proration behavior
        with pytest.raises(ValueError):
            DowngradeSubscriptionRequest(
                new_price_id="price_pro_8",
                new_product_id="prod_TKOqQPhVRxNp4Q",
                proration_behavior="invalid",
            )

    def test_cancel_request_defaults(self):
        """Test CancelSubscriptionRequest defaults"""
        request = CancelSubscriptionRequest()
        assert request.cancel_at_period_end is True
        assert request.reason is None

        request_immediate = CancelSubscriptionRequest(cancel_at_period_end=False)
        assert request_immediate.cancel_at_period_end is False
