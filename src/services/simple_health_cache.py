"""
Simple Health Cache Service

Provides basic Redis caching for health monitoring endpoints.
Compression can be added later.
"""

import json
import logging
from typing import Any

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

# Cache key prefixes
CACHE_PREFIX_SYSTEM = "health:system"
CACHE_PREFIX_PROVIDERS = "health:providers"
CACHE_PREFIX_MODELS = "health:models"
CACHE_PREFIX_GATEWAYS = "health:gateways"
CACHE_PREFIX_SUMMARY = "health:summary"
CACHE_PREFIX_DASHBOARD = "health:dashboard"

# Default TTLs (in seconds)
# Aligned with HEALTH_CHECK_INTERVAL (300s) + buffer to prevent cache expiration
# between health check cycles and avoid fallback to database queries
DEFAULT_TTL_SYSTEM = 360       # 6 minutes (5min health check + 1min buffer)
DEFAULT_TTL_PROVIDERS = 360    # 6 minutes
DEFAULT_TTL_MODELS = 360       # 6 minutes (was 120s - caused 3min gap)
DEFAULT_TTL_GATEWAYS = 360     # 6 minutes
DEFAULT_TTL_SUMMARY = 360      # 6 minutes
DEFAULT_TTL_DASHBOARD = 90     # 1.5 minutes (more frequently accessed)


class SimpleHealthCache:
    """Simple Redis-based cache for health data"""

    def __init__(self):
        self.redis_client = get_redis_client()

    def set_cache(self, key: str, data: Any, ttl: int = 60, max_retries: int = 2) -> bool:
        """
        Store data in Redis cache with retry logic for transient failures

        Args:
            key: Cache key
            data: Data to cache (dict or dataclass)
            ttl: Time to live in seconds
            max_retries: Maximum number of retry attempts for transient errors

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client:
            logger.debug("Redis client not available, skipping cache")
            return False

        # TRANSIENT FAILURE FIX: Retry logic for Redis cache operations
        for attempt in range(max_retries + 1):
            try:
                # Serialize data to JSON
                if isinstance(data, dict):
                    serialized = json.dumps(data, default=str)
                else:
                    # Handle dataclass/Pydantic model
                    if hasattr(data, "model_dump"):
                        serialized = json.dumps(data.model_dump(), default=str)
                    elif hasattr(data, "__dict__"):
                        serialized = json.dumps(data.__dict__, default=str)
                    else:
                        serialized = json.dumps(data, default=str)

                # Store in Redis
                self.redis_client.setex(key, ttl, serialized)

                if attempt > 0:
                    logger.debug(f"Cached {key} after {attempt + 1} attempts (TTL: {ttl}s, size: {len(serialized)} bytes)")
                else:
                    logger.debug(f"Cached {key} (TTL: {ttl}s, size: {len(serialized)} bytes)")
                return True

            except Exception as e:
                # TRANSIENT FAILURE FIX: Check if error is transient
                error_str = str(e).lower()
                is_transient = any(
                    pattern in error_str
                    for pattern in [
                        "timeout",
                        "connection",
                        "network",
                        "redis",
                        "upstash",
                        "unavailable",
                        "refused",
                        "reset",
                    ]
                )

                if is_transient and attempt < max_retries:
                    logger.warning(
                        f"Transient Redis cache write error for {key} "
                        f"(attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}, retrying..."
                    )
                    # Brief delay before retry
                    import time
                    time.sleep(0.1 * (attempt + 1))  # Exponential backoff: 100ms, 200ms
                    continue

                # Non-transient error or max retries reached
                logger.error(
                    f"Failed to cache {key} after {attempt + 1} attempts: "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
                if attempt == 0:
                    # Log full traceback only on first attempt
                    logger.debug(f"Cache write error details", exc_info=True)
                return False

        return False

    def get_cache(self, key: str, max_retries: int = 2) -> dict | None:
        """
        Retrieve data from Redis cache with retry logic for transient failures

        Args:
            key: Cache key
            max_retries: Maximum number of retry attempts for transient errors

        Returns:
            Deserialized data or None if not found
        """
        if not self.redis_client:
            logger.debug("Redis client not available, skipping cache retrieval")
            return None

        # TRANSIENT FAILURE FIX: Retry logic for Redis cache reads
        for attempt in range(max_retries + 1):
            try:
                data = self.redis_client.get(key)
                if data:
                    if attempt > 0:
                        logger.debug(f"Cache HIT for {key} after {attempt + 1} attempts (size: {len(data)} bytes)")
                    else:
                        logger.debug(f"Cache HIT for {key} (size: {len(data)} bytes)")
                    return json.loads(data)
                logger.debug(f"Cache MISS for {key}")
                return None

            except Exception as e:
                # TRANSIENT FAILURE FIX: Check if error is transient
                error_str = str(e).lower()
                is_transient = any(
                    pattern in error_str
                    for pattern in [
                        "timeout",
                        "connection",
                        "network",
                        "redis",
                        "upstash",
                        "unavailable",
                        "refused",
                        "reset",
                    ]
                )

                if is_transient and attempt < max_retries:
                    logger.warning(
                        f"Transient Redis cache read error for {key} "
                        f"(attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}, retrying..."
                    )
                    # Brief delay before retry
                    import time
                    time.sleep(0.1 * (attempt + 1))  # Exponential backoff: 100ms, 200ms
                    continue

                # Non-transient error or max retries reached
                logger.error(
                    f"Failed to read cache {key} after {attempt + 1} attempts: "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
                if attempt == 0:
                    logger.debug(f"Cache read error details", exc_info=True)
                return None

        return None

    def delete_cache(self, key: str) -> bool:
        """Delete cache entry"""
        if not self.redis_client:
            return False

        try:
            self.redis_client.delete(key)
            logger.debug(f"Deleted cache {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete cache {key}: {e}")
            return False

    def clear_all_health_cache(self) -> bool:
        """Clear all health-related cache entries"""
        if not self.redis_client:
            return False

        try:
            pattern = "health:*"
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"Cleared {len(keys)} health cache entries")
            return True
        except Exception as e:
            logger.error(f"Failed to clear health cache: {e}")
            return False

    # Convenience methods for specific cache types

    def cache_system_health(self, data: Any, ttl: int = DEFAULT_TTL_SYSTEM) -> bool:
        """Cache system health data"""
        return self.set_cache(CACHE_PREFIX_SYSTEM, data, ttl)

    def get_system_health(self) -> dict | None:
        """Retrieve cached system health data"""
        return self.get_cache(CACHE_PREFIX_SYSTEM)

    def cache_providers_health(self, data: list, ttl: int = DEFAULT_TTL_PROVIDERS) -> bool:
        """Cache providers health data"""
        return self.set_cache(CACHE_PREFIX_PROVIDERS, {"providers": data}, ttl)

    def get_providers_health(self) -> list | None:
        """Retrieve cached providers health data"""
        cached = self.get_cache(CACHE_PREFIX_PROVIDERS)
        return cached.get("providers") if cached and isinstance(cached, dict) else None

    def cache_models_health(self, data: list, ttl: int = DEFAULT_TTL_MODELS) -> bool:
        """Cache models health data"""
        return self.set_cache(CACHE_PREFIX_MODELS, {"models": data}, ttl)

    def get_models_health(self) -> list | None:
        """Retrieve cached models health data"""
        cached = self.get_cache(CACHE_PREFIX_MODELS)
        return cached.get("models") if cached and isinstance(cached, dict) else None

    def cache_health_summary(self, data: Any, ttl: int = DEFAULT_TTL_SUMMARY) -> bool:
        """Cache complete health summary"""
        return self.set_cache(CACHE_PREFIX_SUMMARY, data, ttl)

    def get_health_summary(self) -> dict | None:
        """Retrieve cached health summary"""
        return self.get_cache(CACHE_PREFIX_SUMMARY)

    def cache_health_dashboard(self, data: Any, ttl: int = DEFAULT_TTL_DASHBOARD) -> bool:
        """Cache health dashboard data"""
        return self.set_cache(CACHE_PREFIX_DASHBOARD, data, ttl)

    def get_health_dashboard(self) -> dict | None:
        """Retrieve cached health dashboard data"""
        return self.get_cache(CACHE_PREFIX_DASHBOARD)

    def cache_gateways_health(self, data: dict, ttl: int = DEFAULT_TTL_GATEWAYS) -> bool:
        """Cache gateways health data"""
        return self.set_cache(CACHE_PREFIX_GATEWAYS, data, ttl)

    def get_gateways_health(self) -> dict | None:
        """Retrieve cached gateways health data"""
        return self.get_cache(CACHE_PREFIX_GATEWAYS)


# Global cache instance
simple_health_cache = SimpleHealthCache()
