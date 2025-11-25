#!/usr/bin/env python3
"""
Tests for subscription products database module
"""

import pytest
from postgrest import APIError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.db.subscription_products as subscription_products_module
from src.db.subscription_products import (
    get_tier_from_product_id,
    get_credits_from_tier,
    get_subscription_product,
    get_all_active_products,
    add_subscription_product,
    update_subscription_product,
)


class TestSubscriptionProducts:
    """Test subscription products database operations"""

    @pytest.fixture(autouse=True)
    def reset_fallback_cache(self):
        subscription_products_module._reset_fallback_cache()
        yield
        subscription_products_module._reset_fallback_cache()

    @pytest.fixture
    def mock_supabase_client(self):
        """Mock Supabase client"""
        with patch("src.db.subscription_products.get_supabase_client") as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    def test_get_tier_from_product_id_pro(self, mock_supabase_client):
        """Test getting tier from PRO product ID"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"tier": "pro"}
        ]

        tier = get_tier_from_product_id("prod_TKOqQPhVRxNp4Q")

        assert tier == "pro"

    def test_get_tier_from_product_id_max(self, mock_supabase_client):
        """Test getting tier from MAX product ID"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"tier": "max"}
        ]

        tier = get_tier_from_product_id("prod_TKOqRE2L6qXu7s")

        assert tier == "max"

    def test_get_tier_from_product_id_not_found(self, mock_supabase_client):
        """Test getting tier from unknown product ID defaults to basic"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        tier = get_tier_from_product_id("prod_unknown_123")

        assert tier == "basic"  # Should default to basic

    def test_get_tier_from_product_id_recovers_from_schema_cache_miss(self, mock_supabase_client):
        """Ensure schema cache refresh is attempted when PostgREST cannot find the table."""
        execute_mock = (
            mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute
        )
        execute_mock.side_effect = [
            APIError({"code": "PGRST205", "message": "Could not find the table"}),
            SimpleNamespace(data=[{"tier": "pro"}]),
        ]

        with patch(
            "src.db.subscription_products.refresh_postgrest_schema_cache", return_value=True
        ) as refresh_mock:
            tier = get_tier_from_product_id("prod_schema_cache")

        assert tier == "pro"
        refresh_mock.assert_called_once()
        assert execute_mock.call_count == 2

    def test_get_tier_from_product_id_schema_cache_refresh_failure(self, mock_supabase_client):
        """Ensure we fall back to cached configuration when schema cache refresh cannot run."""
        execute_mock = (
            mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute
        )
        execute_mock.side_effect = [
            APIError({"code": "PGRST205", "message": "Could not find the table"}),
        ]

        with patch(
            "src.db.subscription_products.refresh_postgrest_schema_cache", return_value=False
        ) as refresh_mock:
            tier = get_tier_from_product_id("prod_TKOqQPhVRxNp4Q")

        assert tier == "pro"
        refresh_mock.assert_called_once()
        assert execute_mock.call_count == 1

    def test_get_tier_from_product_id_uses_fallback_on_supabase_exception(self, mock_supabase_client):
        """Known products should still resolve when Supabase raises."""
        mock_supabase_client.table.side_effect = Exception("database down")

        tier = get_tier_from_product_id("prod_TKOqQPhVRxNp4Q")

        assert tier == "pro"

    def test_get_credits_from_tier_pro(self, mock_supabase_client):
        """Test getting credits for PRO tier"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"credits_per_month": 20.00}
        ]

        credits = get_credits_from_tier("pro")

        assert credits == 20.0

    def test_get_credits_from_tier_max(self, mock_supabase_client):
        """Test getting credits for MAX tier"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"credits_per_month": 150.00}
        ]

        credits = get_credits_from_tier("max")

        assert credits == 150.0

    def test_get_credits_from_tier_not_found(self, mock_supabase_client):
        """Test getting credits from unknown tier defaults to 0"""
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

        credits = get_credits_from_tier("unknown_tier")

        assert credits == 0.0  # Should default to 0

    def test_get_credits_from_tier_uses_fallback_on_supabase_exception(self, mock_supabase_client):
        """Ensure credits are served from fallback when Supabase is unavailable."""
        mock_supabase_client.table.side_effect = Exception("database down")

        credits = get_credits_from_tier("pro")

        assert credits == 20.0

    def test_get_subscription_product(self, mock_supabase_client):
        """Test getting full product configuration"""
        expected_product = {
            "product_id": "prod_TKOqQPhVRxNp4Q",
            "tier": "pro",
            "display_name": "Pro",
            "credits_per_month": 20.00,
            "description": "Professional tier",
            "is_active": True,
        }

        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            expected_product
        ]

        product = get_subscription_product("prod_TKOqQPhVRxNp4Q")

        assert product == expected_product

    def test_get_subscription_product_uses_fallback_on_supabase_exception(self, mock_supabase_client):
        """Ensure subscription product snapshot falls back to cached defaults."""
        mock_supabase_client.table.side_effect = Exception("database down")

        product = get_subscription_product("prod_TKOqQPhVRxNp4Q")

        assert product is not None
        assert product["tier"] == "pro"

    def test_get_all_active_products(self, mock_supabase_client):
        """Test getting all active products"""
        expected_products = [
            {
                "product_id": "prod_TKOqQPhVRxNp4Q",
                "tier": "pro",
                "credits_per_month": 20.00,
            },
            {
                "product_id": "prod_TKOqRE2L6qXu7s",
                "tier": "max",
                "credits_per_month": 150.00,
            },
        ]

        mock_supabase_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = expected_products

        products = get_all_active_products()

        assert len(products) == 2
        assert products == expected_products

    def test_get_all_active_products_uses_fallback_when_supabase_fails(self, mock_supabase_client):
        """Ensure fallback list is returned when Supabase cannot be queried."""
        mock_supabase_client.table.side_effect = Exception("database down")

        products = get_all_active_products()

        assert products  # Should contain fallback entries
        assert any(product["tier"] == "pro" for product in products)

    def test_add_subscription_product(self, mock_supabase_client):
        """Test adding new subscription product"""
        mock_supabase_client.table.return_value.insert.return_value.execute.return_value.data = [
            {"product_id": "prod_new_enterprise_123"}
        ]

        result = add_subscription_product(
            product_id="prod_new_enterprise_123",
            tier="enterprise",
            display_name="Enterprise",
            credits_per_month=500.00,
            description="Enterprise tier",
            is_active=True,
        )

        assert result is True
        mock_supabase_client.table.assert_called_with("subscription_products")

    def test_add_subscription_product_failure(self, mock_supabase_client):
        """Test adding product failure"""
        mock_supabase_client.table.return_value.insert.return_value.execute.return_value.data = None

        result = add_subscription_product(
            product_id="prod_fail_123",
            tier="test",
            display_name="Test",
            credits_per_month=10.00,
        )

        assert result is False

    def test_update_subscription_product(self, mock_supabase_client):
        """Test updating subscription product"""
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"product_id": "prod_TKOqQPhVRxNp4Q"}
        ]

        result = update_subscription_product(
            product_id="prod_TKOqQPhVRxNp4Q",
            credits_per_month=25.00,  # Update credits
        )

        assert result is True

        # Verify update was called with correct data
        call_args = mock_supabase_client.table.return_value.update.call_args[0][0]
        assert call_args["credits_per_month"] == 25.00

    def test_update_subscription_product_no_fields(self, mock_supabase_client):
        """Test updating product with no fields returns False"""
        result = update_subscription_product(
            product_id="prod_TKOqQPhVRxNp4Q",
            # No fields provided
        )

        assert result is False

    def test_database_error_handling(self, mock_supabase_client):
        """Test that database errors are handled gracefully"""
        mock_supabase_client.table.side_effect = Exception("Database error")

        # Should return default values instead of raising
        tier = get_tier_from_product_id("prod_test_123")
        assert tier == "basic"

        credits = get_credits_from_tier("test")
        assert credits == 0.0

        product = get_subscription_product("prod_test_123")
        assert product is None
