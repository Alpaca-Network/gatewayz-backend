#!/usr/bin/env python3
"""
Tests for Authentication Rate Limiting Module

Tests IP-based rate limiting for authentication endpoints.
"""

from unittest.mock import MagicMock

import pytest

from src.services.auth_rate_limiting import (
    AuthRateLimitConfig,
    AuthRateLimiter,
    AuthRateLimitResult,
    AuthRateLimitType,
    check_auth_rate_limit,
    get_auth_rate_limiter,
    get_client_ip,
)


class TestAuthRateLimitConfig:
    """Tests for AuthRateLimitConfig"""

    def test_default_config_values(self):
        """Test default configuration values"""
        config = AuthRateLimitConfig()

        # Login limits
        assert config.login_attempts_per_window == 10
        assert config.login_window_seconds == 900  # 15 minutes

        # Registration limits
        assert config.register_attempts_per_window == 3
        assert config.register_window_seconds == 3600  # 1 hour

        # Password reset limits
        assert config.password_reset_attempts_per_window == 3
        assert config.password_reset_window_seconds == 3600  # 1 hour

        # API key creation limits
        assert config.api_key_create_attempts_per_window == 10
        assert config.api_key_create_window_seconds == 3600  # 1 hour

    def test_custom_config(self):
        """Test custom configuration values"""
        config = AuthRateLimitConfig(
            login_attempts_per_window=5,
            login_window_seconds=300,
            register_attempts_per_window=1,
        )

        assert config.login_attempts_per_window == 5
        assert config.login_window_seconds == 300
        assert config.register_attempts_per_window == 1


class TestAuthRateLimiter:
    """Tests for AuthRateLimiter"""

    @pytest.fixture
    def limiter(self):
        """Create a fresh rate limiter for each test"""
        return AuthRateLimiter(
            config=AuthRateLimitConfig(
                login_attempts_per_window=3,
                login_window_seconds=60,
                register_attempts_per_window=2,
                register_window_seconds=60,
                password_reset_attempts_per_window=2,
                password_reset_window_seconds=60,
                api_key_create_attempts_per_window=2,
                api_key_create_window_seconds=60,
            )
        )

    @pytest.mark.asyncio
    async def test_login_rate_limit_allows_under_limit(self, limiter):
        """Test that requests under limit are allowed"""
        ip = "192.168.1.1"

        # First 3 requests should be allowed
        for i in range(3):
            result = await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
            assert result.allowed is True
            assert result.remaining == 3 - i - 1

    @pytest.mark.asyncio
    async def test_login_rate_limit_blocks_over_limit(self, limiter):
        """Test that requests over limit are blocked"""
        ip = "192.168.1.1"

        # Exhaust the limit
        for _ in range(3):
            await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)

        # 4th request should be blocked
        result = await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after is not None
        assert result.retry_after > 0
        assert result.reason == "login rate limit exceeded"

    @pytest.mark.asyncio
    async def test_register_rate_limit(self, limiter):
        """Test registration rate limiting"""
        ip = "10.0.0.1"

        # First 2 requests allowed
        result1 = await limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
        assert result1.allowed is True
        assert result1.remaining == 1

        result2 = await limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
        assert result2.allowed is True
        assert result2.remaining == 0

        # 3rd request blocked
        result3 = await limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
        assert result3.allowed is False
        assert result3.reason == "register rate limit exceeded"

    @pytest.mark.asyncio
    async def test_password_reset_rate_limit(self, limiter):
        """Test password reset rate limiting"""
        ip = "172.16.0.1"

        # First 2 requests allowed
        for _ in range(2):
            result = await limiter.check_rate_limit(ip, AuthRateLimitType.PASSWORD_RESET)
            assert result.allowed is True

        # 3rd request blocked
        result = await limiter.check_rate_limit(ip, AuthRateLimitType.PASSWORD_RESET)
        assert result.allowed is False
        assert result.reason == "password_reset rate limit exceeded"

    @pytest.mark.asyncio
    async def test_api_key_create_rate_limit(self, limiter):
        """Test API key creation rate limiting"""
        user_id = "user_123"

        # First 2 requests allowed
        for _ in range(2):
            result = await limiter.check_rate_limit(user_id, AuthRateLimitType.API_KEY_CREATE)
            assert result.allowed is True

        # 3rd request blocked
        result = await limiter.check_rate_limit(user_id, AuthRateLimitType.API_KEY_CREATE)
        assert result.allowed is False
        assert result.reason == "api_key_create rate limit exceeded"

    @pytest.mark.asyncio
    async def test_different_ips_have_separate_limits(self, limiter):
        """Test that different IPs are rate limited independently"""
        ip1 = "192.168.1.1"
        ip2 = "192.168.1.2"

        # Exhaust limit for ip1
        for _ in range(3):
            await limiter.check_rate_limit(ip1, AuthRateLimitType.LOGIN)

        # ip1 should be blocked
        result1 = await limiter.check_rate_limit(ip1, AuthRateLimitType.LOGIN)
        assert result1.allowed is False

        # ip2 should still be allowed
        result2 = await limiter.check_rate_limit(ip2, AuthRateLimitType.LOGIN)
        assert result2.allowed is True

    @pytest.mark.asyncio
    async def test_different_limit_types_are_independent(self, limiter):
        """Test that different rate limit types are tracked independently"""
        ip = "192.168.1.1"

        # Exhaust login limit
        for _ in range(3):
            await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)

        # Login should be blocked
        login_result = await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        assert login_result.allowed is False

        # But registration should still be allowed
        register_result = await limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
        assert register_result.allowed is True

    @pytest.mark.asyncio
    async def test_get_remaining(self, limiter):
        """Test getting remaining attempts"""
        ip = "192.168.1.1"

        # Initial remaining
        remaining = await limiter.get_remaining(ip, AuthRateLimitType.LOGIN)
        assert remaining == 3

        # After one request
        await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        remaining = await limiter.get_remaining(ip, AuthRateLimitType.LOGIN)
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_reset_clears_limit(self, limiter):
        """Test that reset clears the rate limit"""
        ip = "192.168.1.1"

        # Exhaust limit
        for _ in range(3):
            await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)

        # Should be blocked
        result = await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        assert result.allowed is False

        # Reset
        await limiter.reset(ip, AuthRateLimitType.LOGIN)

        # Should be allowed again
        result = await limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        assert result.allowed is True

    def test_mask_identifier_ipv4(self):
        """Test IP address masking for IPv4"""
        masked = AuthRateLimiter._mask_identifier("192.168.1.100")
        assert masked == "192.168.1.xxx"

    def test_mask_identifier_short_string(self):
        """Test identifier masking for short strings"""
        masked = AuthRateLimiter._mask_identifier("user123")
        assert masked == "user123"

    def test_mask_identifier_long_string(self):
        """Test identifier masking for long strings"""
        masked = AuthRateLimiter._mask_identifier("user_123456789")
        assert masked == "user_123..."

    def test_mask_identifier_empty(self):
        """Test identifier masking for empty/None values"""
        masked = AuthRateLimiter._mask_identifier("")
        assert masked == "unknown"

        masked = AuthRateLimiter._mask_identifier(None)
        assert masked == "unknown"


class TestGetClientIP:
    """Tests for get_client_ip helper function"""

    def test_get_ip_from_x_real_ip(self):
        """Test extracting IP from X-Real-IP header (highest priority)"""
        request = MagicMock()
        request.headers = {
            "X-Real-IP": "10.20.30.40",
            "X-Forwarded-For": "203.0.113.195, 70.41.3.18",
        }
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        ip = get_client_ip(request)
        assert ip == "10.20.30.40"

    def test_get_ip_from_x_forwarded_for_rightmost(self):
        """Test extracting rightmost IP from X-Forwarded-For header (proxy-added)"""
        request = MagicMock()
        # Format: "client, proxy1, proxy2" - rightmost is added by our trusted proxy
        request.headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18, 150.172.238.178"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        ip = get_client_ip(request)
        # Should return rightmost IP (added by Railway proxy, harder to spoof)
        assert ip == "150.172.238.178"

    def test_get_ip_from_x_forwarded_for_single(self):
        """Test extracting IP from X-Forwarded-For header with single IP"""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.195"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        ip = get_client_ip(request)
        assert ip == "203.0.113.195"

    def test_get_ip_from_client_host(self):
        """Test extracting IP from client.host when no X-Forwarded-For"""
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"

        ip = get_client_ip(request)
        assert ip == "192.168.1.100"

    def test_get_ip_unknown_when_no_client(self):
        """Test returning 'unknown' when no client info available"""
        request = MagicMock()
        request.headers = {}
        request.client = None

        ip = get_client_ip(request)
        assert ip == "unknown"

    def test_spoofed_x_forwarded_for_uses_proxy_added_ip(self):
        """Test that spoofed X-Forwarded-For headers don't bypass rate limiting"""
        request = MagicMock()
        # Attacker tries to spoof by adding fake IPs at the start
        # But Railway's proxy adds the real IP at the end
        request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 192.168.1.100"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        ip = get_client_ip(request)
        # Should use rightmost IP (what Railway's proxy actually added)
        assert ip == "192.168.1.100"


class TestGlobalRateLimiter:
    """Tests for global rate limiter functions"""

    @pytest.mark.asyncio
    async def test_get_auth_rate_limiter_returns_singleton(self):
        """Test that get_auth_rate_limiter returns the same instance"""
        limiter1 = get_auth_rate_limiter()
        limiter2 = get_auth_rate_limiter()
        assert limiter1 is limiter2

    @pytest.mark.asyncio
    async def test_check_auth_rate_limit_convenience_function(self):
        """Test the convenience function for checking rate limits"""
        # Reset the global limiter to ensure clean state
        from src.services import auth_rate_limiting

        auth_rate_limiting._auth_rate_limiter = None

        result = await check_auth_rate_limit("test_ip_123", AuthRateLimitType.LOGIN)
        assert isinstance(result, AuthRateLimitResult)
        assert result.allowed is True
