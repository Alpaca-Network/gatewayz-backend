#!/usr/bin/env python3
"""
Authentication Rate Limiting Module

Implements IP-based rate limiting for authentication endpoints to prevent:
- Brute force attacks on login
- Mass account creation / trial abuse
- Password reset email bombing
- API key creation abuse

Uses sliding window algorithm backed by Redis when available, with an in-memory
fallback for unit tests / Redis-down scenarios.

Previously held per-IP timestamp queues in process memory (O(n) iteration per
check, GC pressure under high IP cardinality). Now delegates to the unified
sliding window in `services.rate_limiting` with a dedicated key prefix.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum

from src.services.rate_limiting import sliding_window_check

logger = logging.getLogger(__name__)


class AuthRateLimitType(Enum):
    """Types of authentication rate limits"""

    LOGIN = "login"
    REGISTER = "register"
    PASSWORD_RESET = "password_reset"
    API_KEY_CREATE = "api_key_create"


@dataclass
class AuthRateLimitConfig:
    """Rate limit configuration for authentication endpoints"""

    # Login: 10 attempts per 15 minutes per IP
    login_attempts_per_window: int = 10
    login_window_seconds: int = 900  # 15 minutes

    # Registration: 3 attempts per hour per IP
    register_attempts_per_window: int = 3
    register_window_seconds: int = 3600  # 1 hour

    # Password reset: 3 attempts per hour per IP
    password_reset_attempts_per_window: int = 3
    password_reset_window_seconds: int = 3600  # 1 hour

    # API key creation: 10 per hour per user
    api_key_create_attempts_per_window: int = 10
    api_key_create_window_seconds: int = 3600  # 1 hour


@dataclass
class AuthRateLimitResult:
    """Result of auth rate limit check"""

    allowed: bool
    remaining: int
    retry_after: int | None = None
    reason: str | None = None
    limit_type: AuthRateLimitType | None = None


# Default configuration
DEFAULT_AUTH_CONFIG = AuthRateLimitConfig()


class AuthRateLimiter:
    """
    IP-based rate limiter for authentication endpoints.

    Uses sliding window algorithm. Distributed (Redis) when available, otherwise
    falls back to in-memory per-process state keyed by `(limit_type, identifier)`.
    The in-memory fallback stores timestamps as plain `list[float]`.
    """

    def __init__(self, config: AuthRateLimitConfig = None):
        self.config = config or DEFAULT_AUTH_CONFIG
        # In-memory fallback: keyed by (limit_type.value, identifier) -> list of timestamps
        self._local: dict[tuple[str, str], list[float]] = {}
        self.lock = asyncio.Lock()

    def _config_for(self, limit_type: AuthRateLimitType) -> tuple[int, int]:
        """Return (limit, window_seconds) for a given action type."""
        if limit_type == AuthRateLimitType.LOGIN:
            return (
                self.config.login_attempts_per_window,
                self.config.login_window_seconds,
            )
        if limit_type == AuthRateLimitType.REGISTER:
            return (
                self.config.register_attempts_per_window,
                self.config.register_window_seconds,
            )
        if limit_type == AuthRateLimitType.PASSWORD_RESET:
            return (
                self.config.password_reset_attempts_per_window,
                self.config.password_reset_window_seconds,
            )
        if limit_type == AuthRateLimitType.API_KEY_CREATE:
            return (
                self.config.api_key_create_attempts_per_window,
                self.config.api_key_create_window_seconds,
            )
        raise ValueError(f"Unknown rate limit type: {limit_type}")

    @staticmethod
    def _redis_key(limit_type: AuthRateLimitType, identifier: str) -> str:
        return f"authrl:{limit_type.value}:{identifier}"

    def _redis_available(self) -> bool:
        try:
            from src.config.redis_config import get_redis_client

            return get_redis_client() is not None
        except Exception:
            return False

    def _local_check(
        self,
        identifier: str,
        limit_type: AuthRateLimitType,
        record: bool,
    ) -> AuthRateLimitResult:
        """In-memory sliding window check. Called under self.lock."""
        limit, window_seconds = self._config_for(limit_type)
        bucket_key = (limit_type.value, identifier)
        now = time.time()
        cutoff = now - window_seconds

        timestamps = self._local.get(bucket_key)
        if timestamps is None:
            timestamps = []
            self._local[bucket_key] = timestamps

        # Drop expired entries (in-place to avoid reallocation churn)
        i = 0
        for ts in timestamps:
            if ts >= cutoff:
                break
            i += 1
        if i:
            del timestamps[:i]

        count = len(timestamps)

        if count >= limit:
            oldest = timestamps[0]
            retry_after = max(1, int(oldest + window_seconds - now) + 1)
            logger.warning(
                "Auth rate limit exceeded: type=%s, identifier=%s, count=%d, limit=%d",
                limit_type.value,
                self._mask_identifier(identifier),
                count,
                limit,
            )
            return AuthRateLimitResult(
                allowed=False,
                remaining=0,
                retry_after=retry_after,
                reason=f"{limit_type.value} rate limit exceeded",
                limit_type=limit_type,
            )

        if record:
            timestamps.append(now)

        return AuthRateLimitResult(
            allowed=True,
            remaining=max(0, limit - count - (1 if record else 0)),
            limit_type=limit_type,
        )

    async def check_rate_limit(
        self,
        identifier: str,
        limit_type: AuthRateLimitType,
    ) -> AuthRateLimitResult:
        """
        Check if request is allowed under rate limit.

        Args:
            identifier: IP address or user ID to rate limit
            limit_type: Type of authentication action being rate limited

        Returns:
            AuthRateLimitResult with allowed status and remaining attempts
        """
        limit, window_seconds = self._config_for(limit_type)

        if self._redis_available():
            key = self._redis_key(limit_type, identifier)
            allowed, remaining, retry_after = await asyncio.to_thread(
                sliding_window_check, key, limit, window_seconds
            )
            if not allowed:
                logger.warning(
                    "Auth rate limit exceeded: type=%s, identifier=%s, limit=%d",
                    limit_type.value,
                    self._mask_identifier(identifier),
                    limit,
                )
                return AuthRateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                    reason=f"{limit_type.value} rate limit exceeded",
                    limit_type=limit_type,
                )
            return AuthRateLimitResult(
                allowed=True,
                remaining=remaining,
                limit_type=limit_type,
            )

        async with self.lock:
            return self._local_check(identifier, limit_type, record=True)

    async def get_remaining(self, identifier: str, limit_type: AuthRateLimitType) -> int:
        """Get remaining attempts for an identifier."""
        limit, _ = self._config_for(limit_type)

        if self._redis_available():
            # Non-recording probe: read current count from Redis without inserting.
            from src.config.redis_config import get_redis_client

            try:
                r = get_redis_client()
                if r is None:
                    raise RuntimeError("redis unavailable")
                key = self._redis_key(limit_type, identifier)
                _, window_seconds = self._config_for(limit_type)
                now_ms = int(time.time() * 1000)
                window_start = now_ms - window_seconds * 1000

                def _probe():
                    pipe = r.pipeline()
                    pipe.zremrangebyscore(key, 0, window_start)
                    pipe.zcard(key)
                    return pipe.execute()

                results = await asyncio.to_thread(_probe)
                count = int(results[1])
                return max(0, limit - count)
            except Exception as e:
                logger.warning("get_remaining: Redis probe failed (%s); using local", e)

        async with self.lock:
            result = self._local_check(identifier, limit_type, record=False)
            return result.remaining if result.allowed else 0

    async def reset(self, identifier: str, limit_type: AuthRateLimitType):
        """Reset rate limit for an identifier (admin function)."""
        if self._redis_available():
            from src.config.redis_config import get_redis_client

            try:
                r = get_redis_client()
                if r is not None:
                    await asyncio.to_thread(r.delete, self._redis_key(limit_type, identifier))
            except Exception as e:
                logger.warning("reset: Redis delete failed (%s)", e)

        async with self.lock:
            bucket_key = (limit_type.value, identifier)
            if bucket_key in self._local:
                self._local[bucket_key].clear()
                logger.info(
                    "Rate limit reset: type=%s, identifier=%s",
                    limit_type.value,
                    self._mask_identifier(identifier),
                )

    @staticmethod
    def _mask_identifier(identifier: str) -> str:
        """Mask IP address or identifier for logging."""
        if not identifier:
            return "unknown"
        # For IP addresses, mask last octet
        parts = identifier.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
        # For other identifiers, show first 8 chars
        if len(identifier) > 8:
            return f"{identifier[:8]}..."
        return identifier


# Global auth rate limiter instance
_auth_rate_limiter: AuthRateLimiter | None = None


def get_auth_rate_limiter() -> AuthRateLimiter:
    """Get the global auth rate limiter instance."""
    global _auth_rate_limiter
    if _auth_rate_limiter is None:
        _auth_rate_limiter = AuthRateLimiter()
    return _auth_rate_limiter


async def check_auth_rate_limit(
    identifier: str,
    limit_type: AuthRateLimitType,
) -> AuthRateLimitResult:
    """
    Convenience function to check auth rate limit.

    Args:
        identifier: IP address or user ID
        limit_type: Type of auth action

    Returns:
        AuthRateLimitResult
    """
    limiter = get_auth_rate_limiter()
    return await limiter.check_rate_limit(identifier, limit_type)


def get_client_ip(request) -> str:
    """
    Get the real client IP address from a FastAPI request.

    Security considerations:
    - X-Forwarded-For can be spoofed by attackers
    - Railway proxy adds the real client IP as the rightmost entry
    - We use a combination approach: prefer X-Real-IP (set by trusted proxy),
      then rightmost X-Forwarded-For entry, then direct connection

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address string
    """
    # First check X-Real-IP (set by trusted proxies like nginx)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Check X-Forwarded-For header
    # Format: "client, proxy1, proxy2" - the rightmost non-private IP is most reliable
    # as proxies append to this header (harder to spoof the rightmost entry)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Split and get all IPs in the chain
        ips = [ip.strip() for ip in forwarded_for.split(",")]

        # For Railway: take the rightmost IP (added by Railway's proxy)
        # This is harder to spoof as the attacker would need to control the proxy
        if len(ips) >= 1:
            # Use rightmost IP - this is what Railway's proxy adds
            return ips[-1]

    # Fall back to direct connection IP
    return request.client.host if request.client else "unknown"
