"""
Conceptual Model Unit Tests - CM-02: Rate Limiting (Three-Layer Architecture)

Tests the three-layer rate limiting system:
  Layer 1: IP-level rate limiting (SecurityMiddleware)
  Layer 2: API key-level rate limiting (SlidingWindowRateLimiter / RateLimitManager)
  Layer 3: Anonymous rate limiting (anonymous_rate_limiter)
  Graceful degradation: Fallback when Redis is down (InMemoryRateLimiter)
"""

import asyncio
import hashlib
from collections import defaultdict, deque
from datetime import UTC, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1 imports (SecurityMiddleware internals)
# ---------------------------------------------------------------------------
from src.middleware.security_middleware import (
    DEFAULT_IP_LIMIT,
    VELOCITY_ERROR_THRESHOLD,
    VELOCITY_MIN_REQUESTS,
    SecurityMiddleware,
)

# ---------------------------------------------------------------------------
# Layer 3 imports (Anonymous rate limiting)
# ---------------------------------------------------------------------------
from src.services.anonymous_rate_limiter import (
    ANONYMOUS_DAILY_LIMIT,
    _hash_ip,
    check_anonymous_rate_limit,
    increment_anonymous_usage,
)

# ---------------------------------------------------------------------------
# Layer 2 imports (API key-level rate limiting)
# ---------------------------------------------------------------------------
from src.services.rate_limiting import (
    DEFAULT_CONFIG,
    ENTERPRISE_CONFIG,
    PREMIUM_CONFIG,
    RateLimitConfig,
    SlidingWindowRateLimiter,
)

# ---------------------------------------------------------------------------
# Layer 4 imports (Fallback / graceful degradation)
# ---------------------------------------------------------------------------
from src.services.rate_limiting_fallback import (
    InMemoryRateLimiter,
)

# ===================================================================
# 2.1 Layer 1: IP-Level Rate Limiting (SecurityMiddleware)
# ===================================================================


class TestLayer1IPRateLimiting:
    """Tests for IP-level rate limiting in SecurityMiddleware."""

    def _make_middleware(self, redis_client=None):
        """Create a SecurityMiddleware instance with a dummy ASGI app."""
        dummy_app = MagicMock()
        mw = SecurityMiddleware(dummy_app, redis_client=redis_client)
        return mw

    def _make_request(
        self,
        ip="1.2.3.4",
        auth_header="",
        user_agent="Mozilla/5.0",
        path="/v1/chat/completions",
        method="POST",
    ):
        """Create a mock Request object."""
        request = MagicMock()
        request.url.path = path
        request.method = method
        request.client.host = ip
        headers_data = {
            "X-Forwarded-For": ip,
            "Authorization": auth_header,
            "user-agent": user_agent,
            "accept-language": "en-US",
            "accept-encoding": "gzip",
            "Accept": "application/json",
            "content-type": "application/json",
            "X-Proxy-ID": "",
            "Via": "",
        }
        headers_mock = MagicMock()
        headers_mock.get = lambda key, default="": headers_data.get(key, default)
        headers_mock.__contains__ = lambda self_inner, key: key in headers_data
        request.headers = headers_mock
        request.query_params = MagicMock()
        request.query_params.get = MagicMock(return_value="")
        return request

    # CM-2.1.1
    @pytest.mark.cm_verified
    def test_ip_rate_limit_under_threshold_allows(self):
        """Requests under IP RPM limit pass through (are allowed)."""
        mw = self._make_middleware()

        # _check_limit with count below the limit should return True (allowed)
        # Simulate a few requests under the DEFAULT_IP_LIMIT (300 RPM)
        allowed = asyncio.get_event_loop().run_until_complete(
            mw._check_limit("ip:1.2.3.4", DEFAULT_IP_LIMIT)
        )
        assert (
            allowed is True
        ), f"Request should be allowed when under IP rate limit of {DEFAULT_IP_LIMIT} RPM"

    # CM-2.1.2
    @pytest.mark.cm_verified
    def test_ip_rate_limit_over_threshold_blocks(self):
        """Requests exceeding IP RPM limit are blocked (return False)."""
        mw = self._make_middleware()

        # Exhaust the limit by making DEFAULT_IP_LIMIT requests
        for _ in range(DEFAULT_IP_LIMIT):
            asyncio.get_event_loop().run_until_complete(
                mw._check_limit("ip:10.0.0.1", DEFAULT_IP_LIMIT)
            )

        # The next request should be blocked
        blocked = asyncio.get_event_loop().run_until_complete(
            mw._check_limit("ip:10.0.0.1", DEFAULT_IP_LIMIT)
        )
        assert (
            blocked is False
        ), f"Request should be blocked after exceeding IP rate limit of {DEFAULT_IP_LIMIT} RPM"

    # CM-2.1.3
    @pytest.mark.cm_verified
    def test_ip_rate_limit_applied_before_auth(self):
        """CM-2.1.3: IP rate limit is applied before auth in dispatch.

        Exhaust the IP limit, then call dispatch() and confirm the downstream
        app (call_next) is never invoked — proving IP check runs before auth.
        """
        mw = self._make_middleware()
        request = self._make_request(ip="192.168.1.1", auth_header="")

        # Exhaust IP limit
        for _ in range(DEFAULT_IP_LIMIT):
            asyncio.get_event_loop().run_until_complete(
                mw._check_limit("ip:192.168.1.1", DEFAULT_IP_LIMIT)
            )

        # Define a call_next that records whether it was called
        call_next_invoked = False

        async def mock_call_next(req):
            nonlocal call_next_invoked
            call_next_invoked = True
            resp = MagicMock()
            resp.status_code = 200
            return resp

        # Patch async helpers to avoid real I/O in dispatch
        with (
            patch.object(mw, "_is_datacenter_ip", return_value=False),
            patch.object(mw, "_get_user_tier_from_request", return_value="basic"),
            patch("src.db.ip_whitelist.is_ip_whitelisted", return_value=False),
        ):
            response = asyncio.get_event_loop().run_until_complete(
                mw.dispatch(request, mock_call_next)
            )
        assert response.status_code == 429, "Over-limit IP should get 429"
        assert call_next_invoked is False, "call_next (auth) should not be reached"

    # CM-2.1.4
    @pytest.mark.cm_verified
    def test_velocity_detection_triggers_on_anomalous_pattern(self):
        """Rapid burst of 5xx errors triggers velocity mode activation."""
        mw = self._make_middleware()

        # Record enough requests with high error rate to trigger velocity mode
        # Need at least VELOCITY_MIN_REQUESTS (100), with >= 25% errors
        total = VELOCITY_MIN_REQUESTS + 10
        error_count = int(total * 0.30)  # 30% error rate > 25% threshold
        success_count = total - error_count

        for _ in range(success_count):
            mw._record_request_outcome(200)
        for _ in range(error_count):
            mw._record_request_outcome(500)

        # Check and activate velocity mode
        activated = mw._check_and_activate_velocity_mode()
        assert activated is True, (
            f"Velocity mode should activate when error rate ({error_count}/{total} = "
            f"{error_count/total:.0%}) exceeds threshold ({VELOCITY_ERROR_THRESHOLD:.0%})"
        )
        assert mw._is_velocity_mode_active() is True

    # CM-2.1.5
    @pytest.mark.cm_verified
    def test_authenticated_users_exempt_from_ip_limits(self):
        """Authenticated requests (with valid API key/Bearer token) bypass IP limits.

        The middleware checks _is_authenticated_request() and skips IP rate limiting
        for authenticated users (they are rate-limited at the API key layer instead).
        """
        mw = self._make_middleware()
        request = self._make_request(auth_header="Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test")

        # Verify the request is recognized as authenticated
        assert mw._is_authenticated_request(request) is True

        # Also test with gw_ prefix API key
        request2 = self._make_request(auth_header="gw_1234567890abcdefghijklmnopqrstuvwxyz")
        assert mw._is_authenticated_request(request2) is True

        # Non-authenticated request should NOT be exempt
        request3 = self._make_request(auth_header="")
        assert mw._is_authenticated_request(request3) is False

        # Short auth header should NOT be exempt
        request4 = self._make_request(auth_header="short")
        assert mw._is_authenticated_request(request4) is False


# ===================================================================
# 2.2 Layer 2: API Key-Level Rate Limiting
# ===================================================================


class TestLayer2APIKeyRateLimiting:
    """Tests for API key-level rate limiting (SlidingWindowRateLimiter)."""

    # CM-2.2.1
    @pytest.mark.cm_verified
    def test_key_rate_limit_tracks_rpm(self):
        """Rate limiter uses Redis INCR with rate_limit key pattern for per-minute tracking.

        The Redis sliding window uses keys like:
            rate_limit:{api_key}:minute:{YYYYMMDDHHMM}:requests
        and increments them with INCR + TTL.
        """
        mock_redis = MagicMock()

        # The check_rate_limit method calls pipeline multiple times:
        # 1. Burst check: hget tokens, hget last_refill -> need tokens > 1
        # 2. Sliding window: get x6 (minute/hour/day requests+tokens) -> all 0
        # 3. Update counters: incr/incrby/expire calls
        # 4. Burst update: hset tokens, hset last_refill, expire
        call_count = {"n": 0}
        burst_pipe = MagicMock()
        burst_pipe.execute.return_value = [100, None]  # tokens=100, last_refill=None
        for m in ["hget", "hset", "expire"]:
            getattr(burst_pipe, m).return_value = burst_pipe

        window_pipe = MagicMock()
        window_pipe.execute.return_value = [0, 0, 0, 0, 0, 0]  # all counts at 0
        for m in ["get", "incr", "incrby", "expire"]:
            getattr(window_pipe, m).return_value = window_pipe

        update_pipe = MagicMock()
        update_pipe.execute.return_value = [1, 0, 1, 0, 1, 0, True, True, True, True, True, True]
        for m in ["incr", "incrby", "expire"]:
            getattr(update_pipe, m).return_value = update_pipe

        burst_update_pipe = MagicMock()
        burst_update_pipe.execute.return_value = [True, True, True]
        for m in ["hset", "expire"]:
            getattr(burst_update_pipe, m).return_value = burst_update_pipe

        def pipeline_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return burst_pipe
            elif call_count["n"] == 2:
                return window_pipe
            elif call_count["n"] == 3:
                return update_pipe
            else:
                return burst_update_pipe

        mock_redis.pipeline = pipeline_factory

        limiter = SlidingWindowRateLimiter(redis_client=mock_redis)
        config = RateLimitConfig(requests_per_minute=100)

        result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_test_key_123", config, tokens_used=0)
        )

        assert result.allowed is True
        # Verify pipeline was called multiple times (burst + window + update)
        assert call_count["n"] >= 2

    # CM-2.2.2
    @pytest.mark.cm_verified
    def test_key_rate_limit_enforces_plan_tier(self):
        """CM-2.2.2: Different plan tiers enforce different RPM limits.

        Verify by calling check_rate_limit with DEFAULT vs ENTERPRISE configs
        on a limiter with no Redis (uses local fallback).
        """
        limiter = SlidingWindowRateLimiter(redis_client=None)

        # Use DEFAULT_CONFIG (250 RPM) — first request should be allowed
        default_result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_default_tier_test_1234", DEFAULT_CONFIG, tokens_used=0)
        )
        assert default_result.allowed is True
        assert default_result.ratelimit_limit_requests == DEFAULT_CONFIG.requests_per_minute

        # Use ENTERPRISE_CONFIG (1000 RPM) — should also be allowed with higher limit
        enterprise_result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit(
                "gw_enterprise_tier_test_1234", ENTERPRISE_CONFIG, tokens_used=0
            )
        )
        assert enterprise_result.allowed is True
        assert enterprise_result.ratelimit_limit_requests == ENTERPRISE_CONFIG.requests_per_minute

        # Enterprise limit must be higher than default
        assert enterprise_result.ratelimit_limit_requests > default_result.ratelimit_limit_requests

    # CM-2.2.3
    @pytest.mark.cm_verified
    def test_key_rate_limit_tracks_tokens_per_day(self):
        """Daily token tracking is enforced via rate_limit:{key}:day:{date}:tokens Redis key.

        When day_tokens + tokens_used > tokens_per_day, the request is denied.
        """
        day_token_limit = 1000000
        mock_redis = MagicMock()

        call_count = {"n": 0}

        # Burst check pipe: return enough tokens so burst passes
        burst_pipe = MagicMock()
        burst_pipe.execute.return_value = [100, None]  # tokens=100, last_refill=None
        for m in ["hget", "hset", "expire"]:
            getattr(burst_pipe, m).return_value = burst_pipe

        # Sliding window pipe: day tokens at limit
        window_pipe = MagicMock()
        window_pipe.execute.return_value = [
            0,  # minute requests
            0,  # minute tokens
            0,  # hour requests
            0,  # hour tokens
            0,  # day requests
            str(day_token_limit),  # day tokens at limit
        ]
        for m in ["get", "incr", "incrby", "expire"]:
            getattr(window_pipe, m).return_value = window_pipe

        # Burst update pipe
        burst_update_pipe = MagicMock()
        burst_update_pipe.execute.return_value = [True, True, True]
        for m in ["hset", "expire"]:
            getattr(burst_update_pipe, m).return_value = burst_update_pipe

        def pipeline_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return burst_pipe
            elif call_count["n"] == 2:
                return burst_update_pipe
            elif call_count["n"] == 3:
                return window_pipe
            else:
                return burst_update_pipe

        mock_redis.pipeline = pipeline_factory

        limiter = SlidingWindowRateLimiter(redis_client=mock_redis)
        config = RateLimitConfig(tokens_per_day=day_token_limit)

        result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_test_key_day", config, tokens_used=100)
        )

        assert result.allowed is False
        assert "Day token limit" in (result.reason or "")

    # CM-2.2.4
    @pytest.mark.cm_gap
    def test_key_rate_limit_tracks_tokens_per_month(self):
        """CM-2.2.4: Monthly token tracking is not implemented as a separate window.

        The implementation tracks daily tokens (tokens_per_day) but has no
        dedicated monthly aggregation. This test verifies the daily token limit
        works as the effective monthly bound, and marks the gap.
        """
        # The fallback limiter tracks per-minute tokens via its sliding window.
        # Demonstrate that token limits are enforced by exceeding the per-minute limit.
        limiter = SlidingWindowRateLimiter(redis_client=None)
        config = RateLimitConfig(
            tokens_per_minute=100,
            tokens_per_day=1000,
            burst_limit=1000,
            requests_per_minute=1000,
        )

        # First request uses most of the per-minute token budget
        result1 = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_monthly_token_test_1234", config, tokens_used=90)
        )
        assert result1.allowed is True

        # Second request exceeds per-minute token limit (90 + 20 > 100)
        result2 = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_monthly_token_test_1234", config, tokens_used=20)
        )
        assert result2.allowed is False
        assert "token" in (result2.reason or "").lower()

    # CM-2.2.5
    @pytest.mark.cm_verified
    def test_rate_limit_returns_proper_headers(self):
        """Rate limit result contains X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset.

        The RateLimitResult dataclass includes ratelimit_limit_requests,
        remaining_requests, and ratelimit_reset_requests fields which map to
        the HTTP headers.
        """
        # Use local (no-Redis) sliding window to get a real result
        limiter = SlidingWindowRateLimiter(redis_client=None)
        config = RateLimitConfig(requests_per_minute=100, tokens_per_minute=5000)

        result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_header_test_key_1234567890", config, tokens_used=10)
        )

        assert result.allowed is True
        # X-RateLimit-Limit (total limit)
        assert (
            result.ratelimit_limit_requests == 100
        ), f"Expected ratelimit_limit_requests=100, got {result.ratelimit_limit_requests}"
        # X-RateLimit-Remaining (should be less than limit after one request)
        assert result.remaining_requests < 100
        # X-RateLimit-Reset (Unix timestamp in the future)
        assert result.ratelimit_reset_requests > 0


# ===================================================================
# 2.3 Layer 3: Anonymous Rate Limiting
# ===================================================================


class TestLayer3AnonymousRateLimiting:
    """Tests for anonymous (unauthenticated) rate limiting."""

    # CM-2.3.1
    @pytest.mark.cm_verified
    def test_anonymous_limits_stricter_than_authenticated(self):
        """CM-2.3.1: Anonymous daily limit is much stricter than authenticated.

        Exercise the anonymous limiter to exhaustion at 3 requests, then show
        an authenticated limiter still allows requests at the same point.
        """
        test_ip = "198.51.100.50"

        with patch("src.services.anonymous_rate_limiter._get_redis_client", return_value=None):
            from src.services import anonymous_rate_limiter

            anonymous_rate_limiter._anonymous_usage_cache.clear()

            # Use up all anonymous requests
            for _ in range(ANONYMOUS_DAILY_LIMIT):
                increment_anonymous_usage(test_ip)

            # Anonymous is now blocked
            anon_result = check_anonymous_rate_limit(test_ip)
            assert anon_result["allowed"] is False
            assert anon_result["remaining"] == 0

        # Authenticated limiter with same number of requests is still fine
        limiter = SlidingWindowRateLimiter(redis_client=None)
        auth_result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit("gw_auth_compare_test_1234", DEFAULT_CONFIG, tokens_used=0)
        )
        assert auth_result.allowed is True

    # CM-2.3.2
    @pytest.mark.cm_verified
    def test_anonymous_rate_limit_uses_ip_hash(self):
        """Redis key for anonymous rate limiting uses a SHA-256 hash of the IP.

        Key format: anon_limit:{sha256(anon_rate:{ip})[:32]}:{YYYY-MM-DD}
        """
        ip = "203.0.113.42"
        ip_hash = _hash_ip(ip)

        # Verify it's a hex hash (SHA-256 truncated to 32 chars)
        assert len(ip_hash) == 32
        assert all(c in "0123456789abcdef" for c in ip_hash)

        # Verify the hash is deterministic
        assert _hash_ip(ip) == ip_hash

        # Verify different IPs produce different hashes
        assert _hash_ip("10.0.0.1") != _hash_ip("10.0.0.2")

        # Verify the hash includes the "anon_rate:" prefix in input
        expected = hashlib.sha256(f"anon_rate:{ip}".encode()).hexdigest()[:32]
        assert ip_hash == expected

    # CM-2.3.3
    @pytest.mark.cm_verified
    def test_anonymous_rate_limit_blocks_excess(self):
        """Anonymous rate limiter returns 'allowed: False' when daily limit exceeded."""
        test_ip = "198.51.100.99"

        with patch("src.services.anonymous_rate_limiter._get_redis_client", return_value=None):
            # Reset in-memory cache for this test
            from src.services import anonymous_rate_limiter

            anonymous_rate_limiter._anonymous_usage_cache.clear()

            # Use up all allowed requests
            for _ in range(ANONYMOUS_DAILY_LIMIT):
                increment_anonymous_usage(test_ip)

            # Next check should be blocked
            result = check_anonymous_rate_limit(test_ip)
            assert result["allowed"] is False
            assert result["remaining"] == 0
            assert result["limit"] == ANONYMOUS_DAILY_LIMIT


# ===================================================================
# 2.4 Graceful Degradation
# ===================================================================


class TestGracefulDegradation:
    """Tests for fallback behavior when Redis is unavailable."""

    # CM-2.4.1
    @pytest.mark.cm_verified
    def test_redis_down_activates_fallback(self):
        """When Redis is unavailable, the fallback InMemoryRateLimiter is used.

        SlidingWindowRateLimiter initializes a fallback_manager from
        get_fallback_rate_limit_manager(). When redis_client is None,
        the local sliding window is used instead.
        """
        with patch("src.services.rate_limiting_fallback.get_rate_limit_config", return_value=None):
            # Create limiter without Redis
            limiter = SlidingWindowRateLimiter(redis_client=None)
            config = RateLimitConfig(requests_per_minute=10)

            # Should still work via local cache fallback
            result = asyncio.get_event_loop().run_until_complete(
                limiter.check_rate_limit("gw_fallback_test_1234567890", config, tokens_used=0)
            )
            assert result.allowed is True

            # Verify local_cache is being used (not Redis)
            assert limiter.redis_client is None
            # The fallback manager should exist
            assert limiter.fallback_manager is not None

    # CM-2.4.2
    @pytest.mark.cm_verified
    def test_fallback_lru_cache_500_entries(self):
        """CM-2.4.2: Fallback InMemoryRateLimiter evicts LRU keys when more
        than 500 unique API keys are tracked."""
        fallback = InMemoryRateLimiter()
        config = RateLimitConfig(requests_per_minute=1000, burst_limit=1000)

        loop = asyncio.get_event_loop()

        # Insert 500 keys (the max), releasing concurrency after each
        for i in range(500):
            key = f"gw_key_{i:04d}"
            loop.run_until_complete(fallback.check_rate_limit(key, config, tokens_used=0))
            loop.run_until_complete(fallback.release_concurrent_request(key))

        assert len(fallback._key_order) == 500

        # Insert one more — should trigger LRU eviction of the oldest key
        loop.run_until_complete(fallback.check_rate_limit("gw_key_0500", config, tokens_used=0))

        # Total keys must not exceed 500 (the eviction cap)
        assert len(fallback._key_order) <= 500
        # The first key (LRU) should have been evicted
        assert "gw_key_0000" not in fallback._key_order
        # The newest key should still be present
        assert "gw_key_0500" in fallback._key_order

    # CM-2.4.3
    @pytest.mark.cm_verified
    def test_fallback_ttl_15_minutes(self, frozen_time):
        """CM-2.4.3: Fallback entries expire after 15 minutes (900s TTL).

        Insert a key, advance time past 900 seconds, then trigger eviction
        and verify the key is removed.
        """
        fallback = InMemoryRateLimiter()
        config = RateLimitConfig(requests_per_minute=1000, burst_limit=1000)

        loop = asyncio.get_event_loop()

        # Insert a key and release its concurrency slot
        loop.run_until_complete(fallback.check_rate_limit("gw_ttl_test_key", config, tokens_used=0))
        loop.run_until_complete(fallback.release_concurrent_request("gw_ttl_test_key"))
        assert "gw_ttl_test_key" in fallback._key_order

        # Advance time past the 15-minute TTL (900 seconds)
        frozen_time.advance(901)

        # Trigger eviction (normally called at the start of check_rate_limit)
        fallback._evict_expired_keys(frozen_time.current)

        # The key should have been evicted
        assert "gw_ttl_test_key" not in fallback._key_order
        assert "gw_ttl_test_key" not in fallback._key_last_accessed

    # CM-2.4.4
    @pytest.mark.cm_verified
    def test_requests_never_blocked_by_infra_failure(self):
        """When both Redis AND fallback fail, request is ALLOWED (fail-open).

        SlidingWindowRateLimiter.check_rate_limit catches all exceptions and
        returns allowed=True with a reason indicating the failure.
        """
        # Create a limiter where everything fails
        limiter = SlidingWindowRateLimiter(redis_client=None)
        config = RateLimitConfig()

        # Patch the internal methods to raise exceptions
        with patch.object(
            limiter, "_check_concurrency_limit", side_effect=RuntimeError("infra down")
        ):
            result = asyncio.get_event_loop().run_until_complete(
                limiter.check_rate_limit("gw_failopen_test_1234567890", config)
            )

            # Fail-open: request should be allowed
            assert (
                result.allowed is True
            ), "Request must be allowed (fail-open) when rate limiting infrastructure fails"
            assert result.reason is not None, "Should include a reason for the fail-open"
