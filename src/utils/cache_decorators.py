"""
Caching decorators for FastAPI endpoints

Provides decorators for automatic caching of endpoint responses with
configurable TTL and cache invalidation strategies.
"""

import functools
import logging
from typing import Any, Callable, Optional

from src.services.health_cache_service import health_cache_service

logger = logging.getLogger(__name__)


def cached_endpoint(
    cache_key: str,
    ttl: int = 60,
    cache_service: Any = health_cache_service,
    skip_cache: bool = False,
):
    """
    Decorator for caching endpoint responses

    Args:
        cache_key: Redis cache key
        ttl: Time to live in seconds
        cache_service: Cache service instance
        skip_cache: If True, always fetch fresh data
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            # Check if cache should be skipped
            force_refresh = kwargs.get("force_refresh", False)

            if not skip_cache and not force_refresh:
                # Try to get from cache
                cached_data = cache_service.get_cache(cache_key)
                if cached_data is not None:
                    logger.debug(f"Cache hit for {cache_key}")
                    return cached_data

            # Cache miss or forced refresh - call the actual function
            logger.debug(f"Cache miss for {cache_key}, fetching fresh data")
            result = await func(*args, **kwargs)

            # Store in cache
            if result is not None:
                cache_service.set_cache(cache_key, result, ttl)

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            # Check if cache should be skipped
            force_refresh = kwargs.get("force_refresh", False)

            if not skip_cache and not force_refresh:
                # Try to get from cache
                cached_data = cache_service.get_cache(cache_key)
                if cached_data is not None:
                    logger.debug(f"Cache hit for {cache_key}")
                    return cached_data

            # Cache miss or forced refresh - call the actual function
            logger.debug(f"Cache miss for {cache_key}, fetching fresh data")
            result = func(*args, **kwargs)

            # Store in cache
            if result is not None:
                cache_service.set_cache(cache_key, result, ttl)

            return result

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def cache_with_ttl(ttl: int = 60, cache_service: Any = health_cache_service):
    """
    Decorator for caching with automatic key generation

    Args:
        ttl: Time to live in seconds
        cache_service: Cache service instance
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            # Generate cache key from function name and args
            cache_key = f"{func.__module__}:{func.__name__}"

            # Check cache
            cached_data = cache_service.get_cache(cache_key)
            if cached_data is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_data

            # Fetch fresh data
            result = await func(*args, **kwargs)

            # Store in cache
            if result is not None:
                cache_service.set_cache(cache_key, result, ttl)

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            # Generate cache key from function name and args
            cache_key = f"{func.__module__}:{func.__name__}"

            # Check cache
            cached_data = cache_service.get_cache(cache_key)
            if cached_data is not None:
                logger.debug(f"Cache hit for {cache_key}")
                return cached_data

            # Fetch fresh data
            result = func(*args, **kwargs)

            # Store in cache
            if result is not None:
                cache_service.set_cache(cache_key, result, ttl)

            return result

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


import asyncio
