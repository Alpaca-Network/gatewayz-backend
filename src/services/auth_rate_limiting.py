#!/usr/bin/env python3
"""
Authentication Rate Limiting Module

Implements IP-based rate limiting for authentication endpoints to prevent:
- Brute force attacks on login
- Mass account creation / trial abuse
- Password reset email bombing
- API key creation abuse

Uses sliding window algorithm with in-memory storage (with optional Redis support).
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

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

    Uses sliding window algorithm to track requests per IP address.
    Thread-safe with asyncio lock.
    """

    def __init__(self, config: AuthRateLimitConfig = None):
        self.config = config or DEFAULT_AUTH_CONFIG
        # Separate windows for each rate limit type
        self.login_windows: dict[str, deque] = defaultdict(deque)
        self.register_windows: dict[str, deque] = defaultdict(deque)
        self.password_reset_windows: dict[str, deque] = defaultdict(deque)
        self.api_key_create_windows: dict[str, deque] = defaultdict(deque)
        self.lock = asyncio.Lock()

        # Periodic cleanup tracking
        self._last_cleanup = time.time()
        self._cleanup_interval = 300  # Clean up every 5 minutes

    def _get_window_and_config(
        self, limit_type: AuthRateLimitType
    ) -> tuple[dict[str, deque], int, int]:
        """Get the appropriate window dict, limit, and window size for a rate limit type."""
        if limit_type == AuthRateLimitType.LOGIN:
            return (
                self.login_windows,
                self.config.login_attempts_per_window,
                self.config.login_window_seconds,
            )
        elif limit_type == AuthRateLimitType.REGISTER:
            return (
                self.register_windows,
                self.config.register_attempts_per_window,
                self.config.register_window_seconds,
            )
        elif limit_type == AuthRateLimitType.PASSWORD_RESET:
            return (
                self.password_reset_windows,
                self.config.password_reset_attempts_per_window,
                self.config.password_reset_window_seconds,
            )
        elif limit_type == AuthRateLimitType.API_KEY_CREATE:
            return (
                self.api_key_create_windows,
                self.config.api_key_create_attempts_per_window,
                self.config.api_key_create_window_seconds,
            )
        else:
            raise ValueError(f"Unknown rate limit type: {limit_type}")

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
        async with self.lock:
            current_time = time.time()

            # Periodic cleanup of old entries
            if current_time - self._last_cleanup > self._cleanup_interval:
                await self._cleanup_all_windows(current_time)
                self._last_cleanup = current_time

            window, limit, window_seconds = self._get_window_and_config(limit_type)

            # Clean up old entries for this identifier
            cutoff_time = current_time - window_seconds
            while window[identifier] and window[identifier][0] < cutoff_time:
                window[identifier].popleft()

            current_count = len(window[identifier])

            if current_count >= limit:
                # Calculate retry_after based on oldest entry in the window
                # The oldest entry will expire first, freeing up a slot
                if window[identifier]:
                    oldest = window[identifier][0]
                    retry_after = max(1, int(oldest + window_seconds - current_time) + 1)
                else:
                    # This branch shouldn't happen (count >= limit but empty window)
                    # Use a short retry as a safe fallback
                    retry_after = 60

                logger.warning(
                    "Auth rate limit exceeded: type=%s, identifier=%s, count=%d, limit=%d",
                    limit_type.value,
                    self._mask_identifier(identifier),
                    current_count,
                    limit,
                )

                return AuthRateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=retry_after,
                    reason=f"{limit_type.value} rate limit exceeded",
                    limit_type=limit_type,
                )

            # Record this request
            window[identifier].append(current_time)

            return AuthRateLimitResult(
                allowed=True,
                remaining=limit - current_count - 1,
                limit_type=limit_type,
            )

    async def _cleanup_all_windows(self, current_time: float):
        """Clean up old entries from all windows."""
        windows_config = [
            (self.login_windows, self.config.login_window_seconds),
            (self.register_windows, self.config.register_window_seconds),
            (self.password_reset_windows, self.config.password_reset_window_seconds),
            (self.api_key_create_windows, self.config.api_key_create_window_seconds),
        ]

        for window, window_seconds in windows_config:
            cutoff_time = current_time - window_seconds
            # Remove empty entries
            empty_keys = []
            for key, timestamps in window.items():
                while timestamps and timestamps[0] < cutoff_time:
                    timestamps.popleft()
                if not timestamps:
                    empty_keys.append(key)

            for key in empty_keys:
                del window[key]

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

    async def get_remaining(self, identifier: str, limit_type: AuthRateLimitType) -> int:
        """Get remaining attempts for an identifier."""
        async with self.lock:
            current_time = time.time()
            window, limit, window_seconds = self._get_window_and_config(limit_type)

            # Clean up old entries
            cutoff_time = current_time - window_seconds
            while window[identifier] and window[identifier][0] < cutoff_time:
                window[identifier].popleft()

            return max(0, limit - len(window[identifier]))

    async def reset(self, identifier: str, limit_type: AuthRateLimitType):
        """Reset rate limit for an identifier (admin function)."""
        async with self.lock:
            window, _, _ = self._get_window_and_config(limit_type)
            if identifier in window:
                window[identifier].clear()
                logger.info(
                    "Rate limit reset: type=%s, identifier=%s",
                    limit_type.value,
                    self._mask_identifier(identifier),
                )


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
