"""Authentication cache utilities for reducing database load.

This module provides Redis-backed caching for frequently accessed user lookups
during the authentication process, specifically for Privy ID to user ID mappings.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Cache TTL in seconds (5 minutes for auth data)
AUTH_CACHE_TTL = 300

# Cache key prefixes
PRIVY_ID_CACHE_PREFIX = "auth:privy_id:"
USERNAME_CACHE_PREFIX = "auth:username:"


def get_redis_client():
    """Get Redis client instance with error handling."""
    try:
        from src.config.redis_config import get_redis_client as get_client
        return get_client()
    except Exception as e:
        logger.warning(f"Failed to get Redis client: {e}")
        return None


def cache_user_by_privy_id(privy_id: str, user_data: dict[str, Any]) -> bool:
    """Cache user data by Privy ID.

    Args:
        privy_id: Privy user ID
        user_data: User data to cache

    Returns:
        True if cached successfully, False otherwise
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False

        cache_key = f"{PRIVY_ID_CACHE_PREFIX}{privy_id}"
        redis_client.setex(
            cache_key, AUTH_CACHE_TTL, json.dumps(user_data)
        )
        logger.debug(f"Cached user data for Privy ID: {privy_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to cache user by Privy ID: {e}")
        return False


def get_cached_user_by_privy_id(privy_id: str) -> Optional[dict[str, Any]]:
    """Get cached user data by Privy ID.

    Args:
        privy_id: Privy user ID

    Returns:
        Cached user data if found, None otherwise
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None

        cache_key = f"{PRIVY_ID_CACHE_PREFIX}{privy_id}"
        cached_data = redis_client.get(cache_key)

        if cached_data:
            user_data = json.loads(cached_data)
            logger.debug(f"Cache hit for Privy ID: {privy_id}")
            return user_data

        return None
    except Exception as e:
        logger.warning(f"Failed to retrieve cached user by Privy ID: {e}")
        return None


def cache_user_by_username(username: str, user_data: dict[str, Any]) -> bool:
    """Cache user data by username.

    Args:
        username: User's username
        user_data: User data to cache

    Returns:
        True if cached successfully, False otherwise
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False

        cache_key = f"{USERNAME_CACHE_PREFIX}{username}"
        redis_client.setex(
            cache_key, AUTH_CACHE_TTL, json.dumps(user_data)
        )
        logger.debug(f"Cached user data for username: {username}")
        return True
    except Exception as e:
        logger.warning(f"Failed to cache user by username: {e}")
        return False


def get_cached_user_by_username(username: str) -> Optional[dict[str, Any]]:
    """Get cached user data by username.

    Args:
        username: User's username

    Returns:
        Cached user data if found, None otherwise
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None

        cache_key = f"{USERNAME_CACHE_PREFIX}{username}"
        cached_data = redis_client.get(cache_key)

        if cached_data:
            user_data = json.loads(cached_data)
            logger.debug(f"Cache hit for username: {username}")
            return user_data

        return None
    except Exception as e:
        logger.warning(f"Failed to retrieve cached user by username: {e}")
        return None


def invalidate_user_cache(privy_id: Optional[str] = None, username: Optional[str] = None) -> bool:
    """Invalidate cached user data.

    Args:
        privy_id: Privy user ID to invalidate (optional)
        username: Username to invalidate (optional)

    Returns:
        True if invalidated successfully, False otherwise
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False

        keys_to_delete = []
        if privy_id:
            keys_to_delete.append(f"{PRIVY_ID_CACHE_PREFIX}{privy_id}")
        if username:
            keys_to_delete.append(f"{USERNAME_CACHE_PREFIX}{username}")

        if keys_to_delete:
            redis_client.delete(*keys_to_delete)
            logger.debug(f"Invalidated cache for: {keys_to_delete}")
            return True

        return False
    except Exception as e:
        logger.warning(f"Failed to invalidate user cache: {e}")
        return False
