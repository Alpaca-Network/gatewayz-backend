"""
Catalog Response Caching Service

Caches complete catalog API responses in Redis to minimize database queries
and improve response times.

This service implements aggressive caching with smart invalidation to achieve:
- 95%+ cache hit rates
- Sub-10ms response times for cached requests
- 90%+ reduction in database load

Usage:
    from src.services.catalog_response_cache import (
        get_cached_catalog_response,
        cache_catalog_response,
        invalidate_catalog_cache,
    )

    # Check cache first
    cached = await get_cached_catalog_response(gateway, params)
    if cached:
        return cached

    # Cache miss - fetch and cache
    result = await fetch_from_database(...)
    await cache_catalog_response(gateway, params, result)
    return result
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis as redis_module

from src.config.redis_config import get_redis_client

try:
    from prometheus_client import Counter

    cache_operations = Counter(
        "catalog_cache_operations_total", "Cache operations", ["operation", "cache_layer", "result"]
    )
except Exception:
    cache_operations = None

logger = logging.getLogger(__name__)

# Cache TTL constants (in seconds)
CATALOG_RESPONSE_CACHE_TTL = 300  # 5 minutes
METADATA_CACHE_TTL = 86400  # 24 hours

# Cache key namespace — prepended to every Redis key to avoid collisions with
# other services or provider slugs that could otherwise match a bare prefix.
# Key schema:
#   gw:catalog:v2:{gateway}:{hash}        - cached catalog API response
#   gw:catalog:metadata:{gateway}         - cache metadata (stats, timestamps)
#   gw:catalog:rebuild_lock:{cache_key}   - stampede-protection lock
CACHE_NAMESPACE = "gw:"

# Cache configuration
CATALOG_CACHE_TTL = CATALOG_RESPONSE_CACHE_TTL  # backward-compatible alias
CATALOG_CACHE_PREFIX = f"{CACHE_NAMESPACE}catalog:v2:"  # Version prefix for easy invalidation
CATALOG_METADATA_KEY = f"{CACHE_NAMESPACE}catalog:metadata"


def get_catalog_cache_key(gateway: str | None, params: dict) -> str:
    """
    Generate deterministic cache key from request parameters.

    The cache key includes all parameters that affect the response to ensure
    correct cache hits. Parameters are hashed to keep Redis keys short.

    Args:
        gateway: Gateway filter (e.g., 'openrouter', 'all', None)
        params: Query parameters (limit, offset, filters, etc.)

    Returns:
        Cache key string (e.g., "catalog:v2:all:a3f5b2c1")

    Examples:
        >>> get_catalog_cache_key("openrouter", {"limit": 100, "offset": 0})
        'catalog:v2:openrouter:8f6e...'
        >>> get_catalog_cache_key(None, {"limit": 100})
        'catalog:v2:all:9a2b...'
    """
    # Normalize gateway value
    gateway_key = gateway or "all"

    # Build cache data with all relevant parameters
    cache_data = {
        "gateway": gateway_key,
        "limit": params.get("limit", 100),
        "offset": params.get("offset", 0),
        "provider": params.get("provider"),
        "is_private": params.get("is_private"),
        "include_huggingface": params.get("include_huggingface", False),
        "unique_models": params.get("unique_models", False),
    }

    # Hash parameters to keep keys short while maintaining uniqueness
    param_hash = hashlib.md5(json.dumps(cache_data, sort_keys=True).encode()).hexdigest()[
        :8
    ]  # Use first 8 chars for readability

    return f"{CATALOG_CACHE_PREFIX}{gateway_key}:{param_hash}"


async def get_cached_catalog_response(gateway: str | None, params: dict) -> dict[str, Any] | None:
    """
    Retrieve cached catalog response if available.

    Args:
        gateway: Gateway filter ('openrouter', 'anthropic', etc.)
        params: Request parameters used to generate cache key

    Returns:
        Cached response dict with metadata, or None if cache miss

    Cache Hit Flow:
        1. Generate cache key from params
        2. Check Redis for key
        3. If found, deserialize JSON and return
        4. Track metrics (cache hit)

    Cache Miss Flow:
        1. Generate cache key from params
        2. Redis returns None
        3. Track metrics (cache miss)
        4. Caller should fetch data and cache it
    """
    try:
        redis = get_redis_client()
        if not redis:
            logger.debug("Redis not available - cache miss (no client)")
            _track_cache_miss(gateway)
            return None

        cache_key = get_catalog_cache_key(gateway, params)
        cached_data = redis.get(cache_key)

        if cached_data:
            logger.info(f"✅ Cache HIT: {cache_key}")
            _track_cache_hit(gateway)
            # Refresh TTL so frequently accessed keys stay warm
            redis.expire(cache_key, CATALOG_CACHE_TTL)

            # Deserialize and return
            return json.loads(cached_data)

        logger.debug(f"Cache MISS: {cache_key}")

        # Thundering herd / stampede protection:
        # If another coroutine is already rebuilding the cache, wait briefly
        # and retry once before returning None so the caller hits DB.
        lock_key = f"{CACHE_NAMESPACE}catalog:rebuild_lock:{cache_key}"
        try:
            acquired = redis.set(lock_key, "1", nx=True, ex=60)
            if not acquired:
                # Another request is rebuilding - wait briefly and retry cache
                import asyncio

                await asyncio.sleep(0.5)
                cached_data = redis.get(cache_key)
                if cached_data:
                    _track_cache_hit(gateway)
                    return json.loads(cached_data)
        except Exception:
            pass  # If lock fails, proceed normally

        _track_cache_miss(gateway)
        return None

    except json.JSONDecodeError as e:
        logger.warning(f"Cache data corrupted for key {cache_key}: {e}")
        _track_cache_miss(gateway)
        return None
    except redis_module.RedisError as e:
        logger.warning(f"Cache read failed: {e}")
        _track_cache_miss(gateway)
        return None


async def cache_catalog_response(
    gateway: str | None, params: dict, response: dict[str, Any], ttl: int = CATALOG_CACHE_TTL
) -> bool:
    """
    Cache catalog response in Redis.

    Args:
        gateway: Gateway filter
        params: Request parameters used to generate cache key
        response: Response data to cache (will be JSON serialized)
        ttl: Time-to-live in seconds (default: 300 = 5 minutes)

    Returns:
        True if cached successfully, False otherwise

    Implementation Notes:
        - Adds cache metadata (_cached_at, _cache_ttl) to response
        - Uses Redis SETEX for atomic set-with-expiry
        - Gracefully handles Redis failures (returns False)
        - Updates cache metadata for monitoring
    """
    try:
        redis = get_redis_client()
        if not redis:
            logger.debug("Redis not available - skip caching")
            return False

        cache_key = get_catalog_cache_key(gateway, params)

        # Add cache metadata to response
        response_with_meta = {
            **response,
            "_cached_at": datetime.now(UTC).isoformat(),
            "_cache_ttl": ttl,
            "_cache_key": cache_key,
        }

        # Serialize to JSON
        try:
            serialized = json.dumps(response_with_meta)
        except TypeError as e:
            logger.error(f"Failed to serialize response for caching: {e}")
            return False

        # Store in Redis with TTL
        redis.setex(cache_key, ttl, serialized)

        logger.info(f"💾 Cached response: {cache_key} (TTL: {ttl}s, size: {len(serialized)} bytes)")

        # Update cache metadata and Prometheus gauge for monitoring
        _update_cache_metadata(redis, gateway, len(serialized))
        _track_cache_size(gateway, len(serialized))

        # Release stampede lock now that cache is populated so other waiters can read immediately
        lock_key = f"{CACHE_NAMESPACE}catalog:rebuild_lock:{cache_key}"
        try:
            redis.delete(lock_key)
        except Exception:
            pass  # Lock will auto-expire via its 60s TTL

        return True

    except redis_module.RedisError as e:
        logger.warning(f"Cache write failed: {e}")
        return False


def invalidate_catalog_cache(gateway: str | None = None) -> int:
    """
    Invalidate catalog cache entries.

    This should be called after model sync operations to ensure fresh data.

    Args:
        gateway: If specified, only invalidate caches for this gateway.
                 If None, invalidate ALL catalog caches (use with caution).

    Returns:
        Number of cache keys deleted

    Examples:
        >>> invalidate_catalog_cache("openrouter")  # Invalidate OpenRouter only
        15
        >>> invalidate_catalog_cache()  # Invalidate all gateways
        250

    Performance Notes:
        - Uses SCAN instead of KEYS to avoid blocking Redis
        - Batch deletes keys for efficiency
        - Safe to call even if Redis unavailable
    """
    try:
        redis = get_redis_client()
        if not redis:
            logger.debug("Redis not available - skip cache invalidation")
            return 0

        if gateway:
            # Invalidate specific gateway
            pattern = f"{CATALOG_CACHE_PREFIX}{gateway}:*"
        else:
            # Invalidate all catalog caches
            pattern = f"{CATALOG_CACHE_PREFIX}*"

        # Use SCAN instead of KEYS to avoid blocking Redis
        # SCAN is cursor-based and won't block other operations
        deleted_count = 0
        cursor = 0

        while True:
            cursor, keys = redis.scan(cursor, match=pattern, count=100)

            if keys:
                # Batch delete for efficiency
                redis.delete(*keys)
                deleted_count += len(keys)

            if cursor == 0:
                break

        if deleted_count > 0:
            logger.info(f"🗑️  Invalidated {deleted_count} cache entries " f"(pattern: {pattern})")
        else:
            logger.debug(f"No cache entries to invalidate (pattern: {pattern})")

        return deleted_count

    except redis_module.RedisError as e:
        logger.warning(f"Cache invalidation failed: {e}")
        return 0


def get_cache_stats(gateway: str | None = None) -> dict[str, Any]:
    """
    Get cache statistics for monitoring.

    Args:
        gateway: Filter stats for specific gateway, or None for all

    Returns:
        Dictionary with cache statistics:
        - total_cached: Number of items cached
        - last_cached_at: Timestamp of last cache write
        - hit_rate: Cache hit rate percentage (if metrics available)
    """
    try:
        redis = get_redis_client()
        if not redis:
            return {"error": "Redis not available"}

        gateway_key = gateway or "all"
        metadata_key = f"{CATALOG_METADATA_KEY}:{gateway_key}"

        metadata = redis.hgetall(metadata_key)

        if not metadata:
            return {
                "gateway": gateway_key,
                "total_cached": 0,
                "last_cached_at": None,
            }

        return {
            "gateway": gateway_key,
            "total_cached": int(metadata.get("total_cached", 0)),
            "last_cached_at": metadata.get("last_cached_at"),
            "total_size_bytes": int(metadata.get("total_size_bytes", 0)),
        }

    except redis_module.RedisError as e:
        logger.warning(f"Failed to get cache stats: {e}")
        return {"error": str(e)}


# ==================== Private Helper Functions ====================


def _track_cache_hit(gateway: str | None):
    """Track cache hit in Prometheus metrics"""
    try:
        from src.services.prometheus_metrics import catalog_cache_hits

        catalog_cache_hits.labels(gateway=gateway or "all").inc()
    except ImportError:
        # Metrics not available - not critical
        pass
    except Exception as e:
        logger.debug(f"Failed to track cache hit: {e}")
    try:
        if cache_operations is not None:
            cache_operations.labels(operation="get", cache_layer="l1", result="hit").inc()
    except Exception as e:
        logger.debug(f"Failed to track cache_operations hit: {e}")


def _track_cache_miss(gateway: str | None):
    """Track cache miss in Prometheus metrics"""
    try:
        from src.services.prometheus_metrics import catalog_cache_misses

        catalog_cache_misses.labels(gateway=gateway or "all").inc()
    except ImportError:
        # Metrics not available - not critical
        pass
    except Exception as e:
        logger.debug(f"Failed to track cache miss: {e}")
    try:
        if cache_operations is not None:
            cache_operations.labels(operation="get", cache_layer="l1", result="miss").inc()
    except Exception as e:
        logger.debug(f"Failed to track cache_operations miss: {e}")


def _track_cache_size(gateway: str | None, size_bytes: int):
    """Update catalog_cache_size_bytes Prometheus gauge for this gateway"""
    try:
        from src.services.prometheus_metrics import catalog_cache_size_bytes

        catalog_cache_size_bytes.labels(gateway=gateway or "all").set(size_bytes)
    except ImportError:
        # Metrics not available - not critical
        pass
    except Exception as e:
        logger.debug(f"Failed to track cache size: {e}")


def _update_cache_metadata(redis, gateway: str | None, size_bytes: int):
    """
    Update cache metadata for monitoring.

    Stores statistics about cache usage:
    - total_cached: Incremented on each cache write
    - last_cached_at: Timestamp of last write
    - total_size_bytes: Cumulative size of cached data
    """
    try:
        gateway_key = gateway or "all"
        metadata_key = f"{CATALOG_METADATA_KEY}:{gateway_key}"

        # Use pipeline for atomic updates
        pipe = redis.pipeline()
        pipe.hincrby(metadata_key, "total_cached", 1)
        pipe.hincrby(metadata_key, "total_size_bytes", size_bytes)
        pipe.hset(metadata_key, "last_cached_at", datetime.now(UTC).isoformat())
        pipe.expire(metadata_key, METADATA_CACHE_TTL)  # Metadata expires after 24 hours
        pipe.execute()

    except redis_module.RedisError as e:
        # Non-critical - don't fail cache operation
        logger.debug(f"Failed to update cache metadata: {e}")
