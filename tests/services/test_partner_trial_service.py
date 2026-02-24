"""
Tests for Partner Trial Service

Tests the partner-specific trial functionality used for partners like Redbeard.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.services.partner_trial_service import (
    PartnerTrialService,
    get_partner_config,
    get_user_daily_limit,
    is_partner_code,
)


class TestPartnerTrialService:
    """Tests for PartnerTrialService class"""

    def setup_method(self):
        """Clear cache before each test"""
        PartnerTrialService.invalidate_cache()

    def teardown_method(self):
        """Clear cache after each test"""
        PartnerTrialService.invalidate_cache()

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_config_valid_partner(self, mock_get_client):
        """Test fetching valid partner configuration"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = [
            {
                "id": "test-uuid",
                "partner_code": "REDBEARD",
                "partner_name": "Red Beard Ventures",
                "trial_duration_days": 14,
                "trial_tier": "pro",
                "trial_credits_usd": 20.00,
                "trial_max_tokens": 1000000,
                "trial_max_requests": 10000,
                "daily_usage_limit_usd": 5.00,
                "is_active": True,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        config = get_partner_config("REDBEARD")

        assert config is not None
        assert config["partner_code"] == "REDBEARD"
        assert config["trial_duration_days"] == 14
        assert config["trial_tier"] == "pro"
        assert config["trial_credits_usd"] == 20.00

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_config_case_insensitive(self, mock_get_client):
        """Test that partner code lookup is case insensitive"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = [
            {
                "id": "test-uuid",
                "partner_code": "REDBEARD",
                "partner_name": "Red Beard Ventures",
                "trial_duration_days": 14,
                "trial_tier": "pro",
                "trial_credits_usd": 20.00,
                "is_active": True,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        # Test lowercase
        config = get_partner_config("redbeard")
        assert config is not None
        assert config["partner_code"] == "REDBEARD"

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_config_not_found(self, mock_get_client):
        """Test fetching non-existent partner"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        config = get_partner_config("NONEXISTENT")
        assert config is None

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_config_caching(self, mock_get_client):
        """Test that partner configs are cached"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = [
            {
                "id": "test-uuid",
                "partner_code": "REDBEARD",
                "partner_name": "Red Beard Ventures",
                "trial_duration_days": 14,
                "trial_tier": "pro",
                "trial_credits_usd": 20.00,
                "is_active": True,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        # First call should hit database
        config1 = get_partner_config("REDBEARD")
        assert config1 is not None

        # Second call should use cache (no additional DB call)
        config2 = get_partner_config("REDBEARD")
        assert config2 is not None

        # Database should only be called once
        assert mock_client.table.call_count == 1

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_is_partner_code_true(self, mock_get_client):
        """Test is_partner_code returns True for valid partner"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = [{"partner_code": "REDBEARD", "is_active": True}]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        assert is_partner_code("REDBEARD") is True

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_is_partner_code_false(self, mock_get_client):
        """Test is_partner_code returns False for invalid partner"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        # User referral code (8 chars alphanumeric)
        assert is_partner_code("ABC12345") is False

    def test_is_partner_code_empty(self):
        """Test is_partner_code returns False for empty string"""
        assert is_partner_code("") is False
        assert is_partner_code(None) is False

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_start_partner_trial_success(self, mock_get_client):
        """Test starting a partner trial successfully"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock partner config
        mock_config_result = MagicMock()
        mock_config_result.data = [
            {
                "id": "test-partner-uuid",
                "partner_code": "REDBEARD",
                "partner_name": "Red Beard Ventures",
                "trial_duration_days": 14,
                "trial_tier": "pro",
                "trial_credits_usd": 20.00,
                "trial_max_tokens": 1000000,
                "trial_max_requests": 10000,
                "daily_usage_limit_usd": 5.00,
                "is_active": True,
            }
        ]

        # Setup the mock chain for config lookup
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_config_result
        )

        # Mock update and insert operations
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            MagicMock()
        )
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        result = PartnerTrialService.start_partner_trial(
            user_id=123,
            api_key="gw_live_test_key",
            partner_code="REDBEARD",
            signup_source="landing_page:redbeard",
        )

        assert result["success"] is True
        assert result["partner_code"] == "REDBEARD"
        assert result["trial_tier"] == "pro"
        assert result["trial_credits_usd"] == 20.00
        assert result["trial_duration_days"] == 14
        assert result["daily_usage_limit_usd"] == 5.00
        assert "trial_expires_at" in result

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_start_partner_trial_invalid_partner(self, mock_get_client):
        """Test starting trial with invalid partner code raises error"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock empty config (partner not found)
        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        with pytest.raises(ValueError) as exc_info:
            PartnerTrialService.start_partner_trial(
                user_id=123,
                api_key="gw_live_test_key",
                partner_code="INVALID",
            )

        assert "INVALID" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_user_daily_limit_partner_user(self, mock_get_client):
        """Test getting daily limit for partner trial user"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock user with partner code
        mock_user_result = MagicMock()
        mock_user_result.data = [
            {
                "partner_code": "REDBEARD",
                "subscription_status": "trial",
                "tier": "pro",
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_user_result
        )

        # Mock partner config
        mock_config_result = MagicMock()
        mock_config_result.data = [
            {
                "partner_code": "REDBEARD",
                "daily_usage_limit_usd": 5.00,
                "is_active": True,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_config_result
        )

        limit = get_user_daily_limit(123)
        assert limit == 5.00

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_user_daily_limit_standard_user(self, mock_get_client):
        """Test getting daily limit for standard trial user"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock user without partner code
        mock_user_result = MagicMock()
        mock_user_result.data = [
            {
                "partner_code": None,
                "subscription_status": "trial",
                "tier": "basic",
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_user_result
        )

        limit = get_user_daily_limit(123)
        assert limit == 1.00  # Standard $1/day limit

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_user_daily_limit_paid_subscriber(self, mock_get_client):
        """Test getting daily limit for paid subscriber (unlimited)"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock paid subscriber
        mock_user_result = MagicMock()
        mock_user_result.data = [
            {
                "partner_code": None,
                "subscription_status": "active",
                "tier": "pro",
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            mock_user_result
        )

        limit = get_user_daily_limit(123)
        assert limit == float("inf")

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_trial_status(self, mock_get_client):
        """Test getting partner trial status"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock trial analytics
        mock_result = MagicMock()
        mock_result.data = [
            {
                "partner_code": "REDBEARD",
                "trial_status": "active",
                "trial_started_at": "2024-01-01T00:00:00+00:00",
                "trial_expires_at": "2099-01-15T00:00:00+00:00",  # Far future
                "total_credits_used": 5.50,
                "total_tokens_used": 100000,
                "total_requests_made": 50,
            }
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        status = PartnerTrialService.get_partner_trial_status(123)

        assert status["has_partner_trial"] is True
        assert status["partner_code"] == "REDBEARD"
        assert status["trial_status"] == "active"
        assert status["is_expired"] is False
        assert status["credits_used"] == 5.50

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_trial_status_no_trial(self, mock_get_client):
        """Test getting status when user has no partner trial"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = (
            mock_result
        )

        status = PartnerTrialService.get_partner_trial_status(123)

        assert status["has_partner_trial"] is False

    @patch("src.services.partner_trial_service.get_supabase_client")
    def test_get_partner_analytics(self, mock_get_client):
        """Test getting partner analytics"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_result = MagicMock()
        mock_result.data = [
            {"trial_status": "active", "conversion_revenue_usd": None},
            {"trial_status": "converted", "conversion_revenue_usd": 19.99},
            {"trial_status": "converted", "conversion_revenue_usd": 29.99},
            {"trial_status": "expired", "conversion_revenue_usd": None},
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            mock_result
        )

        analytics = PartnerTrialService.get_partner_analytics("REDBEARD")

        assert analytics["partner_code"] == "REDBEARD"
        assert analytics["total_trials"] == 4
        assert analytics["active_trials"] == 1
        assert analytics["converted_trials"] == 2
        assert analytics["expired_trials"] == 1
        assert analytics["conversion_rate_percent"] == 50.0
        assert analytics["total_revenue_usd"] == 49.98

    def test_cache_invalidation(self):
        """Test cache invalidation"""
        # Add something to cache
        PartnerTrialService._set_cached_config("TEST", {"partner_code": "TEST"})

        # Verify it's cached
        cached = PartnerTrialService._get_cached_config("TEST")
        assert cached is not None

        # Invalidate specific key
        PartnerTrialService.invalidate_cache("TEST")
        cached = PartnerTrialService._get_cached_config("TEST")
        assert cached is None

    def test_cache_invalidation_all(self):
        """Test invalidating entire cache"""
        # Add multiple items
        PartnerTrialService._set_cached_config("TEST1", {"partner_code": "TEST1"})
        PartnerTrialService._set_cached_config("TEST2", {"partner_code": "TEST2"})

        # Invalidate all
        PartnerTrialService.invalidate_cache()

        assert PartnerTrialService._get_cached_config("TEST1") is None
        assert PartnerTrialService._get_cached_config("TEST2") is None
