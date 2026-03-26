"""
Tests for rate limit header utilities.

Verifies that RateLimitResult objects are correctly converted to
IETF standard (RateLimit-*) and legacy (X-RateLimit-*) HTTP headers.
"""

import time
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from src.utils.rate_limit_headers import get_rate_limit_headers


@dataclass
class FakeRateLimitResult:
    """Minimal RateLimitResult-like object for testing."""

    allowed: bool = True
    remaining_requests: int = 50
    remaining_tokens: int = 9000
    ratelimit_limit_requests: int = 100
    ratelimit_limit_tokens: int = 10000
    ratelimit_reset_requests: int = 0  # Unix timestamp
    ratelimit_reset_tokens: int = 0  # Unix timestamp
    burst_window_description: str = "100 per 60 seconds"


class TestGetRateLimitHeaders:
    def test_returns_empty_dict_for_none(self):
        assert get_rate_limit_headers(None) == {}

    def test_returns_ietf_headers(self):
        now = int(time.time())
        result = FakeRateLimitResult(
            ratelimit_reset_requests=now + 60,
            ratelimit_reset_tokens=now + 60,
        )
        headers = get_rate_limit_headers(result)

        assert headers["RateLimit-Limit"] == "100"
        assert headers["RateLimit-Remaining"] == "50"
        # RateLimit-Reset is seconds-until-reset (delta), not a Unix timestamp
        reset = int(headers["RateLimit-Reset"])
        assert 0 <= reset <= 61

    def test_returns_legacy_headers(self):
        now = int(time.time())
        result = FakeRateLimitResult(
            ratelimit_reset_requests=now + 60,
            ratelimit_reset_tokens=now + 60,
        )
        headers = get_rate_limit_headers(result)

        assert headers["X-RateLimit-Limit-Requests"] == "100"
        assert headers["X-RateLimit-Remaining-Requests"] == "50"
        assert headers["X-RateLimit-Limit-Tokens"] == "10000"
        assert headers["X-RateLimit-Remaining-Tokens"] == "9000"

    def test_reset_is_delta_not_timestamp(self):
        """RateLimit-Reset must be seconds-until-reset per IETF draft, not a Unix ts."""
        now = int(time.time())
        result = FakeRateLimitResult(ratelimit_reset_requests=now + 120)
        headers = get_rate_limit_headers(result)

        reset = int(headers["RateLimit-Reset"])
        # Should be ~120 seconds, not a Unix timestamp like 1700000000
        assert reset <= 121
        assert reset >= 119

    def test_legacy_reset_is_unix_timestamp(self):
        """X-RateLimit-Reset-Requests should be a Unix timestamp (absolute)."""
        future_ts = int(time.time()) + 300
        result = FakeRateLimitResult(ratelimit_reset_requests=future_ts)
        headers = get_rate_limit_headers(result)

        assert headers["X-RateLimit-Reset-Requests"] == str(future_ts)

    def test_burst_window_included(self):
        result = FakeRateLimitResult(
            ratelimit_reset_requests=int(time.time()) + 60,
            burst_window_description="100 per 60 seconds",
        )
        headers = get_rate_limit_headers(result)
        assert headers["X-RateLimit-Burst-Window"] == "100 per 60 seconds"

    def test_skips_zero_limits(self):
        """Headers with 0 limits should not be emitted."""
        result = FakeRateLimitResult(
            ratelimit_limit_requests=0,
            ratelimit_limit_tokens=0,
            ratelimit_reset_requests=0,
            ratelimit_reset_tokens=0,
            burst_window_description="",
        )
        headers = get_rate_limit_headers(result)

        assert "RateLimit-Limit" not in headers
        assert "X-RateLimit-Limit-Tokens" not in headers
        # remaining_requests (50) should still be set
        assert headers["RateLimit-Remaining"] == "50"
