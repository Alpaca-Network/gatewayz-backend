#!/usr/bin/env python3
"""
Advanced Rate Limiting Module
Implements sliding-window rate limiting, burst controls, and configurable limits per key.
Updated: 2025-10-12 - Force restart to clear LRU cache
"""

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import redis

from src.services.pyroscope_config import tag_wrapper

from src.db.rate_limits import get_rate_limit_config, update_rate_limit_config
from src.services.rate_limiting_fallback import get_fallback_rate_limit_manager

logger = logging.getLogger(__name__)


def _calculate_burst_window_description(config: "RateLimitConfig") -> str:
    """Generate a human-readable burst window description.

    Example: "100 per 60 seconds"
    """
    return f"{config.burst_limit} per {config.window_size_seconds} seconds"


def _populate_rate_limit_headers(
    result: "RateLimitResult", config: "RateLimitConfig", request_limit: int, token_limit: int
) -> None:
    """Populate rate limit header fields in the result object.

    Sets:
    - ratelimit_limit_requests: Total request limit
    - ratelimit_limit_tokens: Total token limit
    - ratelimit_reset_requests: Unix timestamp when request limit resets
    - ratelimit_reset_tokens: Unix timestamp when token limit resets
    - burst_window_description: Human-readable burst window (e.g., "100 per 60 seconds")
    """
    result.ratelimit_limit_requests = request_limit
    result.ratelimit_limit_tokens = token_limit
    # Safely convert reset_time to Unix timestamp
    if result.reset_time:
        reset_timestamp = (
            int(result.reset_time.timestamp())
            if hasattr(result.reset_time, "timestamp")
            else int(result.reset_time)
        )
    else:
        reset_timestamp = int(time.time()) + 60
    result.ratelimit_reset_requests = reset_timestamp
    result.ratelimit_reset_tokens = reset_timestamp
    result.burst_window_description = _calculate_burst_window_description(config)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a specific key"""

    requests_per_minute: int = 250
    requests_per_hour: int = 1000
    requests_per_day: int = 10000
    tokens_per_minute: int = 10000
    tokens_per_hour: int = 100000
    tokens_per_day: int = 1000000
    burst_limit: int = 100  # Maximum burst requests
    concurrency_limit: int = 50  # Maximum concurrent requests
    window_size_seconds: int = 60  # Sliding window size


@dataclass
class RateLimitResult:
    """Result of rate limit check"""

    allowed: bool
    remaining_requests: int
    remaining_tokens: int
    reset_time: datetime
    retry_after: int | None = None
    reason: str | None = None
    burst_remaining: int = 0
    concurrency_remaining: int = 0
    # Rate limit headers for HTTP responses
    ratelimit_limit_requests: int = 0  # X-RateLimit-Limit-Requests
    ratelimit_limit_tokens: int = 0  # X-RateLimit-Limit-Tokens
    ratelimit_reset_requests: int = 0  # X-RateLimit-Reset-Requests (Unix timestamp)
    ratelimit_reset_tokens: int = 0  # X-RateLimit-Reset-Tokens (Unix timestamp)
    burst_window_description: str = ""  # Human-readable burst window (e.g., "100 per 60 seconds")


# Default configurations
DEFAULT_CONFIG = RateLimitConfig(
    requests_per_minute=250,
    requests_per_hour=1000,
    requests_per_day=10000,
    tokens_per_minute=10000,
    tokens_per_hour=100000,
    tokens_per_day=1000000,
    burst_limit=100,
    concurrency_limit=50,
)


class SlidingWindowRateLimiter:
    """Simplified rate limiter using fallback system"""

    def __init__(self, redis_client: redis.Redis | None = None):
        # Use fallback rate limiting system (no Redis)
        self.fallback_manager = get_fallback_rate_limit_manager()
        self.concurrent_requests = defaultdict(int)
        self.burst_tokens = {}
        self.local_cache = {}
        self.redis_client = redis_client

    async def check_rate_limit(
        self,
        api_key: str,
        config: RateLimitConfig,
        tokens_used: int = 0,
    ) -> RateLimitResult:
        """Check rate limit using fallback system"""
        try:
            # Check concurrency limit first
            concurrency_check = await self._check_concurrency_limit(api_key, config)
            if not concurrency_check["allowed"]:
                result = RateLimitResult(
                    allowed=False,
                    remaining_requests=0,
                    remaining_tokens=0,
                    reset_time=datetime.now(UTC) + timedelta(seconds=60),
                    retry_after=60,
                    reason="Concurrency limit exceeded",
                    concurrency_remaining=0,
                )
                _populate_rate_limit_headers(
                    result, config, config.requests_per_minute, config.tokens_per_minute
                )
                return result

            # Check burst limit
            burst_check = await self._check_burst_limit(api_key, config)
            if not burst_check["allowed"]:
                result = RateLimitResult(
                    allowed=False,
                    remaining_requests=0,
                    remaining_tokens=0,
                    reset_time=datetime.now(UTC) + timedelta(seconds=burst_check["retry_after"]),
                    retry_after=burst_check["retry_after"],
                    reason="Burst limit exceeded",
                    burst_remaining=burst_check["remaining"],
                )
                _populate_rate_limit_headers(
                    result, config, config.requests_per_minute, config.tokens_per_minute
                )
                return result

            # Check sliding window limits
            window_check = await self._check_sliding_window(api_key, config, tokens_used)
            if not window_check["allowed"]:
                result = RateLimitResult(
                    allowed=False,
                    remaining_requests=window_check["remaining_requests"],
                    remaining_tokens=window_check["remaining_tokens"],
                    reset_time=window_check["reset_time"],
                    retry_after=window_check["retry_after"],
                    reason=window_check["reason"],
                    burst_remaining=burst_check["remaining"],
                    concurrency_remaining=concurrency_check["remaining"],
                )
                _populate_rate_limit_headers(
                    result, config, config.requests_per_minute, config.tokens_per_minute
                )
                return result

            # All checks passed - request is allowed
            # Increment concurrency counter BEFORE returning
            await self.increment_concurrent_requests(api_key)

            # Build result from our own sliding window check data
            limit_result = RateLimitResult(
                allowed=True,
                remaining_requests=window_check.get(
                    "remaining_requests", config.requests_per_minute
                ),
                remaining_tokens=window_check.get("remaining_tokens", config.tokens_per_minute),
                reset_time=(
                    window_check.get("reset_time")
                    if isinstance(window_check.get("reset_time"), datetime)
                    else datetime.now(UTC) + timedelta(minutes=1)
                ),
                retry_after=None,
                reason=None,
                burst_remaining=burst_check["remaining"],
                concurrency_remaining=concurrency_check["remaining"],
            )
            _populate_rate_limit_headers(
                limit_result, config, config.requests_per_minute, config.tokens_per_minute
            )
            return limit_result

        except Exception as e:
            logger.error(f"Rate limit check failed for key {api_key[:10]}...: {e}")
            # Fail open - allow request if rate limiting fails
            result = RateLimitResult(
                allowed=True,
                remaining_requests=config.requests_per_minute,
                remaining_tokens=config.tokens_per_minute,
                reset_time=datetime.now(UTC) + timedelta(minutes=1),
                reason="Rate limit check failed, allowing request",
            )
            _populate_rate_limit_headers(
                result, config, config.requests_per_minute, config.tokens_per_minute
            )
            return result

    async def _check_concurrency_limit(
        self, api_key: str, config: RateLimitConfig
    ) -> dict[str, Any]:
        """Check concurrent request limit"""
        current_concurrent = self.concurrent_requests.get(api_key, 0)

        logger.debug(
            f"Concurrency check: {current_concurrent}/{config.concurrency_limit} for {api_key[:10]}"
        )

        if current_concurrent >= config.concurrency_limit:
            return {
                "allowed": False,
                "remaining": 0,
                "current": current_concurrent,
                "limit": config.concurrency_limit,
            }

        return {
            "allowed": True,
            "remaining": config.concurrency_limit - current_concurrent,
            "current": current_concurrent,
            "limit": config.concurrency_limit,
        }

    async def _check_burst_limit(self, api_key: str, config: RateLimitConfig) -> dict[str, Any]:
        """Check burst limit using token bucket algorithm

        FIX (2026-02-06): Removed invalid `await` on sync Redis pipeline operations.
        Pipeline commands queue up and execute together - they don't return awaitables.
        Used asyncio.to_thread() to wrap the blocking execute() call.
        """
        import asyncio

        now = time.time()
        key = f"burst:{api_key}"

        if self.redis_client:
            # Use Redis for distributed burst limiting
            # Step 1: Get current state
            def _get_burst_state():
                with tag_wrapper({"cache_layer": "rate_limit", "cache_op": "read"}):
                    pipe = self.redis_client.pipeline()
                    pipe.hget(key, "tokens")
                    pipe.hget(key, "last_refill")
                    return pipe.execute()

            results = await asyncio.to_thread(_get_burst_state)
            current_tokens = float(results[0] or 0)
            last_refill = float(results[1] or now)

            # Refill tokens based on time passed
            time_passed = now - last_refill
            tokens_to_add = time_passed * (config.burst_limit / 60)  # Refill rate per second
            current_tokens = min(config.burst_limit, current_tokens + tokens_to_add)

            if current_tokens >= 1:
                # Consume one token
                def _consume_token():
                    with tag_wrapper({"cache_layer": "rate_limit", "cache_op": "write"}):
                        pipe = self.redis_client.pipeline()
                        pipe.hset(key, "tokens", current_tokens - 1)
                        pipe.hset(key, "last_refill", now)
                        pipe.expire(key, 300)  # Expire after 5 minutes
                        return pipe.execute()

                await asyncio.to_thread(_consume_token)

                return {
                    "allowed": True,
                    "remaining": int(current_tokens - 1),
                    "current": int(current_tokens - 1),
                    "limit": config.burst_limit,
                }
            else:
                return {
                    "allowed": False,
                    "remaining": 0,
                    "current": int(current_tokens),
                    "limit": config.burst_limit,
                    "retry_after": int((1 - current_tokens) * 60 / config.burst_limit),
                }
        else:
            # Fallback to local cache
            if api_key not in self.burst_tokens:
                self.burst_tokens[api_key] = config.burst_limit

            if self.burst_tokens[api_key] >= 1:
                self.burst_tokens[api_key] -= 1
                return {
                    "allowed": True,
                    "remaining": int(self.burst_tokens[api_key]),
                    "current": int(self.burst_tokens[api_key]),
                    "limit": config.burst_limit,
                }
            else:
                return {
                    "allowed": False,
                    "remaining": 0,
                    "current": int(self.burst_tokens[api_key]),
                    "limit": config.burst_limit,
                    "retry_after": 60,
                }

    async def _check_sliding_window(
        self, api_key: str, config: RateLimitConfig, tokens_used: int
    ) -> dict[str, Any]:
        """Check sliding window rate limits"""
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=config.window_size_seconds)

        if self.redis_client:
            # Use Redis for distributed rate limiting
            return await self._check_redis_sliding_window(
                api_key, config, tokens_used, now, window_start
            )
        else:
            # Fallback to local cache
            return await self._check_local_sliding_window(
                api_key, config, tokens_used, now, window_start
            )

    async def _check_redis_sliding_window(
        self,
        api_key: str,
        config: RateLimitConfig,
        tokens_used: int,
        now: datetime,
        window_start: datetime,
    ) -> dict[str, Any]:
        """Check sliding window using Redis

        FIX (2026-02-06): Removed invalid `await` on sync Redis pipeline operations.
        Pipeline commands queue up and execute together - they don't return awaitables.
        Used asyncio.to_thread() to wrap the blocking execute() calls.
        """
        import asyncio

        # Get current usage for different time windows
        minute_key = f"rate_limit:{api_key}:minute:{now.strftime('%Y%m%d%H%M')}"
        hour_key = f"rate_limit:{api_key}:hour:{now.strftime('%Y%m%d%H')}"
        day_key = f"rate_limit:{api_key}:day:{now.strftime('%Y%m%d')}"

        # Get current counts - wrap sync Redis calls in thread
        def _get_current_counts():
            with tag_wrapper({"cache_layer": "rate_limit", "cache_op": "read"}):
                pipe = self.redis_client.pipeline()
                pipe.get(f"{minute_key}:requests")
                pipe.get(f"{minute_key}:tokens")
                pipe.get(f"{hour_key}:requests")
                pipe.get(f"{hour_key}:tokens")
                pipe.get(f"{day_key}:requests")
                pipe.get(f"{day_key}:tokens")
                return pipe.execute()

        results = await asyncio.to_thread(_get_current_counts)

        minute_requests = int(results[0] or 0)
        minute_tokens = int(results[1] or 0)
        hour_requests = int(results[2] or 0)
        hour_tokens = int(results[3] or 0)
        day_requests = int(results[4] or 0)
        day_tokens = int(results[5] or 0)

        # Check limits
        if minute_requests >= config.requests_per_minute:
            return {
                "allowed": False,
                "remaining_requests": 0,
                "remaining_tokens": 0,
                "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "retry_after": 60,
                "reason": "Minute request limit exceeded",
            }

        if minute_tokens + tokens_used > config.tokens_per_minute:
            return {
                "allowed": False,
                "remaining_requests": config.requests_per_minute - minute_requests,
                "remaining_tokens": 0,
                "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "retry_after": 60,
                "reason": "Minute token limit exceeded",
            }

        if hour_requests >= config.requests_per_hour:
            return {
                "allowed": False,
                "remaining_requests": 0,
                "remaining_tokens": 0,
                "reset_time": now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                "retry_after": 3600,
                "reason": "Hour request limit exceeded",
            }

        if hour_tokens + tokens_used > config.tokens_per_hour:
            return {
                "allowed": False,
                "remaining_requests": config.requests_per_hour - hour_requests,
                "remaining_tokens": 0,
                "reset_time": now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                "retry_after": 3600,
                "reason": "Hour token limit exceeded",
            }

        if day_requests >= config.requests_per_day:
            return {
                "allowed": False,
                "remaining_requests": 0,
                "remaining_tokens": 0,
                "reset_time": now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1),
                "retry_after": 86400,
                "reason": "Day request limit exceeded",
            }

        if day_tokens + tokens_used > config.tokens_per_day:
            return {
                "allowed": False,
                "remaining_requests": config.requests_per_day - day_requests,
                "remaining_tokens": 0,
                "reset_time": now.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1),
                "retry_after": 86400,
                "reason": "Day token limit exceeded",
            }

        # All checks passed, update counters - wrap sync Redis calls in thread
        def _update_counters():
            with tag_wrapper({"cache_layer": "rate_limit", "cache_op": "write"}):
                pipe = self.redis_client.pipeline()
                pipe.incr(f"{minute_key}:requests")
                pipe.incrby(f"{minute_key}:tokens", tokens_used)
                pipe.incr(f"{hour_key}:requests")
                pipe.incrby(f"{hour_key}:tokens", tokens_used)
                pipe.incr(f"{day_key}:requests")
                pipe.incrby(f"{day_key}:tokens", tokens_used)

                # Set expiration times
                pipe.expire(f"{minute_key}:requests", 120)  # 2 minutes
                pipe.expire(f"{minute_key}:tokens", 120)
                pipe.expire(f"{hour_key}:requests", 7200)  # 2 hours
                pipe.expire(f"{hour_key}:tokens", 7200)
                pipe.expire(f"{day_key}:requests", 172800)  # 2 days
                pipe.expire(f"{day_key}:tokens", 172800)
                return pipe.execute()

        await asyncio.to_thread(_update_counters)

        return {
            "allowed": True,
            "remaining_requests": config.requests_per_minute - minute_requests - 1,
            "remaining_tokens": config.tokens_per_minute - minute_tokens - tokens_used,
            "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
        }

    async def _check_local_sliding_window(
        self,
        api_key: str,
        config: RateLimitConfig,
        tokens_used: int,
        now: datetime,
        window_start: datetime,
    ) -> dict[str, Any]:
        """Check sliding window using local cache (fallback)"""
        if api_key not in self.local_cache:
            # tokens: deque of (timestamp, amount)
            self.local_cache[api_key] = {"requests": deque(), "tokens": deque()}

        cache = self.local_cache[api_key]

        # Clean old entries
        while cache["requests"] and cache["requests"][0] < window_start:
            cache["requests"].popleft()
        while cache["tokens"] and cache["tokens"][0][0] < window_start:
            cache["tokens"].popleft()

        # Current usage in window
        current_requests = len(cache["requests"])
        current_tokens = sum(amount for ts, amount in cache["tokens"])

        # Limits
        if current_requests >= config.requests_per_minute:
            return {
                "allowed": False,
                "remaining_requests": 0,
                "remaining_tokens": 0,
                "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "retry_after": 60,
                "reason": "Minute request limit exceeded",
            }

        if current_tokens + tokens_used > config.tokens_per_minute:
            return {
                "allowed": False,
                "remaining_requests": config.requests_per_minute - current_requests,
                "remaining_tokens": 0,
                "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
                "retry_after": 60,
                "reason": "Minute token limit exceeded",
            }

        # Record this request
        cache["requests"].append(now)
        cache["tokens"].append((now, tokens_used))

        return {
            "allowed": True,
            "remaining_requests": config.requests_per_minute - current_requests - 1,
            "remaining_tokens": config.tokens_per_minute - current_tokens - tokens_used,
            "reset_time": now.replace(second=0, microsecond=0) + timedelta(minutes=1),
        }

    async def increment_concurrent_requests(self, api_key: str):
        """Increment concurrent request counter"""
        self.concurrent_requests[api_key] += 1

    async def decrement_concurrent_requests(self, api_key: str):
        """Decrement concurrent request counter"""
        if api_key in self.concurrent_requests:
            self.concurrent_requests[api_key] = max(0, self.concurrent_requests[api_key] - 1)

    async def release_concurrent_request(self, api_key: str):
        """Release concurrency slot for a key"""
        await self.decrement_concurrent_requests(api_key)
        try:
            await self.fallback_manager.release_concurrent_request(api_key)
        except Exception as exc:
            logger.debug("Fallback concurrency release failed for %s: %s", api_key[:10], exc)


class RateLimitManager:
    """Manager for rate limiting with per-key configuration (OPTIMIZED: with caching)"""

    def __init__(self, redis_client: redis.Redis | None = None):
        self.rate_limiter = SlidingWindowRateLimiter(redis_client)
        self.key_configs = {}  # Cache for per-key configurations
        self.default_config = RateLimitConfig()
        self.fallback_manager = get_fallback_rate_limit_manager()
        # OPTIMIZATION: Short-lived cache for rate limit results (15-30ms faster per cached request)
        self._result_cache: dict[str, tuple[RateLimitResult, float]] = {}
        self._cache_ttl = (
            15.0  # Cache results for 15 seconds (increased from 5s for better performance)
        )

    async def get_key_config(self, api_key: str) -> RateLimitConfig:
        """Get rate limit configuration for a specific API key"""
        if api_key in self.key_configs:
            return self.key_configs[api_key]

        # Load from database
        config = await self._load_key_config_from_db(api_key)
        self.key_configs[api_key] = config
        return config

    async def _load_key_config_from_db(self, api_key: str) -> RateLimitConfig:
        """Load rate limit configuration from database.

        FIX (2026-02-05): Wrapped in asyncio.to_thread() to prevent blocking
        the event loop. This was a major cause of 499/500 errors.
        """
        import asyncio

        try:
            config_data = await asyncio.to_thread(get_rate_limit_config, api_key)
            if config_data:
                return RateLimitConfig(
                    requests_per_minute=config_data.get("requests_per_minute", 250),
                    requests_per_hour=config_data.get("requests_per_hour", 1000),
                    requests_per_day=config_data.get("requests_per_day", 10000),
                    tokens_per_minute=config_data.get("tokens_per_minute", 10000),
                    tokens_per_hour=config_data.get("tokens_per_hour", 100000),
                    tokens_per_day=config_data.get("tokens_per_day", 1000000),
                    burst_limit=config_data.get("burst_limit", 500),
                    concurrency_limit=config_data.get("concurrency_limit", 50),
                    window_size_seconds=config_data.get("window_size_seconds", 60),
                )
        except Exception as e:
            logger.error(f"Failed to load rate limit config from DB: {e}")

        # Return default config if not found or error
        return DEFAULT_CONFIG

    async def increment_request(self, api_key: str, config: RateLimitConfig, tokens_used: int = 0):
        """Increment request count (handled by fallback system)"""
        # Note: Fallback manager doesn't have increment_request, it's handled in check_rate_limit
        pass

    async def check_rate_limit(
        self, api_key: str, tokens_used: int = 0, request_type: str = "api"
    ) -> RateLimitResult:
        """Check rate limit for a specific API key (OPTIMIZED: with short-lived caching)

        FIX (2026-02-05): All synchronous DB calls are now wrapped in
        asyncio.to_thread() to prevent blocking the event loop. Previously,
        synchronous Supabase calls here would block the event loop, causing
        499/500 errors for concurrent requests (including chat completions)
        when admin dashboard pages or rate limiter pages were loaded.
        """
        import asyncio

        # OPTIMIZATION: Check result cache FIRST before any DB calls
        # This avoids expensive user lookups on cache hits
        now = time.time()
        cache_key = f"{api_key}:{tokens_used}"

        if cache_key in self._result_cache:
            cached_result, cached_time = self._result_cache[cache_key]
            if now - cached_time < self._cache_ttl:
                logger.debug(
                    f"Rate limit cache HIT for {api_key[:10]}... (age: {now - cached_time:.2f}s)"
                )
                return cached_result
            else:
                # Expired, remove from cache
                del self._result_cache[cache_key]

        # FIX: Fetch user ONCE via asyncio.to_thread() to avoid blocking the
        # event loop and to eliminate the duplicate get_user() call that was
        # previously happening (once for admin check, once for severe check).
        user = None
        try:
            from src.services.user_lookup_cache import get_user

            user = await asyncio.to_thread(get_user, api_key)
        except Exception as e:
            logger.debug(f"Error fetching user for rate limiting: {e}")

        # ADMIN BYPASS: Check if this is an admin tier user (skip rate limits)
        if user:
            try:
                from src.db.plans import is_admin_tier_user

                is_admin = await asyncio.to_thread(is_admin_tier_user, user.get("id"))
                if is_admin:
                    logger.info("Admin tier user - bypassing rate limit checks")
                    # Return unlimited rate limit result for admin users
                    return RateLimitResult(
                        allowed=True,
                        remaining_requests=2147483647,  # Max int
                        remaining_tokens=2147483647,
                        reset_time=datetime.now(UTC) + timedelta(days=365),
                        retry_after=None,
                        reason="Admin tier - unlimited access",
                        burst_remaining=2147483647,
                        concurrency_remaining=1000,
                        ratelimit_limit_requests=2147483647,
                        ratelimit_limit_tokens=2147483647,
                        ratelimit_reset_requests=int(
                            (datetime.now(UTC) + timedelta(days=365)).timestamp()
                        ),
                        ratelimit_reset_tokens=int(
                            (datetime.now(UTC) + timedelta(days=365)).timestamp()
                        ),
                        burst_window_description="unlimited",
                    )
            except Exception as e:
                logger.debug(f"Error checking admin tier for rate limiting: {e}")

        # SEVERE RATE LIMITING: Check for temporary/blocked email domains
        # FIX: Reuse the already-fetched user instead of calling get_user() again
        severe_config = await self._get_severe_rate_limit_config_with_user(user)
        if severe_config is not None:
            # Apply severe rate limiting for suspicious accounts
            result = await self.rate_limiter.check_rate_limit(api_key, severe_config, tokens_used)
            if not result.allowed:
                logger.warning(
                    f"Severe rate limit exceeded for suspicious account {api_key[:10]}...: {result.reason}"
                )
            return result

        # Cache miss - do actual check
        config = await self.get_key_config(api_key)
        result = await self.rate_limiter.check_rate_limit(api_key, config, tokens_used)

        # Cache the result if allowed (only cache successful checks)
        if result.allowed:
            self._result_cache[cache_key] = (result, now)
            # Clean up old cache entries (keep cache size bounded)
            if len(self._result_cache) > 1000:
                # Remove oldest 200 entries
                sorted_keys = sorted(
                    self._result_cache.keys(), key=lambda k: self._result_cache[k][1]
                )
                for old_key in sorted_keys[:200]:
                    del self._result_cache[old_key]

        return result

    async def update_key_config(self, api_key: str, config: RateLimitConfig):
        """Update rate limit configuration for a specific key"""
        self.key_configs[api_key] = config
        # Also update in database
        await self._save_key_config_to_db(api_key, config)

    async def _get_severe_rate_limit_config_with_user(
        self, user: dict | None
    ) -> RateLimitConfig | None:
        """Check if user should have severe rate limiting applied.

        This version accepts an already-fetched user dict to avoid duplicate
        get_user() calls in the hot path.

        Returns:
            BLOCKED_ACCOUNT_CONFIG for blocked email domains
            SEVERE_RATE_LIMIT_CONFIG for temporary email domains
            None for normal accounts (no severe limiting)
        """
        try:
            if not user:
                return None

            from src.utils.security_validators import (
                is_blocked_email_domain,
                is_temporary_email_domain,
            )

            email = user.get("email", "")
            if not email:
                return None

            # Check for blocked email domains (most restrictive)
            if is_blocked_email_domain(email):
                logger.warning(
                    f"Applying BLOCKED rate limits for user with blocked domain: "
                    f"{email[:3]}***@{email.split('@')[-1] if '@' in email else 'unknown'}"
                )
                return BLOCKED_ACCOUNT_CONFIG

            # Check for temporary/disposable email domains (severe limiting)
            if is_temporary_email_domain(email):
                logger.info(
                    f"Applying SEVERE rate limits for user with temporary email: "
                    f"{email[:3]}***@{email.split('@')[-1] if '@' in email else 'unknown'}"
                )
                return SEVERE_RATE_LIMIT_CONFIG

            return None

        except Exception as e:
            logger.debug(f"Error checking for severe rate limiting: {e}")
            return None

    async def _get_severe_rate_limit_config(self, api_key: str) -> RateLimitConfig | None:
        """Check if user should have severe rate limiting applied.

        Legacy wrapper that fetches user first. Prefer
        _get_severe_rate_limit_config_with_user() when user is already available.
        """
        import asyncio

        try:
            from src.services.user_lookup_cache import get_user

            user = await asyncio.to_thread(get_user, api_key)
            return await self._get_severe_rate_limit_config_with_user(user)
        except Exception as e:
            logger.debug(f"Error checking for severe rate limiting: {e}")
            return None

    async def release_concurrency(self, api_key: str):
        """Release concurrency slot for a key"""
        await self.rate_limiter.release_concurrent_request(api_key)
        try:
            await self.fallback_manager.release_concurrent_request(api_key)
        except Exception as exc:
            logger.debug("Fallback concurrency release failed for %s: %s", api_key[:10], exc)

    async def _save_key_config_to_db(self, api_key: str, config: RateLimitConfig):
        """Save rate limit configuration to database"""
        try:
            config_dict = {
                "requests_per_minute": config.requests_per_minute,
                "requests_per_hour": config.requests_per_hour,
                "requests_per_day": config.requests_per_day,
                "tokens_per_minute": config.tokens_per_minute,
                "tokens_per_hour": config.tokens_per_hour,
                "tokens_per_day": config.tokens_per_day,
                "burst_limit": config.burst_limit,
                "concurrency_limit": config.concurrency_limit,
                "window_size_seconds": config.window_size_seconds,
            }

            update_rate_limit_config(api_key, config_dict)
        except Exception as e:
            logger.error(f"Failed to save rate limit config to DB: {e}")

    async def get_rate_limit_status(self, api_key: str, config: RateLimitConfig) -> dict[str, Any]:
        """Get current rate limit status"""
        # Fallback manager doesn't have get_rate_limit_status, return default
        return {
            "requests_remaining": config.requests_per_minute,
            "tokens_remaining": config.tokens_per_minute,
            "reset_time": int((datetime.now(UTC) + timedelta(minutes=1)).timestamp()),
        }


# Global rate limiter instance
_rate_limiter = None


@lru_cache(maxsize=1)
def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Get global rate limiter instance"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = SlidingWindowRateLimiter()
    return _rate_limiter


# Convenience functions
async def check_rate_limit(
    api_key: str, config: RateLimitConfig, tokens_used: int = 0
) -> RateLimitResult:
    """Check rate limit for API key"""
    limiter = get_rate_limiter()
    return await limiter.check_rate_limit(api_key, config, tokens_used)


async def increment_request(api_key: str, config: RateLimitConfig, tokens_used: int = 0):
    """Increment request count for API key"""
    limiter = get_rate_limiter()
    await limiter.increment_request(api_key, config, tokens_used)


async def get_rate_limit_status(api_key: str, config: RateLimitConfig) -> dict[str, Any]:
    """Get rate limit status for API key"""
    limiter = get_rate_limiter()
    return await limiter.get_rate_limit_status(api_key, config)


PREMIUM_CONFIG = RateLimitConfig(
    requests_per_minute=300,
    requests_per_hour=5000,
    requests_per_day=50000,
    tokens_per_minute=50000,
    tokens_per_hour=500000,
    tokens_per_day=5000000,
    burst_limit=50,
    concurrency_limit=20,
)

ENTERPRISE_CONFIG = RateLimitConfig(
    requests_per_minute=1000,
    requests_per_hour=20000,
    requests_per_day=200000,
    tokens_per_minute=200000,
    tokens_per_hour=2000000,
    tokens_per_day=20000000,
    burst_limit=100,
    concurrency_limit=50,
)

# Severe rate limiting for suspicious/abusive accounts
# Applied to: temporary email domains, blocked email domains, flagged accounts
SEVERE_RATE_LIMIT_CONFIG = RateLimitConfig(
    requests_per_minute=5,  # 5 requests per minute (vs 250 default)
    requests_per_hour=20,  # 20 requests per hour (vs 1000 default)
    requests_per_day=50,  # 50 requests per day (vs 10000 default)
    tokens_per_minute=500,  # 500 tokens per minute (vs 10000 default)
    tokens_per_hour=2000,  # 2000 tokens per hour (vs 100000 default)
    tokens_per_day=5000,  # 5000 tokens per day (vs 1000000 default)
    burst_limit=3,  # 3 burst requests (vs 100 default)
    concurrency_limit=1,  # 1 concurrent request (vs 50 default)
    window_size_seconds=60,
)

# Blocked accounts - even more restrictive (essentially read-only/denied)
BLOCKED_ACCOUNT_CONFIG = RateLimitConfig(
    requests_per_minute=1,  # 1 request per minute - essentially blocked
    requests_per_hour=5,  # 5 requests per hour
    requests_per_day=10,  # 10 requests per day
    tokens_per_minute=100,  # 100 tokens per minute
    tokens_per_hour=500,  # 500 tokens per hour
    tokens_per_day=1000,  # 1000 tokens per day
    burst_limit=1,  # 1 burst request
    concurrency_limit=1,  # 1 concurrent request
    window_size_seconds=60,
)

# Global rate limit manager instance
_rate_limit_manager = None


@lru_cache(maxsize=1)
def get_rate_limit_manager() -> RateLimitManager:
    """Get global rate limit manager instance"""
    global _rate_limit_manager
    if _rate_limit_manager is None:
        _rate_limit_manager = RateLimitManager()
    return _rate_limit_manager
