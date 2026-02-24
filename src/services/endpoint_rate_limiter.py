"""
Per-Endpoint Rate Limiter

Lightweight in-memory rate limiter for individual API endpoints.
Uses a fixed-window counter keyed by (api_key, endpoint_name) with
automatic expiry of old buckets.

This module is intentionally simple: no Redis dependency, no database
lookups, no external services.  It is designed to be used as a FastAPI
dependency injected into specific route handlers that need tighter
per-endpoint throttling on top of the existing three-layer rate limiting
architecture.

Usage in a route:
    from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

    balance_rate_limit = create_endpoint_rate_limit(
        endpoint_name="user_balance",
        max_requests=60,
        window_seconds=60,
    )

    @router.get("/user/balance")
    async def get_user_balance(
        api_key: str = Depends(get_api_key),
        _rl: None = Depends(balance_rate_limit),
    ):
        ...
"""

import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal storage
# ---------------------------------------------------------------------------

# _buckets[endpoint_name][(api_key, bucket_id)] = request_count
_buckets: dict[str, dict[tuple[str, int], int]] = defaultdict(dict)

# Track when we last cleaned up each endpoint's stale buckets
_last_cleanup: dict[str, float] = {}

# How often (seconds) to purge expired buckets per endpoint
_CLEANUP_INTERVAL = 120


def _cleanup_stale_buckets(endpoint_name: str, window_seconds: int) -> None:
    """Remove expired bucket entries to prevent unbounded memory growth."""
    now = time.time()
    last = _last_cleanup.get(endpoint_name, 0)
    if now - last < _CLEANUP_INTERVAL:
        return

    _last_cleanup[endpoint_name] = now
    current_bucket = int(now) // window_seconds
    store = _buckets[endpoint_name]

    # Keep only the current bucket (older ones are irrelevant)
    expired_keys = [
        key for key in store if key[1] < current_bucket
    ]
    for key in expired_keys:
        del store[key]

    if expired_keys:
        logger.debug(
            "Cleaned %d expired rate-limit buckets for endpoint '%s'",
            len(expired_keys),
            endpoint_name,
        )


def _check_rate_limit(
    api_key: str,
    endpoint_name: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """Check whether a request is allowed.

    Returns:
        (allowed, remaining, retry_after_seconds)
    """
    now = time.time()
    bucket_id = int(now) // window_seconds
    store = _buckets[endpoint_name]
    key = (api_key, bucket_id)

    current_count = store.get(key, 0)

    if current_count >= max_requests:
        # Calculate seconds until the current window resets
        window_end = (bucket_id + 1) * window_seconds
        retry_after = max(1, int(window_end - now))
        return False, 0, retry_after

    # Increment and allow
    store[key] = current_count + 1
    remaining = max_requests - current_count - 1

    # Periodic cleanup
    _cleanup_stale_buckets(endpoint_name, window_seconds)

    return True, remaining, 0


# ---------------------------------------------------------------------------
# Public API: FastAPI dependency factory
# ---------------------------------------------------------------------------


def create_endpoint_rate_limit(
    endpoint_name: str,
    max_requests: int = 60,
    window_seconds: int = 60,
) -> Callable:
    """Create a FastAPI dependency that enforces per-endpoint rate limiting.

    Args:
        endpoint_name: Unique identifier for the endpoint (used as part of
            the rate-limit key together with the API key).
        max_requests: Maximum number of requests allowed per window.
        window_seconds: Length of the rate-limit window in seconds.

    Returns:
        An async callable suitable for use with ``Depends()``.

    The dependency extracts the API key from the ``Authorization`` header
    (same logic as ``get_api_key``).  If the limit is exceeded it raises
    an ``HTTPException(429)`` with a ``Retry-After`` header and
    informative rate-limit headers.

    Example:
        balance_rl = create_endpoint_rate_limit("user_balance", 60, 60)

        @router.get("/user/balance")
        async def get_balance(
            api_key: str = Depends(get_api_key),
            _rl: None = Depends(balance_rl),
        ):
            ...
    """

    async def _rate_limit_dependency(request: Request) -> None:
        # Extract API key using the same approach as the auth dependency
        # (security/deps.py get_api_key). HTTPBearer parses the
        # "Authorization: Bearer <token>" header and returns .credentials.
        # We replicate the extraction here to avoid calling the full auth
        # dependency (which performs DB lookups and validation).
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            api_key = auth_header[7:].strip()
        else:
            api_key = ""

        # If no API key is present, skip endpoint rate limiting
        # (the auth dependency will reject the request anyway)
        if not api_key:
            return None

        allowed, remaining, retry_after = _check_rate_limit(
            api_key=api_key,
            endpoint_name=endpoint_name,
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

        if not allowed:
            logger.warning(
                "Endpoint rate limit exceeded: endpoint=%s, api_key=%s..., "
                "limit=%d/%ds",
                endpoint_name,
                api_key[:10],
                max_requests,
                window_seconds,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "message": (
                            f"Rate limit exceeded for this endpoint. "
                            f"Maximum {max_requests} requests per "
                            f"{window_seconds} seconds."
                        ),
                        "type": "endpoint_rate_limit",
                        "code": 429,
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "RateLimit-Limit": str(max_requests),
                    "RateLimit-Remaining": "0",
                    "RateLimit-Reset": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reason": f"endpoint_{endpoint_name}_limit",
                },
            )

        return None

    return _rate_limit_dependency
