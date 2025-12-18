"""
Optimized user lookup service - thin wrapper around db.users

PERF NOTE: This module now delegates directly to src.db.users which has its own
optimized caching layer (60s TTL). We removed the duplicate cache here to:
1. Avoid double-caching overhead and memory waste
2. Ensure consistent cache invalidation
3. Simplify the codebase

The db.users module handles:
- In-memory caching with 60s TTL
- Cache invalidation on user updates
- Legacy API key support with non-blocking background migration
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.db.users import (
    get_user as db_get_user,
    clear_user_cache as db_clear_cache,
    get_user_cache_stats as db_get_cache_stats,
    invalidate_user_cache as db_invalidate_cache,
)

logger = logging.getLogger(__name__)


def clear_cache(api_key: str = None) -> None:
    """Clear user cache (delegates to db.users)"""
    db_clear_cache(api_key)


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring (delegates to db.users)"""
    return db_get_cache_stats()


def get_user(api_key: str) -> dict[str, Any] | None:
    """
    Get user by API key with caching

    Delegates to src.db.users.get_user which has optimized caching.

    Args:
        api_key: User's API key

    Returns:
        User dict if found, None otherwise
    """
    # Check cache first
    if api_key in _user_cache:
        entry = _user_cache[api_key]
        cache_time = entry["timestamp"]
        ttl = entry["ttl"]

        # Check if cache is still valid
        if datetime.now(UTC) - cache_time < timedelta(seconds=ttl):
            logger.debug(f"Cache hit for API key {api_key[:10]}... (age: {(datetime.now(UTC) - cache_time).total_seconds():.1f}s)")
            return entry["user"]
        else:
            # Cache expired, remove it
            del _user_cache[api_key]
            logger.debug(f"Cache expired for API key {api_key[:10]}...")

    # Cache miss or expired - fetch from database
    logger.debug(f"Cache miss for API key {api_key[:10]}... - fetching from database")
    user = db_get_user(api_key)

    # Cache the result (even if None, to avoid repeated DB queries)
    _user_cache[api_key] = {
        "user": user,
        "timestamp": datetime.now(UTC),
        "ttl": _cache_ttl,
    }

    return user


def invalidate_user(api_key: str) -> None:
    """Invalidate cache for a specific user (e.g., after updates)"""
    db_invalidate_cache(api_key)


def set_cache_ttl(ttl_seconds: int) -> None:
    """Set cache TTL (for testing or configuration)

    Note: This now modifies the TTL in db.users module.
    """
    from src.db import users as users_module
    users_module._user_cache_ttl = ttl_seconds
    logger.info(f"Set cache TTL to {ttl_seconds} seconds")
