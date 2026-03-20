"""
Tests for anonymous rate limiter service.

This test suite validates:
- Model whitelist enforcement (only :free models allowed)
- IP-based rate limiting (3 requests/day)
- Redis fallback to in-memory storage
- Rate limit counters and tracking
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.anonymous_rate_limiter import (
    ANONYMOUS_DAILY_LIMIT,
    _anonymous_usage_cache,
    _hash_ip,
    check_anonymous_rate_limit,
    get_anonymous_stats,
    get_anonymous_usage_count,
    increment_anonymous_usage,
    is_model_allowed_for_anonymous,
    record_anonymous_request,
    validate_anonymous_request,
)


class TestModelWhitelist:
    """Tests for anonymous model whitelist"""

    def test_free_model_allowed(self):
        """Test that whitelisted free models are allowed"""
        assert is_model_allowed_for_anonymous("google/gemini-2.0-flash-exp:free") is True
        assert is_model_allowed_for_anonymous("google/gemma-2-9b-it:free") is True
        assert is_model_allowed_for_anonymous("mistralai/mistral-7b-instruct:free") is True

    def test_non_free_model_rejected(self):
        """Test that non-free models are rejected"""
        assert is_model_allowed_for_anonymous("openai/gpt-4") is False
        assert is_model_allowed_for_anonymous("anthropic/claude-3-opus") is False
        assert is_model_allowed_for_anonymous("google/gemini-pro") is False

    def test_free_suffix_but_not_whitelisted(self):
        """Test that models with :free suffix but not whitelisted are rejected"""
        # These end with :free but aren't in the whitelist
        assert is_model_allowed_for_anonymous("unknown/model:free") is False
        assert is_model_allowed_for_anonymous("fake/notreal:free") is False

    def test_model_case_sensitivity(self):
        """Test model matching is case-insensitive"""
        assert is_model_allowed_for_anonymous("GOOGLE/GEMINI-2.0-FLASH-EXP:FREE") is True
        assert is_model_allowed_for_anonymous("Google/Gemini-2.0-Flash-Exp:Free") is True

    def test_empty_and_none_models(self):
        """Test handling of empty and None models"""
        assert is_model_allowed_for_anonymous("") is False
        assert is_model_allowed_for_anonymous(None) is False

    def test_model_without_free_suffix(self):
        """Test that models without :free suffix are rejected even if similar"""
        # Even if the base model name matches, must have :free suffix
        assert is_model_allowed_for_anonymous("google/gemini-2.0-flash-exp") is False


class TestAnonymousRateLimiting:
    """Tests for IP-based rate limiting"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear in-memory cache before each test"""
        _anonymous_usage_cache.clear()
        yield
        _anonymous_usage_cache.clear()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_rate_limit_allows_first_request(self, mock_redis):
        """Test that first request from IP is allowed"""
        mock_redis.return_value = None  # Use memory fallback

        result = check_anonymous_rate_limit("192.168.1.1")

        assert result["allowed"] is True
        assert result["remaining"] == ANONYMOUS_DAILY_LIMIT
        assert result["limit"] == ANONYMOUS_DAILY_LIMIT

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_rate_limit_blocks_after_limit(self, mock_redis):
        """Test that requests are blocked after daily limit"""
        mock_redis.return_value = None  # Use memory fallback
        ip = "192.168.1.2"

        # Use up all requests
        for _ in range(ANONYMOUS_DAILY_LIMIT):
            increment_anonymous_usage(ip)

        result = check_anonymous_rate_limit(ip)

        assert result["allowed"] is False
        assert result["remaining"] == 0
        assert "exceeded" in result["reason"].lower()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_rate_limit_decrements_remaining(self, mock_redis):
        """Test that remaining count decreases properly"""
        mock_redis.return_value = None  # Use memory fallback
        ip = "192.168.1.3"

        # First request
        result1 = check_anonymous_rate_limit(ip)
        assert result1["remaining"] == ANONYMOUS_DAILY_LIMIT

        # Record usage
        increment_anonymous_usage(ip)

        # Second request
        result2 = check_anonymous_rate_limit(ip)
        assert result2["remaining"] == ANONYMOUS_DAILY_LIMIT - 1

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_different_ips_tracked_separately(self, mock_redis):
        """Test that different IPs have separate rate limits"""
        mock_redis.return_value = None  # Use memory fallback

        ip1 = "192.168.1.10"
        ip2 = "192.168.1.20"

        # Use up ip1's quota
        for _ in range(ANONYMOUS_DAILY_LIMIT):
            increment_anonymous_usage(ip1)

        # ip1 should be blocked
        result1 = check_anonymous_rate_limit(ip1)
        assert result1["allowed"] is False

        # ip2 should still be allowed
        result2 = check_anonymous_rate_limit(ip2)
        assert result2["allowed"] is True
        assert result2["remaining"] == ANONYMOUS_DAILY_LIMIT


class TestValidateAnonymousRequest:
    """Tests for full request validation (model + rate limit)"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear in-memory cache before each test"""
        _anonymous_usage_cache.clear()
        yield
        _anonymous_usage_cache.clear()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_valid_request_allowed(self, mock_redis):
        """Test that valid request with free model and under limit is allowed"""
        mock_redis.return_value = None

        result = validate_anonymous_request(
            ip_address="10.0.0.1", model_id="google/gemini-2.0-flash-exp:free"
        )

        assert result["allowed"] is True
        assert result["model_allowed"] is True
        assert result["rate_limit_allowed"] is True

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_non_free_model_rejected(self, mock_redis):
        """Test that non-free model request is rejected"""
        mock_redis.return_value = None

        result = validate_anonymous_request(ip_address="10.0.0.2", model_id="openai/gpt-4")

        assert result["allowed"] is False
        assert result["model_allowed"] is False
        assert "not available for anonymous" in result["reason"].lower()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_rate_limit_exceeded_rejected(self, mock_redis):
        """Test that request over rate limit is rejected"""
        mock_redis.return_value = None
        ip = "10.0.0.3"

        # Use up quota
        for _ in range(ANONYMOUS_DAILY_LIMIT):
            increment_anonymous_usage(ip)

        result = validate_anonymous_request(
            ip_address=ip, model_id="google/gemini-2.0-flash-exp:free"
        )

        assert result["allowed"] is False
        assert result["model_allowed"] is True
        assert result["rate_limit_allowed"] is False
        assert "limit exceeded" in result["reason"].lower()


class TestRecordAnonymousRequest:
    """Tests for recording anonymous requests"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear in-memory cache before each test"""
        _anonymous_usage_cache.clear()
        yield
        _anonymous_usage_cache.clear()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_record_increments_count(self, mock_redis):
        """Test that recording a request increments the counter"""
        mock_redis.return_value = None
        ip = "172.16.0.1"

        initial_count = get_anonymous_usage_count(ip)
        assert initial_count == 0

        record_anonymous_request(ip, "google/gemini-2.0-flash-exp:free")

        new_count = get_anonymous_usage_count(ip)
        assert new_count == 1

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_record_returns_remaining(self, mock_redis):
        """Test that recording returns correct remaining count"""
        mock_redis.return_value = None
        ip = "172.16.0.2"

        result = record_anonymous_request(ip, "google/gemini-2.0-flash-exp:free")

        assert result["count"] == 1
        assert result["remaining"] == ANONYMOUS_DAILY_LIMIT - 1
        assert result["limit"] == ANONYMOUS_DAILY_LIMIT


class TestIPHashing:
    """Tests for IP hashing (privacy)"""

    def test_ip_hashing_consistency(self):
        """Test that same IP produces same hash"""
        ip = "192.168.1.100"
        hash1 = _hash_ip(ip)
        hash2 = _hash_ip(ip)
        assert hash1 == hash2

    def test_ip_hashing_different_ips(self):
        """Test that different IPs produce different hashes"""
        hash1 = _hash_ip("192.168.1.1")
        hash2 = _hash_ip("192.168.1.2")
        assert hash1 != hash2

    def test_ip_hash_length(self):
        """Test that hash is truncated to expected length"""
        hash_result = _hash_ip("10.0.0.1")
        assert len(hash_result) == 32


class TestAnonymousStats:
    """Tests for anonymous usage statistics"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear in-memory cache before each test"""
        _anonymous_usage_cache.clear()
        yield
        _anonymous_usage_cache.clear()

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_stats_empty(self, mock_redis):
        """Test stats when no usage"""
        mock_redis.return_value = None

        stats = get_anonymous_stats()

        assert stats["unique_ips_today"] == 0
        assert stats["total_requests_today"] == 0
        assert stats["storage"] == "memory"

    @patch("src.services.anonymous_rate_limiter._get_redis_client")
    def test_stats_with_usage(self, mock_redis):
        """Test stats after some usage"""
        mock_redis.return_value = None

        # Record some usage
        record_anonymous_request("1.1.1.1", "google/gemini-2.0-flash-exp:free")
        record_anonymous_request("1.1.1.1", "google/gemini-2.0-flash-exp:free")
        record_anonymous_request("2.2.2.2", "google/gemini-2.0-flash-exp:free")

        stats = get_anonymous_stats()

        assert stats["unique_ips_today"] == 2
        assert stats["total_requests_today"] == 3
        assert stats["storage"] == "memory"


class TestRedisIntegration:
    """Tests for Redis integration"""

    def test_redis_increment(self):
        """Test Redis INCR operation"""
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [5, True]  # INCR returns 5, EXPIRE returns True
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.services.anonymous_rate_limiter._get_redis_client", return_value=mock_redis
        ):
            result = increment_anonymous_usage("test-ip")
            assert result == 5
            mock_pipe.incr.assert_called_once()
            mock_pipe.expire.assert_called_once()

    def test_redis_get(self):
        """Test Redis GET operation"""
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"3"

        with patch(
            "src.services.anonymous_rate_limiter._get_redis_client", return_value=mock_redis
        ):
            result = get_anonymous_usage_count("test-ip")
            assert result == 3

    def test_redis_fallback_on_error(self):
        """Test fallback to memory when Redis errors"""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis connection error")

        with patch(
            "src.services.anonymous_rate_limiter._get_redis_client", return_value=mock_redis
        ):
            _anonymous_usage_cache.clear()
            result = get_anonymous_usage_count("fallback-ip")
            # Should return 0 from memory fallback, not raise
            assert result == 0
