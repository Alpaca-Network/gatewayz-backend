"""
Tests for safe data access patterns - ensuring no IndexError on empty lists.

This test suite validates fixes for unsafe .data[0] access patterns where
database queries could return empty lists [] causing IndexError.
"""

import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.db import rate_limits, users


class TestUsersDataAccessSafety:
    """Test safe data access in users.py"""

    @patch("src.db.users.get_supabase_client")
    @patch("src.db.users.track_database_query")
    def test_deduct_credits_v2_handles_empty_concurrent_balance_check(
        self, mock_track, mock_get_client
    ):
        """Test that concurrent modification error handling doesn't crash on empty data"""
        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_track.return_value.__enter__ = Mock()
        mock_track.return_value.__exit__ = Mock()

        # Mock initial balance read
        initial_result = Mock()
        initial_result.data = [{"credits": 100.0}]

        # Mock failed update (concurrent modification)
        update_result = Mock()
        update_result.data = []  # Empty list - concurrent modification detected

        # Mock the re-fetch that returns empty list (edge case)
        refetch_result = Mock()
        refetch_result.data = []  # This should be handled gracefully

        mock_client.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            initial_result,  # Initial balance check
            refetch_result,  # Re-fetch after concurrent modification
        ]
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = (
            update_result
        )

        # Execute - should raise ValueError with "unknown" balance, not IndexError
        with pytest.raises(ValueError) as exc_info:
            users.deduct_credits_v2(user_id=1, tokens=10.0, model_id="test-model", provider="test")

        # Verify error message contains "unknown" instead of crashing
        assert "unknown" in str(exc_info.value).lower()
        assert "concurrent modification" in str(exc_info.value).lower()


class TestRateLimitsDataAccessSafety:
    """Test safe data access in rate_limits.py"""

    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_user_rate_limits_handles_empty_key_record(self, mock_get_client):
        """Test that empty key record is handled gracefully"""
        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock empty key record (edge case)
        key_record = Mock()
        key_record.data = []  # Empty list

        # Mock fallback rate limits query
        fallback_result = Mock()
        fallback_result.data = []  # Also empty

        mock_client.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            key_record,  # api_keys_new query returns empty
            fallback_result,  # rate_limits fallback also empty
        ]

        # Execute - should return None, not crash
        result = rate_limits.get_user_rate_limits("test_key")

        # Verify
        assert result is None

    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_user_rate_limits_handles_empty_config_record(self, mock_get_client):
        """Test that empty rate config record is handled gracefully"""
        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock key record with data
        key_record = Mock()
        key_record.data = [{"id": 123}]

        # Mock empty rate config (edge case)
        rate_config = Mock()
        rate_config.data = []  # Empty list

        # Mock fallback rate limits query
        fallback_result = Mock()
        fallback_result.data = []

        mock_client.table.return_value.select.return_value.eq.return_value.execute.side_effect = [
            key_record,  # api_keys_new query
            rate_config,  # rate_limit_configs query returns empty
            fallback_result,  # Fallback also empty
        ]

        # Execute - should return None, not crash
        result = rate_limits.get_user_rate_limits("test_key")

        # Verify
        assert result is None

    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_rate_limit_config_handles_empty_results(self, mock_get_client):
        """Test get_rate_limit_config handles empty results at all levels"""
        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # All queries return empty lists
        empty_result = Mock()
        empty_result.data = []

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            empty_result
        )

        # Execute - should return default config, not crash
        result = rate_limits.get_rate_limit_config("test_key")

        # Verify - should get default config
        assert result is not None
        assert result["requests_per_minute"] == 60
        assert result["concurrency_limit"] == 50

    @patch("src.db.rate_limits.get_supabase_client")
    def test_update_rate_limit_usage_handles_empty_existing_record(self, mock_get_client):
        """Test that empty existing record check is handled gracefully"""
        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = Mock(
            data=[]
        )  # Table exists check

        # Mock get_user returning a user
        with patch("src.db.rate_limits.get_user") as mock_get_user:
            mock_get_user.return_value = {"id": 1}

            # Mock empty existing record
            empty_existing = Mock()
            empty_existing.data = []

            # Table exists, but no existing record
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
                empty_existing
            )
            mock_client.table.return_value.insert.return_value.execute.return_value = Mock(
                data=[{"id": 1}]
            )

            # Execute - should insert new record, not crash
            rate_limits.update_rate_limit_usage("test_key", tokens_used=100)

            # Verify insert was called (since existing was empty)
            assert mock_client.table.return_value.insert.called


class TestAuthDataAccessSafety:
    """Test safe data access in auth.py routes"""

    @patch("src.routes.auth.get_supabase_client")
    def test_password_reset_handles_empty_user_result(self, mock_get_client):
        """Test password reset doesn't crash on empty user result"""
        from src.routes.auth import request_password_reset
        from src.schemas.auth import PasswordResetRequest

        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock empty user result
        user_result = Mock()
        user_result.data = []  # No user found

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
            user_result
        )

        # Execute - should return success message (security), not crash
        result = request_password_reset(PasswordResetRequest(email="test@example.com"))

        # Verify - should return generic message for security
        assert "If an account with that email exists" in result["message"]

    @patch("src.routes.auth.get_supabase_client")
    def test_reset_password_handles_empty_token_result(self, mock_get_client):
        """Test reset password handles empty token result"""
        from fastapi import HTTPException

        from src.routes.auth import reset_password
        from src.schemas.auth import PasswordResetConfirm

        # Setup
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock empty token result
        token_result = Mock()
        token_result.data = []  # No valid token

        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            token_result
        )

        # Execute - should raise HTTPException, not IndexError
        with pytest.raises(HTTPException) as exc_info:
            reset_password(PasswordResetConfirm(token="invalid_token", new_password="newpass123"))

        # Verify correct error
        assert exc_info.value.status_code == 400
        assert "Invalid or expired reset token" in exc_info.value.detail


class TestRateLimitingConcurrencyReenabled:
    """Test that concurrency limits are properly re-enabled"""

    @pytest.mark.asyncio
    @patch("src.services.rate_limiting.logger")
    async def test_concurrency_limit_enforced(self, mock_logger):
        """Test that concurrency limits are now enforced (not disabled)"""
        from src.services.rate_limiting import RateLimitConfig, RateLimitService

        # Setup
        service = RateLimitService()
        config = RateLimitConfig(
            requests_per_minute=100,
            tokens_per_minute=10000,
            burst_limit=10,
            concurrency_limit=5,
            window_size_seconds=60,
        )

        # Simulate 5 concurrent requests (at limit)
        api_key = "test_key"
        service.concurrent_requests[api_key] = 5

        # Execute concurrency check
        result = await service._check_concurrency_limit(api_key, config)

        # Verify - should be blocked (concurrency limit enforced)
        assert result["allowed"] is False
        assert result["current"] == 5
        assert result["limit"] == 5
        assert result["remaining"] == 0

    @pytest.mark.asyncio
    @patch("src.services.rate_limiting_fallback.logger")
    async def test_fallback_concurrency_limit_enforced(self, mock_logger):
        """Test that fallback concurrency limits are now enforced (not disabled)"""
        from src.services.rate_limiting_fallback import (
            FallbackRateLimiter,
            RateLimitConfig,
        )

        # Setup
        limiter = FallbackRateLimiter()
        config = RateLimitConfig(
            requests_per_minute=100,
            tokens_per_minute=10000,
            burst_limit=10,
            concurrency_limit=5,
            window_size_seconds=60,
        )

        # Simulate 5 concurrent requests (at limit)
        api_key = "test_key"
        limiter.concurrent_requests[api_key] = 5

        # Execute
        result = await limiter.check_rate_limit(api_key, config, tokens=100)

        # Verify - should be blocked with correct reason
        assert result.allowed is False
        assert result.reason == "Concurrency limit exceeded"

    @pytest.mark.asyncio
    async def test_concurrency_limit_allows_under_limit(self):
        """Test that requests under concurrency limit are allowed"""
        from src.services.rate_limiting import RateLimitConfig, RateLimitService

        # Setup
        service = RateLimitService()
        config = RateLimitConfig(
            requests_per_minute=100,
            tokens_per_minute=10000,
            burst_limit=10,
            concurrency_limit=5,
            window_size_seconds=60,
        )

        # Simulate 3 concurrent requests (under limit)
        api_key = "test_key"
        service.concurrent_requests[api_key] = 3

        # Execute
        result = await service._check_concurrency_limit(api_key, config)

        # Verify - should be allowed
        assert result["allowed"] is True
        assert result["current"] == 3
        assert result["limit"] == 5
        assert result["remaining"] == 2

    @pytest.mark.asyncio
    @patch("src.services.rate_limiting.get_fallback_rate_limit_manager")
    async def test_main_limiter_increments_concurrency_on_allowed_request(self, mock_get_fallback):
        """Test that SlidingWindowRateLimiter increments concurrency counter when request allowed"""
        from src.services.rate_limiting import (
            RateLimitConfig,
        )
        from src.services.rate_limiting import RateLimitResult as FallbackResult
        from src.services.rate_limiting import (
            SlidingWindowRateLimiter,
        )

        # Setup mock fallback manager
        mock_fallback = Mock()
        mock_fallback.check_rate_limit = AsyncMock(
            return_value=FallbackResult(
                allowed=True,
                remaining_requests=100,
                remaining_tokens=10000,
                reset_time=int(time.time()) + 60,
                retry_after=None,
                reason=None,
            )
        )
        mock_get_fallback.return_value = mock_fallback

        # Create limiter instance
        limiter = SlidingWindowRateLimiter()
        api_key = "test_key_increment"
        config = RateLimitConfig(concurrency_limit=10)

        # Verify counter starts at 0
        assert limiter.concurrent_requests[api_key] == 0

        # Make request (should be allowed)
        result = await limiter.check_rate_limit(api_key, config, tokens_used=100)

        # Verify request was allowed
        assert result.allowed is True

        # CRITICAL: Verify concurrency counter was incremented
        assert (
            limiter.concurrent_requests[api_key] == 1
        ), "Concurrency counter should increment on allowed request"

    @pytest.mark.asyncio
    @patch("src.services.rate_limiting.get_fallback_rate_limit_manager")
    async def test_main_limiter_does_not_increment_on_rejected_request(self, mock_get_fallback):
        """Test that concurrency counter is NOT incremented when request is rejected"""
        from src.services.rate_limiting import (
            RateLimitConfig,
        )
        from src.services.rate_limiting import RateLimitResult as FallbackResult
        from src.services.rate_limiting import (
            SlidingWindowRateLimiter,
        )

        # Setup mock fallback manager
        mock_fallback = Mock()
        mock_fallback.check_rate_limit = AsyncMock(
            return_value=FallbackResult(
                allowed=False,  # Request rejected
                remaining_requests=0,
                remaining_tokens=0,
                reset_time=int(time.time()) + 60,
                retry_after=60,
                reason="Rate limit exceeded",
            )
        )
        mock_get_fallback.return_value = mock_fallback

        # Create limiter instance
        limiter = SlidingWindowRateLimiter()
        api_key = "test_key_no_increment"
        config = RateLimitConfig(concurrency_limit=10)

        # Verify counter starts at 0
        assert limiter.concurrent_requests[api_key] == 0

        # Make request (should be rejected)
        result = await limiter.check_rate_limit(api_key, config, tokens_used=100)

        # Verify request was rejected
        assert result.allowed is False

        # CRITICAL: Verify concurrency counter was NOT incremented
        assert (
            limiter.concurrent_requests[api_key] == 0
        ), "Concurrency counter should NOT increment on rejected request"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
