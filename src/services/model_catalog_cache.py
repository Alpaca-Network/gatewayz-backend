#!/usr/bin/env python3
"""
Model Catalog Caching Layer
Provides high-performance Redis-backed caching for model catalog data.

This module significantly improves catalog endpoint performance by:
- Reducing catalog build time from 500ms-2s to 5-20ms (96-99% improvement)
- Caching provider model lists with background refresh
- Reducing load on database queries
- Supporting distributed caching across multiple instances

Database-first architecture:
Cache misses fetch from database (kept fresh by scheduled background sync)
instead of directly calling provider APIs.
"""

import asyncio
import json
import logging
import threading
import time
from enum import Enum
from typing import Any, Callable

try:
    import orjson
    def _serialize(data): return orjson.dumps(data)
    def _deserialize(data): return orjson.loads(data)
except ImportError:
    def _serialize(data): return json.dumps(data)
    def _deserialize(data): return json.loads(data)

from src.config.redis_config import get_redis_client, is_redis_available

import redis

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter
    try:
        cache_operations = Counter(
            'catalog_cache_operations_total',
            'Cache operations',
            ['operation', 'cache_layer', 'result']
        )
    except ValueError:
        # Already registered by catalog_response_cache — the existing instance
        # is being used there; set None here to avoid relying on private API.
        cache_operations = None
except Exception:
    cache_operations = None

# Cache TTL constants (in seconds)
CATALOG_RESPONSE_CACHE_TTL = 300   # 5 minutes
PROVIDER_MODELS_CACHE_TTL = 1800   # 30 minutes


# ============================================================================
# Debouncing Infrastructure (Issue #1099 - Prevent Cache Thrashing)
# ============================================================================

class InvalidationDebouncer:
    """
    Debounces cache invalidation requests to prevent thrashing.

    When multiple invalidation requests arrive rapidly for the same key,
    only the last one is executed after a delay. This prevents cache thrashing
    caused by cascading invalidations and rapid-fire requests.

    Example:
        # Multiple rapid requests for same key:
        invalidate("openrouter")  # Scheduled for 1s
        invalidate("openrouter")  # Cancels previous, schedules for 1s
        invalidate("openrouter")  # Cancels previous, schedules for 1s
        # Result: Only 1 invalidation executed 1s after last request
    """

    def __init__(self, delay: float = 1.0):
        """
        Initialize debouncer.

        Args:
            delay: Debounce delay in seconds (default: 1.0)
        """
        self.delay = delay
        self._pending_tasks: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._stats = {
            "scheduled": 0,
            "executed": 0,
            "coalesced": 0,  # Number of requests that were debounced/skipped
        }

    def schedule(
        self,
        key: str,
        func: Callable[[], Any],
        *args,
        **kwargs
    ) -> None:
        """
        Schedule a debounced invalidation.

        If a request for the same key is already pending, it will be
        cancelled and replaced with this new request.

        Args:
            key: Cache key to debounce on
            func: Function to execute after delay
            *args: Arguments to pass to func
            **kwargs: Keyword arguments to pass to func
        """
        with self._lock:
            # Cancel existing pending task for this key
            if key in self._pending_tasks:
                self._pending_tasks[key].cancel()
                self._stats["coalesced"] += 1
                logger.debug(f"Debounced: Cancelled previous invalidation for '{key}'")

            # Schedule new delayed execution
            def execute():
                try:
                    with self._lock:
                        if key in self._pending_tasks:
                            del self._pending_tasks[key]

                    # Execute the actual invalidation
                    func(*args, **kwargs)
                    self._stats["executed"] += 1
                    logger.debug(f"Debounced: Executed invalidation for '{key}'")
                except Exception as e:
                    logger.error(f"Debounced invalidation failed for '{key}': {e}")

            timer = threading.Timer(self.delay, execute)
            timer.start()
            self._pending_tasks[key] = timer
            self._stats["scheduled"] += 1
            logger.debug(f"Debounced: Scheduled invalidation for '{key}' in {self.delay}s")

    def cancel_all(self) -> int:
        """
        Cancel all pending debounced invalidations.

        Returns:
            Number of tasks cancelled
        """
        with self._lock:
            count = len(self._pending_tasks)
            for timer in self._pending_tasks.values():
                timer.cancel()
            self._pending_tasks.clear()
            logger.debug(f"Debouncer: Cancelled {count} pending invalidations")
            return count

    def get_stats(self) -> dict[str, Any]:
        """Get debouncing statistics"""
        with self._lock:
            return {
                **self._stats,
                "pending_count": len(self._pending_tasks),
                "efficiency_percent": round(
                    (self._stats["coalesced"] / max(self._stats["scheduled"], 1)) * 100,
                    2
                )
            }


# Global debouncer instance
_invalidation_debouncer = InvalidationDebouncer(delay=1.0)

# ============================================================================
# Cache Key Namespace
# All Redis keys in this module are prefixed with CACHE_NAMESPACE to prevent
# collisions with other services or with provider slugs that might otherwise
# overlap a bare prefix like "models:" or "providers:".
#
# Key schema:
#   gw:models:catalog:full          - full aggregated catalog
#   gw:models:provider:{name}       - per-provider catalog list
#   gw:models:model:{key}           - individual model metadata
#   gw:models:pricing:{key}         - pricing data
#   gw:models:gateway:{name}        - per-gateway catalog
#   gw:models:stats:{key}           - catalog statistics
#   gw:models:unique                - deduplicated unique models list
#   gw:models:index:{provider}      - index of model IDs for a provider
#   gw:models:unique:{model_name}   - single unique model entry
#   gw:models:unique:index          - index of all unique model names
#   gw:models:unique:filtered:...   - unique models with filter variants
#   gw:models:relationships:unique:{model_name}    - provider relationships
#   gw:models:relationships:provider:{slug}        - unique models per provider
#   gw:providers:provider:{id}      - provider metadata
#   gw:providers:index              - list of all provider IDs
# ============================================================================
CACHE_NAMESPACE = "gw:"


class CacheErrorType(Enum):
    """Classification of cache errors for better debugging"""

    REDIS_UNAVAILABLE = "redis_unavailable"
    REDIS_TIMEOUT = "redis_timeout"
    DATA_CORRUPTION = "data_corruption"
    SERIALIZATION_ERROR = "serialization_error"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN = "unknown"


class ModelCatalogCache:
    """High-performance model catalog caching with Redis backend"""

    # Cache key prefixes — all include CACHE_NAMESPACE ("gw:") to avoid
    # collisions with provider slugs or keys from other services.
    PREFIX_FULL_CATALOG = f"{CACHE_NAMESPACE}models:catalog:full"
    PREFIX_PROVIDER = f"{CACHE_NAMESPACE}models:provider"
    PREFIX_MODEL = f"{CACHE_NAMESPACE}models:model"
    PREFIX_PRICING = f"{CACHE_NAMESPACE}models:pricing"
    PREFIX_GATEWAY = f"{CACHE_NAMESPACE}models:gateway"
    PREFIX_STATS = f"{CACHE_NAMESPACE}models:stats"
    PREFIX_UNIQUE = f"{CACHE_NAMESPACE}models:unique"

    # Cache TTL values (in seconds)
    TTL_FULL_CATALOG = 900  # 15 minutes - full aggregated catalog
    TTL_PROVIDER = 1800  # 30 minutes - individual provider catalogs
    TTL_MODEL = 3600  # 60 minutes - individual model metadata
    TTL_PRICING = 3600  # 60 minutes - pricing data (relatively static)
    TTL_GATEWAY = 1800  # 30 minutes - gateway/provider catalogs
    TTL_STATS = 900  # 15 minutes - catalog statistics
    TTL_UNIQUE = 1800  # 30 minutes - unique models list

    def __init__(self):
        self.redis_client = get_redis_client()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "errors": 0,
            "invalidations": 0,
        }

    def _classify_cache_error(self, error: Exception) -> CacheErrorType:
        """
        Classify cache error for better debugging.

        Args:
            error: The exception to classify

        Returns:
            CacheErrorType enum value
        """
        error_name = type(error).__name__  # noqa: F841

        # Redis connection errors
        if isinstance(error, redis.ConnectionError):
            return CacheErrorType.REDIS_UNAVAILABLE

        # Redis timeout errors
        if isinstance(error, redis.TimeoutError):
            return CacheErrorType.REDIS_TIMEOUT

        # Data corruption / deserialization errors
        if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
            return CacheErrorType.DATA_CORRUPTION

        # Serialization errors
        if isinstance(error, (TypeError, ValueError)):
            return CacheErrorType.SERIALIZATION_ERROR

        # Permission errors
        if "permission" in str(error).lower() or isinstance(error, PermissionError):
            return CacheErrorType.PERMISSION_DENIED

        return CacheErrorType.UNKNOWN

    def _generate_key(self, prefix: str, identifier: str = "") -> str:
        """Generate cache key with prefix and optional identifier"""
        if identifier:
            return f"{prefix}:{identifier}"
        return prefix

    def _record_cache_operation(self, result: str) -> None:
        """Increment cache_operations Prometheus counter for l2 cache layer"""
        try:
            if cache_operations is not None:
                cache_operations.labels(operation='get', cache_layer='l2', result=result).inc()
        except Exception as e:
            logger.debug(f"Failed to record cache operation metric: {e}")

    # Full Catalog Caching

    def get_full_catalog(self) -> list[dict[str, Any]] | None:
        """Get cached full model catalog.

        Returns:
            Cached catalog list or None if not found/expired
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self.PREFIX_FULL_CATALOG

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug("Cache HIT: Full model catalog")
                self.redis_client.expire(key, self.TTL_FULL_CATALOG)
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug("Cache MISS: Full model catalog")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            error_type = self._classify_cache_error(e)
            logger.warning(
                f"Cache GET error | "
                f"Key: full_catalog | "
                f"Error Type: {error_type.value} | "
                f"Details: {str(e)} | "
                f"Redis Available: {is_redis_available()}"
            )
            return None

    def set_full_catalog(
        self,
        catalog: list[dict[str, Any]],
        ttl: int | None = None,
    ) -> bool:
        """Cache the full aggregated model catalog.

        Args:
            catalog: Complete model catalog
            ttl: Time to live in seconds (default: TTL_FULL_CATALOG)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_FULL_CATALOG
        ttl = ttl or self.TTL_FULL_CATALOG

        try:
            serialized_data = _serialize(catalog)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.info(f"Cache SET: Full model catalog ({len(catalog)} models, TTL: {ttl}s)")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            error_type = self._classify_cache_error(e)
            logger.warning(
                f"Cache SET error | "
                f"Key: full_catalog | "
                f"Models: {len(catalog)} | "
                f"Error Type: {error_type.value} | "
                f"Details: {str(e)}"
            )
            return False

    def invalidate_full_catalog(self) -> bool:
        """Invalidate the full catalog cache.

        Should be called when:
        - Provider catalogs are updated
        - Models are added/removed
        - Model metadata changes

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_FULL_CATALOG

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            logger.info("Cache INVALIDATE: Full model catalog")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for full catalog: {e}")
            return False

    # Provider Catalog Caching

    def get_provider_catalog(self, provider_name: str) -> list[dict[str, Any]] | None:
        """Get cached model catalog for a specific provider.

        Args:
            provider_name: Provider name (e.g., "openrouter", "portkey")

        Returns:
            Cached provider catalog or None if not found
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self._generate_key(self.PREFIX_PROVIDER, provider_name)

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug(f"Cache HIT: Provider catalog for {provider_name}")
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug(f"Cache MISS: Provider catalog for {provider_name}")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for provider {provider_name}: {e}")
            return None

    def set_provider_catalog(
        self,
        provider_name: str,
        catalog: list[dict[str, Any]],
        ttl: int | None = None,
    ) -> bool:
        """Cache model catalog for a specific provider.

        Args:
            provider_name: Provider name
            catalog: Provider's model catalog
            ttl: Time to live in seconds (default: TTL_PROVIDER)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_PROVIDER, provider_name)
        ttl = ttl or self.TTL_PROVIDER

        try:
            serialized_data = _serialize(catalog)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(
                f"Cache SET: Provider catalog for {provider_name} "
                f"({len(catalog)} models, TTL: {ttl}s)"
            )
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for provider {provider_name}: {e}")
            return False

    def invalidate_provider_catalog(
        self,
        provider_name: str,
        cascade: bool = False,
        debounce: bool = False
    ) -> bool:
        """Invalidate cached catalog for a specific provider.

        Args:
            provider_name: Provider name
            cascade: If True, also invalidate full catalog. Defaults to False to prevent
                     cache thrashing from cascading invalidations (Issue #1099).
                     Only set to True when provider changes truly affect the aggregated
                     catalog structure (e.g., during single-provider model sync).
            debounce: If True, debounce this invalidation to coalesce rapid requests
                     (Issue #1099). Useful for frontend-triggered invalidations.

        Returns:
            True if successful (or scheduled via debouncing), False otherwise
        """
        if debounce:
            # Schedule debounced invalidation
            debounce_key = f"provider:{provider_name}:cascade={cascade}"
            _invalidation_debouncer.schedule(
                debounce_key,
                self.invalidate_provider_catalog,
                provider_name=provider_name,
                cascade=cascade,
                debounce=False  # Don't re-debounce
            )
            logger.debug(f"Cache INVALIDATE (debounced): Provider '{provider_name}'")
            return True

        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_PROVIDER, provider_name)

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            if cascade:
                self.invalidate_full_catalog()
            logger.info(f"Cache INVALIDATE: Provider catalog for {provider_name} (cascade={cascade})")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for provider {provider_name}: {e}")
            return False

    # Individual Model Caching

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Get cached metadata for a specific model.

        Args:
            model_id: Model identifier

        Returns:
            Cached model data or None if not found
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self._generate_key(self.PREFIX_MODEL, model_id)

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug(f"Cache HIT: Model {model_id}")
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug(f"Cache MISS: Model {model_id}")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for model {model_id}: {e}")
            return None

    def set_model(
        self,
        model_id: str,
        model_data: dict[str, Any],
        ttl: int | None = None,
    ) -> bool:
        """Cache metadata for a specific model.

        Args:
            model_id: Model identifier
            model_data: Model metadata
            ttl: Time to live in seconds (default: TTL_MODEL)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_MODEL, model_id)
        ttl = ttl or self.TTL_MODEL

        try:
            serialized_data = _serialize(model_data)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Model {model_id} (TTL: {ttl}s)")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for model {model_id}: {e}")
            return False

    def invalidate_model(self, model_id: str) -> bool:
        """Invalidate cached metadata for a specific model.

        Args:
            model_id: Model identifier

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_MODEL, model_id)

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            logger.debug(f"Cache INVALIDATE: Model {model_id}")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for model {model_id}: {e}")
            return False

    # Pricing Cache

    def get_model_pricing(self, model_id: str) -> dict[str, Any] | None:
        """Get cached pricing data for a model.

        Args:
            model_id: Model identifier

        Returns:
            Cached pricing data or None if not found
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self._generate_key(self.PREFIX_PRICING, model_id)

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug(f"Cache HIT: Pricing for {model_id}")
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug(f"Cache MISS: Pricing for {model_id}")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for pricing {model_id}: {e}")
            return None

    def set_model_pricing(
        self,
        model_id: str,
        pricing_data: dict[str, Any],
        ttl: int | None = None,
    ) -> bool:
        """Cache pricing data for a model.

        Args:
            model_id: Model identifier
            pricing_data: Pricing information
            ttl: Time to live in seconds (default: TTL_PRICING)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_PRICING, model_id)
        ttl = ttl or self.TTL_PRICING

        try:
            serialized_data = _serialize(pricing_data)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Pricing for {model_id} (TTL: {ttl}s)")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for pricing {model_id}: {e}")
            return False

    # Batch Operations

    def invalidate_providers_batch(
        self,
        provider_names: list[str],
        cascade: bool = False
    ) -> dict[str, any]:
        """Batch invalidate multiple provider catalogs using Redis pipeline.

        This method provides significant performance improvements for bulk invalidations:
        - Single network round-trip for all deletions (vs N round-trips)
        - Atomic operation (all succeed or all fail)
        - Reduces latency from ~100ms * N to ~100ms total

        Use this when invalidating multiple providers at once (e.g., full cache refresh).

        Args:
            provider_names: List of provider names to invalidate
            cascade: If True, invalidate full catalog once at the end

        Returns:
            dict with:
            - success: bool
            - providers_invalidated: int (count)
            - keys_deleted: int (total Redis keys deleted)
            - duration_ms: float (operation duration)
        """
        if not self.redis_client or not is_redis_available():
            return {
                "success": False,
                "providers_invalidated": 0,
                "keys_deleted": 0,
                "error": "Redis unavailable"
            }

        if not provider_names:
            return {
                "success": True,
                "providers_invalidated": 0,
                "keys_deleted": 0,
                "duration_ms": 0
            }

        start_time = time.time()

        try:
            # Use Redis pipeline for atomic batch operations
            pipe = self.redis_client.pipeline()

            # Queue all provider deletions
            for provider_name in provider_names:
                key = self._generate_key(self.PREFIX_PROVIDER, provider_name)
                pipe.delete(key)

            # Execute pipeline (single network round-trip)
            results = pipe.execute()

            # Count successful deletions
            keys_deleted = sum(1 for result in results if result > 0)

            # Update stats
            self._stats["invalidations"] += len(provider_names)

            # Cascade invalidation (once, not per provider)
            if cascade:
                self.invalidate_full_catalog()

            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                f"Batch invalidate: {len(provider_names)} providers, "
                f"{keys_deleted} keys deleted, {duration_ms:.2f}ms "
                f"(cascade={cascade})"
            )

            return {
                "success": True,
                "providers_invalidated": len(provider_names),
                "keys_deleted": keys_deleted,
                "duration_ms": round(duration_ms, 2),
                "cascade": cascade
            }

        except redis.RedisError as e:
            self._stats["errors"] += 1
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Batch invalidate error: {e}", exc_info=True)
            return {
                "success": False,
                "providers_invalidated": 0,
                "keys_deleted": 0,
                "duration_ms": round(duration_ms, 2),
                "error": str(e)
            }

    def invalidate_all_models(self) -> int:
        """Invalidate all cached model data.

        Returns:
            Number of keys deleted
        """
        if not self.redis_client or not is_redis_available():
            return 0

        try:
            total_deleted = 0

            # Invalidate all model caches using SCAN (non-blocking)
            for prefix in [self.PREFIX_MODEL, self.PREFIX_PRICING, self.PREFIX_PROVIDER]:
                pattern = f"{prefix}:*"
                cursor = 0
                while True:
                    cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        deleted = self.redis_client.delete(*keys)
                        total_deleted += deleted
                    if cursor == 0:
                        break

            # Invalidate full catalog
            self.redis_client.delete(self.PREFIX_FULL_CATALOG)
            total_deleted += 1

            self._stats["invalidations"] += total_deleted
            logger.warning(f"Cache INVALIDATE ALL: {total_deleted} model cache keys deleted")
            return total_deleted

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE ALL error: {e}")
            return 0

    def _count_keys(self, pattern: str) -> int:
        """Count keys matching pattern using non-blocking SCAN."""
        count = 0
        cursor = 0
        while True:
            cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)
            count += len(keys)
            if cursor == 0:
                break
        return count

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (
            (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        )

        stats = {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "errors": self._stats["errors"],
            "invalidations": self._stats["invalidations"],
            "hit_rate_percent": round(hit_rate, 2),
            "total_requests": total_requests,
            "redis_available": is_redis_available(),
        }

        # Get cache sizes
        if self.redis_client and is_redis_available():
            try:
                stats["full_catalog_cached"] = self.redis_client.exists(self.PREFIX_FULL_CATALOG)
                stats["provider_catalogs_count"] = self._count_keys(f"{self.PREFIX_PROVIDER}:*")
                stats["models_cached_count"] = self._count_keys(f"{self.PREFIX_MODEL}:*")
                stats["pricing_cached_count"] = self._count_keys(f"{self.PREFIX_PRICING}:*")
            except redis.RedisError as e:
                logger.warning(f"Failed to get cache size stats: {e}")

        return stats

    def clear_stats(self):
        """Reset cache statistics"""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "errors": 0,
            "invalidations": 0,
        }

    # Gateway Catalog Caching (alias for provider catalog with consistent naming)

    def get_gateway_catalog(self, gateway_name: str) -> list[dict[str, Any]] | None:
        """Get cached model catalog for a specific gateway.

        This is an alias for get_provider_catalog to support consistent naming
        across the codebase (gateway vs provider terminology).

        Args:
            gateway_name: Gateway name (e.g., "openrouter", "anthropic")

        Returns:
            Cached gateway catalog or None if not found
        """
        return self.get_provider_catalog(gateway_name)

    def set_gateway_catalog(
        self,
        gateway_name: str,
        catalog: list[dict[str, Any]],
        ttl: int | None = None,
    ) -> bool:
        """Cache model catalog for a specific gateway.

        This is an alias for set_provider_catalog to support consistent naming.

        Args:
            gateway_name: Gateway name
            catalog: Gateway's model catalog
            ttl: Time to live in seconds (default: TTL_GATEWAY)

        Returns:
            True if successful, False otherwise
        """
        ttl = ttl or self.TTL_GATEWAY
        return self.set_provider_catalog(gateway_name, catalog, ttl=ttl)

    def invalidate_gateway_catalog(
        self,
        gateway_name: str,
        cascade: bool = False,
        debounce: bool = False
    ) -> bool:
        """Invalidate cached catalog for a specific gateway.

        This is an alias for invalidate_provider_catalog to support consistent naming.

        Args:
            gateway_name: Gateway name
            cascade: If True, also invalidate full catalog. Defaults to False to prevent
                     cache thrashing from cascading invalidations (Issue #1099).
            debounce: If True, debounce this invalidation to coalesce rapid requests
                     (Issue #1099).

        Returns:
            True if successful (or scheduled via debouncing), False otherwise
        """
        return self.invalidate_provider_catalog(gateway_name, cascade=cascade, debounce=debounce)

    # Catalog Statistics Caching

    def get_catalog_stats(self) -> dict[str, Any] | None:
        """Get cached catalog statistics.

        Returns:
            Cached statistics or None if not found
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self.PREFIX_STATS

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug("Cache HIT: Catalog statistics")
                self.redis_client.expire(key, self.TTL_STATS)
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug("Cache MISS: Catalog statistics")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for catalog stats: {e}")
            return None

    def set_catalog_stats(
        self,
        stats: dict[str, Any],
        ttl: int | None = None,
    ) -> bool:
        """Cache catalog statistics.

        Args:
            stats: Catalog statistics
            ttl: Time to live in seconds (default: TTL_STATS)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_STATS
        ttl = ttl or self.TTL_STATS

        try:
            serialized_data = _serialize(stats)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Catalog statistics (TTL: {ttl}s)")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for catalog stats: {e}")
            return False

    def invalidate_catalog_stats(self) -> bool:
        """Invalidate cached catalog statistics.

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_STATS

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            logger.debug("Cache INVALIDATE: Catalog statistics")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for catalog stats: {e}")
            return False

    # Unique Models Caching

    def get_unique_models(self) -> list[dict[str, Any]] | None:
        """Get cached unique models list.

        Returns:
            Cached unique models or None if not found
        """
        if not self.redis_client or not is_redis_available():
            return None

        key = self.PREFIX_UNIQUE

        try:
            cached_data = self.redis_client.get(key)
            if cached_data:
                self._stats["hits"] += 1
                self._record_cache_operation('hit')
                logger.debug("Cache HIT: Unique models")
                self.redis_client.expire(key, self.TTL_UNIQUE)
                return _deserialize(cached_data)
            else:
                self._stats["misses"] += 1
                self._record_cache_operation('miss')
                logger.debug("Cache MISS: Unique models")
                return None

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for unique models: {e}")
            return None

    def set_unique_models(
        self,
        unique_models: list[dict[str, Any]],
        ttl: int | None = None,
    ) -> bool:
        """Cache unique models list.

        Args:
            unique_models: List of unique models with providers
            ttl: Time to live in seconds (default: TTL_UNIQUE)

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_UNIQUE
        ttl = ttl or self.TTL_UNIQUE

        try:
            serialized_data = _serialize(unique_models)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Unique models ({len(unique_models)} models, TTL: {ttl}s)")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for unique models: {e}")
            return False

    def invalidate_unique_models(self) -> bool:
        """Invalidate cached unique models list.

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self.PREFIX_UNIQUE

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            logger.debug("Cache INVALIDATE: Unique models")
            return True

        except redis.RedisError as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for unique models: {e}")
            return False


# Global cache instance
_model_catalog_cache: ModelCatalogCache | None = None

# Stampede protection locks — prevent multiple threads from rebuilding cache simultaneously
# after invalidation. Only one thread fetches from DB while others wait for the result.
# CRITICAL: Must use RLock (reentrant) because pricing enrichment can trigger recursive
# catalog lookups on the SAME thread (e.g., cross-reference pricing calls get_cached_models
# for the same provider). A regular Lock() deadlocks in this case.
_rebuild_lock_full_catalog = threading.RLock()
_rebuild_lock_unique_models = threading.RLock()
_rebuild_locks_provider: dict[str, threading.RLock] = {}


def get_model_catalog_cache() -> ModelCatalogCache:
    """Get or create global model catalog cache instance"""
    global _model_catalog_cache
    if _model_catalog_cache is None:
        _model_catalog_cache = ModelCatalogCache()
    return _model_catalog_cache


# Convenience functions for common operations


def cache_full_catalog(catalog: list[dict[str, Any]], ttl: int | None = None) -> bool:
    """Cache the full aggregated model catalog"""
    cache = get_model_catalog_cache()
    return cache.set_full_catalog(catalog, ttl=ttl)


def get_cached_full_catalog() -> list[dict[str, Any]] | None:
    """
    Get cached full model catalog with multi-tier caching and step logging.

    Cache hierarchy:
    1. Redis (primary) - distributed cache
    2. Local memory (fallback) - for when Redis is slow/unavailable
    3. Database (last resort) - kept fresh by scheduled sync

    Returns:
        Cached catalog or empty list on error
    """
    from src.services.local_memory_cache import get_local_catalog, set_local_catalog
    from src.utils.step_logger import StepLogger

    step_logger = StepLogger("Cache: Fetch Full Catalog", total_steps=3)
    step_logger.start(cache_type="full_catalog")

    cache = get_model_catalog_cache()

    # Step 1: Try Redis cache
    step_logger.step(1, "Checking Redis cache", cache_layer="redis")
    cached = cache.get_full_catalog()
    if cached is not None:
        # Also update local cache for fallback
        set_local_catalog("all", cached)
        step_logger.success(result="HIT", count=len(cached))
        step_logger.complete(source="redis", models=len(cached))
        return cached
    step_logger.success(result="MISS")

    # Step 2: Try local memory cache (fallback for Redis failures)
    step_logger.step(2, "Checking local memory cache", cache_layer="local_memory")
    local_data, is_stale = get_local_catalog("all")
    if local_data is not None:
        if is_stale:
            step_logger.success(result="STALE_HIT", count=len(local_data), stale="true")
        else:
            step_logger.success(result="HIT", count=len(local_data))
        step_logger.complete(source="local_memory", models=len(local_data), stale=is_stale)
        return local_data
    step_logger.success(result="MISS")

    # Step 3: Fetch from database (cache miss everywhere)
    # Use stampede lock to prevent thundering herd — only one thread rebuilds
    step_logger.step(3, "Acquiring rebuild lock", cache_layer="stampede_protection")
    with _rebuild_lock_full_catalog:
        # Double-check: another thread may have rebuilt while we waited for the lock
        cached = cache.get_full_catalog()
        if cached is not None:
            set_local_catalog("all", cached)
            step_logger.success(result="REBUILT_BY_OTHER_THREAD", count=len(cached))
            step_logger.complete(source="redis_after_lock", models=len(cached))
            return cached

        # Rebuild full catalog by assembling per-provider catalogs.
        # Each provider is fetched individually (small, fast queries that never
        # hit the 120s wall-clock deadline) instead of one giant all-models query.
        step_logger.step(3, "Rebuilding from per-provider caches", cache_layer="provider_assembly")
        try:
            api_models = rebuild_full_catalog_from_providers()
            step_logger.success(api_models=len(api_models))

            step_logger.complete(source="provider_assembly", models=len(api_models), cache_status="populated")
            return api_models

        except Exception as e:
            step_logger.failure(e, source="provider_assembly")
            logger.error(f"Error rebuilding catalog from providers: {e}")
            return []


def rebuild_full_catalog_from_providers() -> list[dict[str, Any]]:
    """
    Build the full catalog by assembling individually-cached per-provider catalogs.

    This avoids the single-giant-query timeout issue in get_all_models_for_catalog()
    by fetching each provider separately (small, fast queries) and merging.

    Flow:
    1. Get list of known provider slugs from the database
    2. Fetch each provider's catalog (from Redis cache or DB on miss via
       get_cached_provider_catalog which has its own Redis → local → DB fallback)
    3. Merge all providers into a single list
    4. Cache the merged result as the full catalog in Redis + local memory
    5. Return the complete catalog

    Each per-provider query is small (100-5000 rows) and well within the
    DB_QUERY_TIMEOUT_SECONDS deadline, unlike the monolithic all-models query
    which times out at ~3600 rows for 17k+ model catalogs.

    Returns:
        Complete merged catalog (all providers), or empty list on total failure.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.services.local_memory_cache import set_local_catalog

    cache = get_model_catalog_cache()

    # --- Step 1: Discover provider slugs ---
    # Use the database as the authoritative source of active provider slugs.
    # This avoids hardcoding the list and automatically picks up new providers.
    provider_slugs: list[str] = []
    try:
        from src.config.supabase_config import get_client_for_query

        supabase = get_client_for_query(read_only=True)
        response = (
            supabase.table("providers")
            .select("slug")
            .eq("is_active", True)
            .execute()
        )
        provider_slugs = [
            row["slug"] for row in (response.data or []) if row.get("slug")
        ]
        logger.info(
            f"rebuild_full_catalog_from_providers: discovered {len(provider_slugs)} "
            f"active provider slugs from database"
        )
    except Exception as e:
        logger.warning(
            f"rebuild_full_catalog_from_providers: failed to fetch provider slugs "
            f"from database ({e}), falling back to PROVIDER_SLUGS from catalog"
        )
        try:
            from src.routes.catalog import PROVIDER_SLUGS
            provider_slugs = list(PROVIDER_SLUGS)
        except Exception:
            logger.error(
                "rebuild_full_catalog_from_providers: cannot determine provider slugs, "
                "aborting rebuild"
            )
            return []

    if not provider_slugs:
        logger.warning("rebuild_full_catalog_from_providers: no provider slugs found")
        return []

    # --- Step 2: Fetch per-provider catalogs in parallel ---
    # get_cached_provider_catalog already has Redis → local memory → DB fallback
    # and per-provider stampede protection, so we just call it for each slug.
    # With 30+ providers, use 10 workers to complete in ~3 batches while leaving
    # plenty of headroom in the connection pool (50 max connections). Most providers
    # will hit Redis cache (sub-ms), so only a few use DB connections simultaneously.
    MAX_WORKERS = 10
    all_models: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}
    failed_providers: list[str] = []

    def _fetch_provider(slug: str) -> tuple[str, list[dict[str, Any]]]:
        """Fetch a single provider's catalog (runs in thread pool)."""
        try:
            models = get_cached_provider_catalog(slug) or []
            return (slug, models)
        except Exception as exc:
            logger.warning(
                f"rebuild_full_catalog_from_providers: failed to fetch {slug}: {exc}"
            )
            return (slug, [])

    import time as _time
    fetch_start = _time.monotonic()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_provider, slug): slug
            for slug in provider_slugs
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                slug, models = future.result(timeout=120)
                if models:
                    all_models.extend(models)
                    provider_counts[slug] = len(models)
                else:
                    failed_providers.append(slug)
            except Exception as exc:
                logger.warning(
                    f"rebuild_full_catalog_from_providers: {slug} future failed: {exc}"
                )
                failed_providers.append(slug)

    fetch_elapsed = _time.monotonic() - fetch_start

    logger.info(
        f"rebuild_full_catalog_from_providers: assembled {len(all_models)} models "
        f"from {len(provider_counts)} providers in {fetch_elapsed:.1f}s "
        f"(failed: {len(failed_providers)})"
    )
    if failed_providers:
        logger.warning(
            f"rebuild_full_catalog_from_providers: providers with 0 models: "
            f"{failed_providers}"
        )

    # --- Step 3: Cache the assembled full catalog ---
    if all_models:
        cache.set_full_catalog(all_models, ttl=ModelCatalogCache.TTL_FULL_CATALOG)
        set_local_catalog("all", all_models)
        logger.info(
            f"rebuild_full_catalog_from_providers: cached full catalog "
            f"({len(all_models)} models, TTL={ModelCatalogCache.TTL_FULL_CATALOG}s)"
        )

    return all_models


def invalidate_full_catalog() -> bool:
    """Invalidate the full catalog cache"""
    cache = get_model_catalog_cache()
    return cache.invalidate_full_catalog()


def cache_provider_catalog(
    provider_name: str,
    catalog: list[dict[str, Any]],
    ttl: int | None = None
) -> bool:
    """Cache model catalog for a specific provider"""
    cache = get_model_catalog_cache()
    return cache.set_provider_catalog(provider_name, catalog, ttl=ttl)


def get_cached_provider_catalog(provider_name: str) -> list[dict[str, Any]] | None:
    """
    Get cached provider catalog with multi-tier caching and background refresh.

    Cache hierarchy:
    1. Redis (primary) - distributed cache
    2. Local memory (fallback) - for when Redis is slow/unavailable
    3. Database (last resort) - kept fresh by scheduled sync

    Features background cache warming when stale data is detected.

    Args:
        provider_name: Provider slug (e.g., "openrouter", "anthropic")

    Returns:
        Cached provider catalog or empty list on error
    """
    from src.services.local_memory_cache import get_local_catalog, set_local_catalog
    from src.services.cache_warmer import get_cache_warmer

    cache = get_model_catalog_cache()

    # 1. Try Redis first
    cached = cache.get_provider_catalog(provider_name)
    if cached is not None:
        # Also update local cache for fallback
        set_local_catalog(provider_name, cached)
        return cached

    # 2. Try local memory cache (fallback for Redis failures)
    local_data, is_stale = get_local_catalog(provider_name)
    if local_data is not None:
        if is_stale:
            logger.debug(f"Local cache STALE HIT: {provider_name} (returning stale data)")

            # Trigger background refresh using cache warmer
            # This prevents thundering herd - only one refresh at a time
            def fetch_fresh_data():
                from src.db.models_catalog_db import (
                    get_models_by_gateway_for_catalog,
                    transform_db_models_batch,
                )
                db_models = get_models_by_gateway_for_catalog(
                    gateway_slug=provider_name,
                    include_inactive=False
                )
                return transform_db_models_batch(db_models)

            def update_caches(fresh_data):
                cache.set_provider_catalog(provider_name, fresh_data, ttl=ModelCatalogCache.TTL_PROVIDER)
                set_local_catalog(provider_name, fresh_data)

            # Fire and forget background refresh
            warmer = get_cache_warmer()
            warmer.warm_cache_sync(
                cache_key=f"provider:{provider_name}",
                fetch_fn=fetch_fresh_data,
                set_cache_fn=update_caches,
            )
        else:
            logger.debug(f"Local cache HIT: {provider_name}")

        return local_data

    # 3. Cache miss everywhere - fetch from database with stampede protection
    logger.debug(f"Cache MISS (all layers): Fetching {provider_name} catalog from database")

    lock = _rebuild_locks_provider.setdefault(provider_name, threading.RLock())
    with lock:
        # Double-check: another thread may have rebuilt while we waited
        cached = cache.get_provider_catalog(provider_name)
        if cached is not None:
            set_local_catalog(provider_name, cached)
            return cached

        try:
            from src.db.models_catalog_db import (
                get_models_by_gateway_for_catalog,
                transform_db_models_batch,
            )
            from src.services.models import _set_building_catalog

            # Set building flag so pricing enrichment skips cross-reference
            # lookups that would trigger recursive catalog fetches and deadlock.
            _set_building_catalog(True)
            try:
                # Fetch from database
                db_models = get_models_by_gateway_for_catalog(
                    gateway_slug=provider_name,
                    include_inactive=False
                )

                # Transform to API format
                api_models = transform_db_models_batch(db_models)
            finally:
                _set_building_catalog(False)

            # Cache in both Redis and local memory
            cache.set_provider_catalog(provider_name, api_models, ttl=ModelCatalogCache.TTL_PROVIDER)
            set_local_catalog(provider_name, api_models)

            logger.info(f"Fetched {len(api_models)} models for {provider_name} from database and cached")

            return api_models

        except Exception as e:
            logger.error(f"Error fetching {provider_name} catalog from database: {e}")
            return []


def invalidate_provider_catalog(
    provider_name: str,
    cascade: bool = False,
    debounce: bool = False
) -> bool:
    """Invalidate cached provider catalog.

    Args:
        provider_name: Provider name to invalidate
        cascade: If True, also invalidate full catalog. Defaults to False to prevent
                 cache thrashing (Issue #1099).
        debounce: If True, debounce this invalidation to coalesce rapid requests
                 (Issue #1099). Recommended for frontend-triggered invalidations.

    Returns:
        True if successful (or scheduled via debouncing), False otherwise
    """
    cache = get_model_catalog_cache()
    return cache.invalidate_provider_catalog(provider_name, cascade=cascade, debounce=debounce)


def get_catalog_cache_stats() -> dict[str, Any]:
    """Get model catalog cache statistics"""
    cache = get_model_catalog_cache()
    return cache.get_stats()


def clear_all_model_caches() -> int:
    """Clear all model-related caches"""
    cache = get_model_catalog_cache()
    return cache.invalidate_all_models()


# Gateway catalog convenience functions


def cache_gateway_catalog(
    gateway_name: str,
    catalog: list[dict[str, Any]],
    ttl: int | None = None,
) -> bool:
    """Cache model catalog for a specific gateway"""
    cache = get_model_catalog_cache()
    return cache.set_gateway_catalog(gateway_name, catalog, ttl=ttl)


def get_cached_gateway_catalog(gateway_name: str) -> list[dict[str, Any]] | None:
    """
    Get cached gateway catalog with multi-tier caching and background refresh.

    Cache hierarchy:
    1. Redis (primary) - distributed cache
    2. Local memory (fallback) - for when Redis is slow/unavailable
    3. Database (last resort) - kept fresh by scheduled sync

    Features background cache warming when stale data is detected.

    Args:
        gateway_name: Gateway slug (e.g., "openrouter", "anthropic")

    Returns:
        Cached gateway catalog or empty list on error
    """
    from src.services.local_memory_cache import get_local_catalog, set_local_catalog
    from src.services.cache_warmer import get_cache_warmer

    cache = get_model_catalog_cache()

    # 1. Try Redis first
    cached = cache.get_gateway_catalog(gateway_name)
    if cached is not None:
        # Also update local cache for fallback
        set_local_catalog(gateway_name, cached)
        return cached

    # 2. Try local memory cache (fallback for Redis failures)
    local_data, is_stale = get_local_catalog(gateway_name)
    if local_data is not None:
        if is_stale:
            logger.debug(f"Local cache STALE HIT: {gateway_name} (returning stale data)")

            # Trigger background refresh using cache warmer
            def fetch_fresh_data():
                from src.db.models_catalog_db import (
                    get_models_by_gateway_for_catalog,
                    transform_db_models_batch,
                )
                db_models = get_models_by_gateway_for_catalog(
                    gateway_slug=gateway_name,
                    include_inactive=False
                )
                return transform_db_models_batch(db_models)

            def update_caches(fresh_data):
                cache.set_gateway_catalog(gateway_name, fresh_data, ttl=ModelCatalogCache.TTL_GATEWAY)
                set_local_catalog(gateway_name, fresh_data)

            # Fire and forget background refresh
            warmer = get_cache_warmer()
            warmer.warm_cache_sync(
                cache_key=f"gateway:{gateway_name}",
                fetch_fn=fetch_fresh_data,
                set_cache_fn=update_caches,
            )
        else:
            logger.debug(f"Local cache HIT: {gateway_name}")

        return local_data

    # 3. Cache miss everywhere - fetch from database with stampede protection
    logger.debug(f"Cache MISS (all layers): Fetching {gateway_name} catalog from database")

    lock = _rebuild_locks_provider.setdefault(gateway_name, threading.RLock())
    with lock:
        # Double-check: another thread may have rebuilt while we waited
        cached = cache.get_gateway_catalog(gateway_name)
        if cached is not None:
            set_local_catalog(gateway_name, cached)
            return cached

        try:
            from src.db.models_catalog_db import (
                get_models_by_gateway_for_catalog,
                transform_db_models_batch,
            )
            from src.services.models import _set_building_catalog

            # Set building flag so pricing enrichment skips cross-reference
            # lookups that would trigger recursive catalog fetches and deadlock.
            _set_building_catalog(True)
            try:
                # Fetch from database
                db_models = get_models_by_gateway_for_catalog(
                    gateway_slug=gateway_name,
                    include_inactive=False
                )

                # Transform to API format
                api_models = transform_db_models_batch(db_models)
            finally:
                _set_building_catalog(False)

            # Cache in both Redis and local memory
            cache.set_gateway_catalog(gateway_name, api_models, ttl=ModelCatalogCache.TTL_GATEWAY)
            set_local_catalog(gateway_name, api_models)

            logger.info(f"Fetched {len(api_models)} models for {gateway_name} from database and cached")

            return api_models

        except Exception as e:
            logger.error(f"Error fetching {gateway_name} catalog from database: {e}")
            return []


def invalidate_gateway_catalog(
    gateway_name: str,
    cascade: bool = False,
    debounce: bool = False
) -> bool:
    """Invalidate cached gateway catalog.

    Args:
        gateway_name: Gateway name to invalidate
        cascade: If True, also invalidate full catalog. Defaults to False to prevent
                 cache thrashing (Issue #1099).
        debounce: If True, debounce this invalidation to coalesce rapid requests
                 (Issue #1099). Recommended for frontend-triggered invalidations.

    Returns:
        True if successful (or scheduled via debouncing), False otherwise
    """
    cache = get_model_catalog_cache()
    return cache.invalidate_gateway_catalog(gateway_name, cascade=cascade, debounce=debounce)


# Unique models convenience functions


def get_cached_unique_models() -> list[dict[str, Any]] | None:
    """
    Get cached unique models with multi-tier caching.

    Cache hierarchy:
    1. Redis (primary) - distributed cache
    2. Local memory (fallback) - for when Redis is slow/unavailable
    3. Database (last resort) - kept fresh by scheduled sync

    Returns:
        Cached unique models or empty list on error
    """
    from src.services.local_memory_cache import get_local_catalog, set_local_catalog

    cache = get_model_catalog_cache()

    # 1. Try Redis first
    cached = cache.get_unique_models()
    if cached is not None:
        # Also update local cache for fallback
        set_local_catalog("unique", cached)
        return cached

    # 2. Try local memory cache (fallback for Redis failures)
    local_data, is_stale = get_local_catalog("unique")
    if local_data is not None:
        if is_stale:
            logger.debug("Local cache STALE HIT: unique models (returning stale data)")
        else:
            logger.debug("Local cache HIT: unique models")
        return local_data

    # 3. Cache miss everywhere - compute from database with stampede protection
    logger.debug("Cache MISS (all layers): Computing unique models from database")

    with _rebuild_lock_unique_models:
        # Double-check: another thread may have rebuilt while we waited
        cached = cache.get_unique_models()
        if cached is not None:
            set_local_catalog("unique", cached)
            return cached

        try:
            from src.db.models_catalog_db import (
                get_all_unique_models_for_catalog,
                transform_db_models_batch,
            )

            # Fetch unique models from database
            db_models = get_all_unique_models_for_catalog(include_inactive=False)

            # Transform to API format
            api_models = transform_db_models_batch(db_models)

            # Cache in both Redis and local memory
            cache.set_unique_models(api_models, ttl=ModelCatalogCache.TTL_UNIQUE)
            set_local_catalog("unique", api_models)

            logger.info(f"Computed {len(api_models)} unique models from database and cached")

            return api_models

        except Exception as e:
            logger.error(f"Error computing unique models from database: {e}")
            return []


def cache_unique_models(unique_models: list[dict[str, Any]], ttl: int | None = None) -> bool:
    """Cache unique models list"""
    cache = get_model_catalog_cache()
    return cache.set_unique_models(unique_models, ttl=ttl)


def invalidate_unique_models() -> bool:
    """Invalidate cached unique models"""
    cache = get_model_catalog_cache()
    return cache.invalidate_unique_models()


# Catalog statistics convenience functions


def get_cached_catalog_stats() -> dict[str, Any] | None:
    """Get cached catalog statistics"""
    cache = get_model_catalog_cache()
    return cache.get_catalog_stats()


def cache_catalog_stats(stats: dict[str, Any], ttl: int | None = None) -> bool:
    """Cache catalog statistics"""
    cache = get_model_catalog_cache()
    return cache.set_catalog_stats(stats, ttl=ttl)


def invalidate_catalog_stats() -> bool:
    """Invalidate cached catalog statistics"""
    cache = get_model_catalog_cache()
    return cache.invalidate_catalog_stats()


# ============================================================================
# Backward Compatibility Wrappers for Routes
# ============================================================================
# These functions provide dict-like cache info compatible with old src.cache API
# Allows route files to migrate away from src.cache imports cleanly


def get_gateway_cache_metadata(gateway_name: str) -> dict[str, Any]:
    """
    Get cache metadata for a gateway (backward compatibility wrapper).

    Returns dict with keys matching old cache.py format:
    - data: List of cached models or None
    - timestamp: Cache timestamp or None
    - ttl: Time to live in seconds

    Args:
        gateway_name: Gateway slug (e.g., "openrouter", "anthropic")

    Returns:
        Cache info dict
    """
    cached_data = get_cached_gateway_catalog(gateway_name)
    return {
        "data": cached_data,
        "timestamp": None,  # Could be enhanced to fetch actual timestamp from Redis TTL
        "ttl": ModelCatalogCache.TTL_GATEWAY,  # 30 minutes - matches TTL_GATEWAY
    }


def get_provider_cache_metadata() -> dict[str, Any]:
    """
    Get providers cache metadata (backward compatibility wrapper).

    Returns dict with keys matching old cache.py format:
    - data: List of cached providers or None
    - timestamp: Cache timestamp or None
    - ttl: Time to live in seconds

    Returns:
        Cache info dict
    """
    # Note: providers are typically stored under a specific key
    # For backward compatibility, we return provider catalog data
    cached_data = get_cached_provider_catalog("providers")
    return {
        "data": cached_data,
        "timestamp": None,
        "ttl": ModelCatalogCache.TTL_PROVIDER,  # 30 minutes
    }


def clear_models_cache(gateway: str, cascade: bool = False, debounce: bool = False) -> None:
    """
    Clear cache for a specific gateway (backward compatibility wrapper).

    Args:
        gateway: Gateway name to clear cache for
        cascade: If True, also invalidate full catalog. Defaults to False to prevent
                 cache thrashing (Issue #1099).
        debounce: If True, debounce this invalidation to coalesce rapid requests
                 (Issue #1099).
    """
    invalidate_gateway_catalog(gateway, cascade=cascade, debounce=debounce)


def clear_providers_cache() -> None:
    """Clear providers cache (backward compatibility wrapper)."""
    invalidate_provider_catalog("providers")


# ============================================================================
# Smart Caching with Individual Model Keys (Phase 1)
# ============================================================================


def set_provider_catalog_smart(
    provider_name: str,
    catalog: list[dict[str, Any]],
    ttl: int | None = None,
) -> dict[str, Any]:
    """
    Cache provider catalog using individual model keys for smart updates.

    Instead of one big JSON blob per provider, stores each model individually.
    This enables:
    - Updating only changed models (99% less cache writes)
    - Granular invalidation
    - Memory efficiency (Redis auto-expires individual models)

    Args:
        provider_name: Provider slug (e.g., "openai", "anthropic")
        catalog: List of models for this provider
        ttl: Time to live in seconds (default: TTL_PROVIDER)

    Returns:
        dict with stats: {
            "success": bool,
            "models_cached": int,
            "provider": str
        }
    """
    if not catalog or not isinstance(catalog, list):
        logger.warning(f"Invalid catalog for {provider_name}: empty or not a list")
        return {"success": False, "models_cached": 0, "provider": provider_name}

    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_PROVIDER
    cached_count = 0

    try:
        # Collect valid models and their IDs up front
        valid_models = []
        for model in catalog:
            model_id = model.get("id") or model.get("slug") or model.get("provider_model_id")
            if not model_id:
                logger.warning(f"Model missing ID in {provider_name} catalog: {model}")
                continue
            valid_models.append((model_id, model))

        model_ids = [mid for mid, _ in valid_models]

        # Store all individual model keys + index in a single pipeline round-trip
        if cache.redis_client and is_redis_available() and valid_models:
            pipe = cache.redis_client.pipeline()
            for model_id, model in valid_models:
                # Individual model key: "models:model:{provider}:{model_id}"
                model_key = f"{cache.PREFIX_MODEL}:{provider_name}:{model_id}"
                pipe.setex(model_key, ttl, _serialize(model))
            # Append index write to the same pipeline
            pipe.setex(f"{CACHE_NAMESPACE}models:index:{provider_name}", ttl, _serialize(model_ids))
            results = pipe.execute()
            # Count successful model setex calls (last result is the index write)
            cached_count = sum(1 for r in results[:-1] if r)

        logger.info(
            f"Smart cache SET: {provider_name} - {cached_count}/{len(catalog)} models cached individually (TTL: {ttl}s)"
        )

        return {
            "success": True,
            "models_cached": cached_count,
            "provider": provider_name,
            "ttl": ttl
        }

    except redis.RedisError as e:
        logger.warning(f"Error in smart cache SET for {provider_name}: {e}")
        return {
            "success": False,
            "models_cached": cached_count,
            "provider": provider_name,
            "error": str(e)
        }


def get_provider_catalog_smart(provider_name: str) -> list[dict[str, Any]] | None:
    """
    Get provider catalog from individual model keys.

    Reconstructs catalog by fetching all individual models for this provider.
    Falls back to legacy method if individual keys not found.

    Args:
        provider_name: Provider slug (e.g., "openai", "anthropic")

    Returns:
        List of models or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        # Get index of model IDs for this provider
        if cache.redis_client and is_redis_available():
            index_data = cache.redis_client.get(f"{CACHE_NAMESPACE}models:index:{provider_name}")
            if not index_data:
                # Fall back to legacy method
                logger.debug(f"No index found for {provider_name}, falling back to legacy cache")
                return cache.get_provider_catalog(provider_name)

            model_ids = _deserialize(index_data)

            # Fetch all individual model keys in a single pipeline round-trip
            if model_ids:
                pipe = cache.redis_client.pipeline()
                for model_id in model_ids:
                    model_key = f"{cache.PREFIX_MODEL}:{provider_name}:{model_id}"
                    pipe.get(model_key)
                results = pipe.execute()

                models = []
                for raw in results:
                    if raw:
                        try:
                            models.append(_deserialize(raw))
                        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                            logger.debug(f"Skipping corrupted cache entry for model {model_id}: {e}")

            if models:
                logger.debug(f"Smart cache HIT: {provider_name} - {len(models)} models retrieved individually")
                return models

        # Fall back to legacy method if smart cache not available
        return cache.get_provider_catalog(provider_name)

    except redis.RedisError as e:
        logger.warning(f"Error in smart cache GET for {provider_name}: {e}")
        # Fall back to legacy method
        return cache.get_provider_catalog(provider_name)


# ============================================================================
# Change Detection for Incremental Sync (Phase 2)
# ============================================================================


def has_model_changed(old_model: dict[str, Any], new_model: dict[str, Any]) -> bool:
    """
    Detect if a model has actually changed between versions.

    Compares critical fields to determine if cache/database update is needed.
    This prevents unnecessary writes when models haven't actually changed.

    Args:
        old_model: Previously cached/stored model data
        new_model: Newly fetched model data from provider API

    Returns:
        True if model changed and needs update, False otherwise
    """
    # Quick check: if one is None, they're different
    if not old_model or not new_model:
        return True

    # Compare critical fields that matter for API responses
    critical_fields = [
        "pricing",           # Pricing changes
        "context_length",    # Context window updates
        "description",       # Model description changes
        "modality",          # Capability changes
        "supports_streaming",
        "supports_function_calling",
        "supports_vision",
        "is_active",         # Availability changes
        "health_status",     # Health status updates
    ]

    for field in critical_fields:
        old_value = old_model.get(field)
        new_value = new_model.get(field)

        # Handle nested pricing dict specially
        if field == "pricing" and isinstance(old_value, dict) and isinstance(new_value, dict):
            # Compare pricing fields
            pricing_fields = ["prompt", "completion", "image", "request"]
            for p_field in pricing_fields:
                if str(old_value.get(p_field)) != str(new_value.get(p_field)):
                    logger.debug(
                        f"Model changed: pricing.{p_field} changed from "
                        f"{old_value.get(p_field)} to {new_value.get(p_field)}"
                    )
                    return True
        elif old_value != new_value:
            logger.debug(f"Model changed: {field} changed from {old_value} to {new_value}")
            return True

    # No changes detected
    return False


def find_changed_models(
    cached_models: list[dict[str, Any]],
    new_models: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Find which models changed, were added, or were deleted.

    Compares old and new model catalogs to determine delta.
    Returns only models that need database/cache updates.

    Args:
        cached_models: Previously cached model list
        new_models: Newly fetched model list from provider API

    Returns:
        dict with:
        - changed: List of models that changed
        - added: List of new models
        - deleted: List of model IDs that were removed
        - unchanged: Count of models that didn't change
    """
    # Build lookup by model ID
    cached_by_id = {}
    for model in cached_models or []:
        model_id = model.get("id") or model.get("slug") or model.get("provider_model_id")
        if model_id:
            cached_by_id[model_id] = model

    new_by_id = {}
    for model in new_models or []:
        model_id = model.get("id") or model.get("slug") or model.get("provider_model_id")
        if model_id:
            new_by_id[model_id] = model

    changed = []
    added = []
    unchanged_count = 0

    # Find changed and added models
    for model_id, new_model in new_by_id.items():
        if model_id in cached_by_id:
            # Model exists - check if changed
            if has_model_changed(cached_by_id[model_id], new_model):
                changed.append(new_model)
            else:
                unchanged_count += 1
        else:
            # New model
            added.append(new_model)

    # Find deleted models
    deleted_ids = [
        model_id for model_id in cached_by_id.keys()
        if model_id not in new_by_id
    ]

    return {
        "changed": changed,
        "added": added,
        "deleted": deleted_ids,
        "unchanged": unchanged_count,
        "total_new": len(new_models),
        "total_cached": len(cached_models or [])
    }


def update_provider_catalog_incremental(
    provider_name: str,
    new_models: list[dict[str, Any]],
    ttl: int | None = None
) -> dict[str, Any]:
    """
    Update provider catalog incrementally - only update what changed.

    This is the SMART caching function that:
    1. Fetches current cache
    2. Detects what changed
    3. Only updates changed/added models
    4. Removes deleted models
    5. Skips unchanged models entirely

    Result: 95-99% reduction in cache operations!

    Args:
        provider_name: Provider slug
        new_models: New model list from provider API
        ttl: Time to live in seconds

    Returns:
        dict with operation stats
    """
    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_PROVIDER

    try:
        # Get current cached models
        cached_models = get_provider_catalog_smart(provider_name) or []

        # Find delta
        delta = find_changed_models(cached_models, new_models)

        # Update changed models
        updated_count = 0
        for model in delta["changed"]:
            model_id = model.get("id") or model.get("slug") or model.get("provider_model_id")
            if model_id:
                model_key = f"{provider_name}:{model_id}"
                if cache.set_model(model_key, model, ttl=ttl):
                    updated_count += 1

        # Add new models
        added_count = 0
        for model in delta["added"]:
            model_id = model.get("id") or model.get("slug") or model.get("provider_model_id")
            if model_id:
                model_key = f"{provider_name}:{model_id}"
                if cache.set_model(model_key, model, ttl=ttl):
                    added_count += 1

        # Remove deleted models
        deleted_count = 0
        for model_id in delta["deleted"]:
            model_key = f"{provider_name}:{model_id}"
            if cache.invalidate_model(model_key):
                deleted_count += 1

        # Update index
        if cache.redis_client and is_redis_available():
            model_ids = [
                m.get("id") or m.get("slug") or m.get("provider_model_id")
                for m in new_models
                if m.get("id") or m.get("slug") or m.get("provider_model_id")
            ]
            cache.redis_client.setex(
                f"{CACHE_NAMESPACE}models:index:{provider_name}",
                ttl,
                _serialize(model_ids)
            )

        logger.info(
            f"Incremental cache update: {provider_name} | "
            f"Changed: {updated_count}, Added: {added_count}, Deleted: {deleted_count}, "
            f"Unchanged: {delta['unchanged']} (skipped) | "
            f"Efficiency: {delta['unchanged']}/{delta['total_cached']} models skipped "
            f"({round(delta['unchanged'] / max(delta['total_cached'], 1) * 100, 1)}%)"
        )

        return {
            "success": True,
            "provider": provider_name,
            "changed": updated_count,
            "added": added_count,
            "deleted": deleted_count,
            "unchanged": delta["unchanged"],
            "total_operations": updated_count + added_count + deleted_count,
            "efficiency_percent": round(delta["unchanged"] / max(delta["total_cached"], 1) * 100, 1)
        }

    except Exception as e:
        logger.error(f"Error in incremental cache update for {provider_name}: {e}")
        return {
            "success": False,
            "provider": provider_name,
            "error": str(e)
        }


# ============================================================================
# Background Refresh with TTL Checking (Phase 3)
# ============================================================================


def get_provider_catalog_with_refresh(
    provider_name: str,
    ttl_threshold: int = CATALOG_RESPONSE_CACHE_TTL  # 5 minutes
) -> list[dict[str, Any]] | None:
    """
    Get provider catalog with smart background refresh.

    Implements stale-while-revalidate pattern:
    - Returns cached data immediately (fast response)
    - Triggers background refresh if TTL is low
    - Never blocks the request waiting for refresh

    This ensures:
    - Zero cache misses (always returns data)
    - Fast responses (1-5ms even when refreshing)
    - Fresh data (background refresh keeps cache warm)

    Args:
        provider_name: Provider slug
        ttl_threshold: Trigger refresh when TTL < this many seconds (default: 5 min)

    Returns:
        Cached models (may be slightly stale if refresh in progress)
    """
    import asyncio

    cache = get_model_catalog_cache()

    try:
        # Get current cache
        cached_models = get_provider_catalog_smart(provider_name)

        # Check TTL if we have Redis
        if cache.redis_client and is_redis_available():
            index_key = f"{CACHE_NAMESPACE}models:index:{provider_name}"
            ttl = cache.redis_client.ttl(index_key)

            # If TTL is low (< threshold), trigger background refresh
            if ttl and 0 < ttl < ttl_threshold:
                logger.debug(
                    f"TTL low for {provider_name} ({ttl}s < {ttl_threshold}s), "
                    f"triggering background refresh"
                )

                # Fire-and-forget background refresh
                asyncio.create_task(_refresh_provider_catalog_background(provider_name))

        # Return current cache immediately (don't wait for refresh)
        return cached_models

    except Exception as e:
        logger.error(f"Error in get_provider_catalog_with_refresh for {provider_name}: {e}")
        return None


async def _refresh_provider_catalog_background(provider_name: str):
    """
    Background task to refresh provider catalog from database.

    Runs asynchronously without blocking the request that triggered it.
    Updates cache with fresh data from database.

    Args:
        provider_name: Provider slug to refresh
    """
    try:
        logger.info(f"Background refresh started for {provider_name}")

        # Fetch fresh data from database (in thread pool to avoid blocking)
        fresh_models = await asyncio.to_thread(_fetch_provider_models_from_db, provider_name)

        if fresh_models:
            # Update cache incrementally (only update what changed)
            result = update_provider_catalog_incremental(provider_name, fresh_models)

            logger.info(
                f"Background refresh completed for {provider_name}: "
                f"{result.get('changed', 0)} changed, {result.get('added', 0)} added, "
                f"{result.get('deleted', 0)} deleted"
            )
        else:
            logger.warning(f"Background refresh got no data for {provider_name}")

    except Exception as e:
        logger.error(f"Background refresh failed for {provider_name}: {e}")


def _fetch_provider_models_from_db(provider_name: str) -> list[dict[str, Any]]:
    """
    Fetch provider models from database (blocking operation).

    Helper function for background refresh.
    Called in thread pool to avoid blocking async event loop.

    Args:
        provider_name: Provider slug

    Returns:
        List of models from database
    """
    try:
        from src.db.models_catalog_db import (
            get_models_by_gateway_for_catalog,
            transform_db_models_batch,
        )

        # Fetch from database
        db_models = get_models_by_gateway_for_catalog(
            gateway_slug=provider_name,
            include_inactive=False
        )

        # Transform to API format
        api_models = transform_db_models_batch(db_models)

        return api_models

    except Exception as e:
        logger.error(f"Error fetching {provider_name} from database: {e}")
        return []


# ============================================================================
# Unique Models Caching (Phase 4 - Deduplicated Cross-Provider View)
# ============================================================================


def cache_unique_model(
    model_name: str,
    model_data: dict[str, Any],
    ttl: int | None = None
) -> bool:
    """
    Cache a single unique model with its provider relationships.

    Args:
        model_name: Cleaned model name (e.g., "GPT-4")
        model_data: Model data including providers array
        ttl: Time to live in seconds (default: TTL_UNIQUE)

    Returns:
        True if cached successfully, False otherwise
    """
    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_UNIQUE

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}models:unique:{model_name}"
        cache.redis_client.setex(key, ttl, _serialize(model_data))
        cache._stats["sets"] += 1
        logger.debug(f"Cached unique model: {model_name} (TTL: {ttl}s)")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error caching unique model {model_name}: {e}")
        return False


def get_cached_unique_model(model_name: str) -> dict[str, Any] | None:
    """
    Get a single cached unique model.

    Args:
        model_name: Cleaned model name (e.g., "GPT-4")

    Returns:
        Model data dict or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return None

        key = f"{CACHE_NAMESPACE}models:unique:{model_name}"
        cached_data = cache.redis_client.get(key)

        if cached_data:
            cache._stats["hits"] += 1
            logger.debug(f"Cache HIT: unique model {model_name}")
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            logger.debug(f"Cache MISS: unique model {model_name}")
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error getting unique model {model_name}: {e}")
        return None


def update_unique_models_incremental(
    new_unique_models: list[dict[str, Any]],
    ttl: int | None = None
) -> dict[str, Any]:
    """
    Update unique models cache incrementally - only update what changed.

    This is the smart caching for deduplicated models:
    1. Fetches current cache
    2. Detects which unique models changed
    3. Only updates changed/added unique models
    4. Removes deleted unique models
    5. Skips unchanged models entirely

    Args:
        new_unique_models: New unique models list from database
        ttl: Time to live in seconds

    Returns:
        dict with operation stats
    """
    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_UNIQUE

    try:
        # Get current cached index
        cached_index = []
        if cache.redis_client and is_redis_available():
            index_data = cache.redis_client.get(f"{CACHE_NAMESPACE}models:unique:index")
            if index_data:
                cached_index = _deserialize(index_data)

        # Build lookup for cached models
        cached_by_name = {}
        for model_name in cached_index:
            cached_model = get_cached_unique_model(model_name)
            if cached_model:
                cached_by_name[model_name] = cached_model

        # Build lookup for new models
        new_by_name = {}
        for model in new_unique_models:
            model_name = model.get("model_name") or model.get("name") or model.get("id")
            if model_name:
                new_by_name[model_name] = model

        # Find delta
        changed = []
        added = []
        unchanged_count = 0

        for model_name, new_model in new_by_name.items():
            if model_name in cached_by_name:
                # Check if changed
                if has_unique_model_changed(cached_by_name[model_name], new_model):
                    changed.append(new_model)
                else:
                    unchanged_count += 1
            else:
                # New unique model
                added.append(new_model)

        # Find deleted
        deleted_names = [
            name for name in cached_by_name.keys()
            if name not in new_by_name
        ]

        # Update changed models
        updated_count = 0
        for model in changed:
            model_name = model.get("model_name") or model.get("name") or model.get("id")
            if model_name and cache_unique_model(model_name, model, ttl):
                updated_count += 1

        # Add new models
        added_count = 0
        for model in added:
            model_name = model.get("model_name") or model.get("name") or model.get("id")
            if model_name and cache_unique_model(model_name, model, ttl):
                added_count += 1

        # Remove deleted models
        deleted_count = 0
        for model_name in deleted_names:
            if invalidate_unique_model(model_name):
                deleted_count += 1

        # Update index
        if cache.redis_client and is_redis_available():
            new_index = list(new_by_name.keys())
            cache.redis_client.setex(
                f"{CACHE_NAMESPACE}models:unique:index",
                ttl,
                _serialize(new_index)
            )

        logger.info(
            f"Incremental unique models update | "
            f"Changed: {updated_count}, Added: {added_count}, Deleted: {deleted_count}, "
            f"Unchanged: {unchanged_count} (skipped) | "
            f"Efficiency: {round(unchanged_count / max(len(cached_by_name), 1) * 100, 1)}%"
        )

        return {
            "success": True,
            "changed": updated_count,
            "added": added_count,
            "deleted": deleted_count,
            "unchanged": unchanged_count,
            "total_operations": updated_count + added_count + deleted_count,
            "efficiency_percent": round(unchanged_count / max(len(cached_by_name), 1) * 100, 1)
        }

    except Exception as e:
        logger.error(f"Error in incremental unique models update: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def has_unique_model_changed(old_model: dict[str, Any], new_model: dict[str, Any]) -> bool:
    """
    Detect if a unique model has actually changed.

    Compares the model and its provider relationships to determine if update is needed.

    Args:
        old_model: Previously cached unique model
        new_model: Newly fetched unique model from database

    Returns:
        True if model changed, False otherwise
    """
    if not old_model or not new_model:
        return True

    # Compare model count
    if old_model.get("model_count") != new_model.get("model_count"):
        return True

    # Compare provider array
    old_providers = old_model.get("providers", [])
    new_providers = new_model.get("providers", [])

    if len(old_providers) != len(new_providers):
        return True

    # Build provider lookups by provider_id
    old_by_id = {p.get("provider_id"): p for p in old_providers}
    new_by_id = {p.get("provider_id"): p for p in new_providers}

    # Check if provider set changed
    if set(old_by_id.keys()) != set(new_by_id.keys()):
        return True

    # Check if any provider pricing changed
    for provider_id, new_provider in new_by_id.items():
        old_provider = old_by_id.get(provider_id)
        if not old_provider:
            continue

        # Compare pricing
        old_pricing = old_provider.get("pricing", {})
        new_pricing = new_provider.get("pricing", {})

        if str(old_pricing) != str(new_pricing):
            return True

    # No changes detected
    return False


def invalidate_unique_model(model_name: str) -> bool:
    """
    Invalidate a single cached unique model.

    Args:
        model_name: Cleaned model name to invalidate

    Returns:
        True if invalidated successfully
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}models:unique:{model_name}"
        cache.redis_client.delete(key)
        cache._stats["invalidations"] += 1
        logger.debug(f"Invalidated unique model: {model_name}")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error invalidating unique model {model_name}: {e}")
        return False


# ============================================================================
# Relationship Caching (Phase 5 - Provider-Model Mappings)
# ============================================================================


def cache_model_relationships_by_unique(
    model_name: str,
    relationship_data: dict[str, Any],
    ttl: int | None = None
) -> bool:
    """
    Cache provider relationships for a unique model.

    Stores "which providers offer this model" data.

    Args:
        model_name: Unique model name
        relationship_data: Relationship data with provider mappings
        ttl: Time to live in seconds

    Returns:
        True if cached successfully
    """
    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_UNIQUE

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}models:relationships:unique:{model_name}"
        cache.redis_client.setex(key, ttl, _serialize(relationship_data))
        cache._stats["sets"] += 1
        logger.debug(f"Cached relationships for unique model: {model_name}")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error caching relationships for {model_name}: {e}")
        return False


def get_cached_model_relationships_by_unique(model_name: str) -> dict[str, Any] | None:
    """
    Get cached provider relationships for a unique model.

    Args:
        model_name: Unique model name

    Returns:
        Relationship data or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return None

        key = f"{CACHE_NAMESPACE}models:relationships:unique:{model_name}"
        cached_data = cache.redis_client.get(key)

        if cached_data:
            cache._stats["hits"] += 1
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error getting relationships for {model_name}: {e}")
        return None


def cache_model_relationships_by_provider(
    provider_slug: str,
    relationship_data: dict[str, Any],
    ttl: int | None = None
) -> bool:
    """
    Cache unique model relationships for a provider.

    Stores "which unique models this provider offers" data.

    Args:
        provider_slug: Provider slug
        relationship_data: Relationship data with unique models
        ttl: Time to live in seconds

    Returns:
        True if cached successfully
    """
    cache = get_model_catalog_cache()
    ttl = ttl or cache.TTL_PROVIDER

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}models:relationships:provider:{provider_slug}"
        cache.redis_client.setex(key, ttl, _serialize(relationship_data))
        cache._stats["sets"] += 1
        logger.debug(f"Cached relationships for provider: {provider_slug}")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error caching relationships for provider {provider_slug}: {e}")
        return False


def get_cached_model_relationships_by_provider(provider_slug: str) -> dict[str, Any] | None:
    """
    Get cached unique model relationships for a provider.

    Args:
        provider_slug: Provider slug

    Returns:
        Relationship data or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return None

        key = f"{CACHE_NAMESPACE}models:relationships:provider:{provider_slug}"
        cached_data = cache.redis_client.get(key)

        if cached_data:
            cache._stats["hits"] += 1
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error getting relationships for provider {provider_slug}: {e}")
        return None


# ============================================================================
# Provider Metadata Caching (Phase 6)
# ============================================================================


def cache_provider_metadata(
    provider_id: int,
    provider_data: dict[str, Any],
    ttl: int = 3600
) -> bool:
    """
    Cache provider metadata.

    Args:
        provider_id: Provider ID
        provider_data: Provider metadata
        ttl: Time to live (default: 1 hour)

    Returns:
        True if cached successfully
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}providers:provider:{provider_id}"
        cache.redis_client.setex(key, ttl, _serialize(provider_data))
        cache._stats["sets"] += 1
        logger.debug(f"Cached provider metadata: {provider_id}")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error caching provider metadata {provider_id}: {e}")
        return False


def get_cached_provider_metadata(provider_id: int) -> dict[str, Any] | None:
    """
    Get cached provider metadata.

    Args:
        provider_id: Provider ID

    Returns:
        Provider data or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return None

        key = f"{CACHE_NAMESPACE}providers:provider:{provider_id}"
        cached_data = cache.redis_client.get(key)

        if cached_data:
            cache._stats["hits"] += 1
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error getting provider metadata {provider_id}: {e}")
        return None


def cache_providers_index(provider_ids: list[int], ttl: int = 3600) -> bool:
    """
    Cache list of all provider IDs.

    Args:
        provider_ids: List of provider IDs
        ttl: Time to live (default: 1 hour)

    Returns:
        True if cached successfully
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return False

        key = f"{CACHE_NAMESPACE}providers:index"
        cache.redis_client.setex(key, ttl, _serialize(provider_ids))
        cache._stats["sets"] += 1
        logger.debug(f"Cached providers index: {len(provider_ids)} providers")
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error caching providers index: {e}")
        return False


def get_cached_providers_index() -> list[int] | None:
    """
    Get cached list of all provider IDs.

    Returns:
        List of provider IDs or None if not found
    """
    cache = get_model_catalog_cache()

    try:
        if not cache.redis_client or not is_redis_available():
            return None

        key = f"{CACHE_NAMESPACE}providers:index"
        cached_data = cache.redis_client.get(key)

        if cached_data:
            cache._stats["hits"] += 1
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Error getting providers index: {e}")
        return None


# ============================================================================
# Smart Unique Models Caching with Filter Support (NEW)
# ============================================================================


def _generate_unique_models_cache_key(
    include_inactive: bool = False,
    min_providers: int | None = None,
    sort_by: str = "provider_count",
    order: str = "desc"
) -> str:
    """
    Generate cache key for unique models with specific filters.

    Args:
        include_inactive: Include inactive models
        min_providers: Minimum provider count filter
        sort_by: Sort field
        order: Sort order (asc/desc)

    Returns:
        Cache key string
    """
    key_parts = [f"{CACHE_NAMESPACE}models:unique:filtered"]

    if include_inactive:
        key_parts.append("inactive")

    if min_providers is not None:
        key_parts.append(f"minp{min_providers}")

    key_parts.append(f"sort{sort_by}")
    key_parts.append(order)

    return ":".join(key_parts)


async def get_cached_unique_models_smart(
    include_inactive: bool = False,
    min_providers: int | None = None,
    sort_by: str = "provider_count",
    order: str = "desc"
) -> list[dict[str, Any]] | None:
    """
    Get cached unique models with filter support.

    Supports caching different filter/sort combinations separately.
    Falls back to asyncio.to_thread for non-blocking Redis access.

    Args:
        include_inactive: Include inactive models
        min_providers: Minimum provider count filter
        sort_by: Sort by field (provider_count, name, cheapest_price)
        order: Sort order (asc, desc)

    Returns:
        Cached models list or None if not found
    """
    import asyncio

    cache = get_model_catalog_cache()

    if not cache.redis_client or not is_redis_available():
        return None

    try:
        key = _generate_unique_models_cache_key(
            include_inactive=include_inactive,
            min_providers=min_providers,
            sort_by=sort_by,
            order=order
        )

        # Non-blocking Redis access
        cached_data = await asyncio.to_thread(cache.redis_client.get, key)

        if cached_data:
            cache._stats["hits"] += 1
            logger.debug(f"Cache HIT: Unique models with filters (key: {key})")
            return _deserialize(cached_data)
        else:
            cache._stats["misses"] += 1
            logger.debug(f"Cache MISS: Unique models with filters (key: {key})")
            return None

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Cache GET error for unique models with filters: {e}")
        return None


async def cache_unique_models_with_filters(
    models: list[dict[str, Any]],
    include_inactive: bool = False,
    min_providers: int | None = None,
    sort_by: str = "provider_count",
    order: str = "desc",
    ttl: int = PROVIDER_MODELS_CACHE_TTL
) -> bool:
    """
    Cache unique models with specific filter combination.

    Args:
        models: List of models to cache
        include_inactive: Include inactive models
        min_providers: Minimum provider count filter
        sort_by: Sort by field
        order: Sort order
        ttl: Time to live (default: 30 minutes)

    Returns:
        True if cached successfully
    """
    import asyncio

    cache = get_model_catalog_cache()

    if not cache.redis_client or not is_redis_available():
        return False

    try:
        key = _generate_unique_models_cache_key(
            include_inactive=include_inactive,
            min_providers=min_providers,
            sort_by=sort_by,
            order=order
        )

        serialized_data = _serialize(models)

        # Non-blocking Redis access
        await asyncio.to_thread(cache.redis_client.setex, key, ttl, serialized_data)

        cache._stats["sets"] += 1
        logger.info(
            f"Cache SET: Unique models with filters ({len(models)} models, "
            f"key: {key}, TTL: {ttl}s)"
        )
        return True

    except redis.RedisError as e:
        cache._stats["errors"] += 1
        logger.warning(f"Cache SET error for unique models with filters: {e}")
        return False


async def warm_unique_models_cache_all_variants() -> dict[str, Any]:
    """
    Warm cache for all common unique models filter combinations.

    Pre-populates cache with the most frequently requested variants:
    - Default view (active, all providers, sorted by provider_count desc)
    - Common min_providers filters (2+, 3+, 5+)
    - Different sort options (name, cheapest_price)

    This dramatically reduces database load for common queries.

    Returns:
        dict with warming statistics
    """
    from src.db.models_catalog_db import (
        get_all_unique_models_for_catalog,
        transform_unique_models_batch,
    )
    import asyncio

    logger.info("Starting unique models cache warming for common variants...")

    # Define common filter combinations to pre-warm
    variants = [
        # Default view
        {"include_inactive": False, "min_providers": None, "sort_by": "provider_count", "order": "desc"},
        # Multi-provider models
        {"include_inactive": False, "min_providers": 2, "sort_by": "provider_count", "order": "desc"},
        {"include_inactive": False, "min_providers": 3, "sort_by": "provider_count", "order": "desc"},
        {"include_inactive": False, "min_providers": 5, "sort_by": "provider_count", "order": "desc"},
        # Alphabetical
        {"include_inactive": False, "min_providers": None, "sort_by": "name", "order": "asc"},
        # Cheapest
        {"include_inactive": False, "min_providers": None, "sort_by": "cheapest_price", "order": "asc"},
    ]

    stats = {
        "total_variants": len(variants),
        "successful": 0,
        "failed": 0,
        "variants_cached": []
    }

    try:
        # Fetch base data once
        logger.debug("Fetching base unique models data from database...")
        db_unique_models = await asyncio.to_thread(
            get_all_unique_models_for_catalog,
            include_inactive=False
        )

        api_models = await asyncio.to_thread(
            transform_unique_models_batch,
            db_unique_models
        )

        logger.info(f"Fetched {len(api_models)} unique models from database")

        # Cache each variant
        for variant in variants:
            try:
                filtered_models = list(api_models)  # Copy

                # Apply filter
                if variant["min_providers"] is not None:
                    filtered_models = [
                        m for m in filtered_models
                        if m.get("provider_count", 0) >= variant["min_providers"]
                    ]

                # Apply sort
                if variant["sort_by"] == "provider_count":
                    filtered_models.sort(
                        key=lambda m: m.get("provider_count", 0),
                        reverse=(variant["order"] == "desc")
                    )
                elif variant["sort_by"] == "name":
                    filtered_models.sort(
                        key=lambda m: m.get("name", "").lower(),
                        reverse=(variant["order"] == "desc")
                    )
                elif variant["sort_by"] == "cheapest_price":
                    filtered_models.sort(
                        key=lambda m: m.get("cheapest_prompt_price") if m.get("cheapest_prompt_price") is not None else float('inf'),
                        reverse=(variant["order"] == "desc")
                    )

                # Cache this variant
                success = await cache_unique_models_with_filters(
                    models=filtered_models,
                    **variant,
                    ttl=PROVIDER_MODELS_CACHE_TTL
                )

                if success:
                    stats["successful"] += 1
                    stats["variants_cached"].append({
                        "variant": variant,
                        "count": len(filtered_models)
                    })
                else:
                    stats["failed"] += 1

            except Exception as e:
                logger.error(f"Error warming variant {variant}: {e}")
                stats["failed"] += 1

        logger.info(
            f"Unique models cache warming complete: "
            f"{stats['successful']}/{stats['total_variants']} variants cached"
        )

        return stats

    except Exception as e:
        logger.error(f"Error in unique models cache warming: {e}")
        return stats


# ============================================================================
# Cache Invalidation Helper (CA-L3: Supabase model data change invalidation)
# ============================================================================


def invalidate_catalog_caches(gateway: str = None) -> dict[str, Any]:
    """Invalidate catalog caches, optionally for a specific gateway.

    Scans and deletes Redis keys in two layers:
    - L1 (response cache): full catalog, stats, unique models, filtered unique
      model variants, and (when gateway is given) the specific provider/gateway
      response key.
    - L2 (model cache): per-model metadata, pricing, index, and relationship
      keys scoped to the gateway (when given) or all model keys otherwise.

    Uses SCAN for non-blocking key discovery so it does not stall the Redis
    server on large keyspaces.

    Args:
        gateway: Optional gateway/provider slug (e.g. "openrouter").
                 When supplied only keys belonging to that gateway are removed
                 in addition to the shared L1 response keys.
                 When None, all catalog-related keys are removed.

    Returns:
        dict with:
        - success: bool
        - keys_deleted: int (total keys removed)
        - gateway: str or None (the gateway argument)
        - error: str (only present on failure)
    """
    cache = get_model_catalog_cache()

    if not cache.redis_client or not is_redis_available():
        logger.warning(
            "Cache invalidation skipped: Redis unavailable "
            f"(gateway={gateway!r})"
        )
        return {"success": False, "keys_deleted": 0, "gateway": gateway, "error": "Redis unavailable"}

    total_deleted = 0

    try:
        # ------------------------------------------------------------------
        # L1 — shared response-level keys (always cleared regardless of scope)
        # ------------------------------------------------------------------
        l1_exact_keys = [
            ModelCatalogCache.PREFIX_FULL_CATALOG,  # gw:models:catalog:full
            ModelCatalogCache.PREFIX_STATS,          # gw:models:stats
            ModelCatalogCache.PREFIX_UNIQUE,         # gw:models:unique
        ]
        for key in l1_exact_keys:
            deleted = cache.redis_client.delete(key)
            total_deleted += deleted

        # gw:models:unique:filtered:* — covers all filter/sort variant keys
        for pattern in [f"{CACHE_NAMESPACE}models:unique:filtered:*"]:
            cursor = 0
            while True:
                cursor, keys = cache.redis_client.scan(cursor, match=pattern, count=100)
                if keys:
                    total_deleted += cache.redis_client.delete(*keys)
                if cursor == 0:
                    break

        # ------------------------------------------------------------------
        # L2 — model-level keys, scoped by gateway when provided
        # ------------------------------------------------------------------
        if gateway:
            # Gateway-specific L1 response key
            gw_l1_key = f"{ModelCatalogCache.PREFIX_PROVIDER}:{gateway}"
            total_deleted += cache.redis_client.delete(gw_l1_key)

            # Gateway-specific L2 model/index/relationship keys
            l2_gateway_patterns = [
                f"{CACHE_NAMESPACE}models:model:{gateway}:*",
                f"{CACHE_NAMESPACE}models:pricing:{gateway}:*",
                f"{CACHE_NAMESPACE}models:index:{gateway}",
                f"{CACHE_NAMESPACE}models:relationships:provider:{gateway}",
            ]
            for pattern in l2_gateway_patterns:
                cursor = 0
                while True:
                    cursor, keys = cache.redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        total_deleted += cache.redis_client.delete(*keys)
                    if cursor == 0:
                        break

            logger.info(
                f"Cache invalidated for gateway '{gateway}': "
                f"{total_deleted} keys deleted (L1 shared + L2 gateway-scoped)"
            )
        else:
            # Full invalidation — remove all gw:models:* and gw:providers:* keys
            for pattern in [f"{CACHE_NAMESPACE}models:*", f"{CACHE_NAMESPACE}providers:*"]:
                cursor = 0
                while True:
                    cursor, keys = cache.redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        total_deleted += cache.redis_client.delete(*keys)
                    if cursor == 0:
                        break

            logger.info(
                f"Full catalog cache invalidated: {total_deleted} keys deleted"
            )

        return {"success": True, "keys_deleted": total_deleted, "gateway": gateway}

    except Exception as e:
        logger.error(f"Cache invalidation error (gateway={gateway!r}): {e}", exc_info=True)
        return {"success": False, "keys_deleted": total_deleted, "gateway": gateway, "error": str(e)}


__all__ = [
    # Core cache class and instance accessor
    "ModelCatalogCache",
    "get_model_catalog_cache",
    # Full catalog
    "cache_full_catalog",
    "get_cached_full_catalog",
    "rebuild_full_catalog_from_providers",
    "invalidate_full_catalog",
    # Provider catalog
    "cache_provider_catalog",
    "get_cached_provider_catalog",
    "invalidate_provider_catalog",
    # Gateway catalog (alias)
    "cache_gateway_catalog",
    "get_cached_gateway_catalog",
    "invalidate_gateway_catalog",
    # Individual models
    "get_cached_unique_models",
    "cache_unique_models",
    "invalidate_unique_models",
    "cache_unique_model",
    "get_cached_unique_model",
    "invalidate_unique_model",
    "update_unique_models_incremental",
    # Catalog stats
    "get_cached_catalog_stats",
    "cache_catalog_stats",
    "invalidate_catalog_stats",
    # Smart / incremental caching
    "set_provider_catalog_smart",
    "get_provider_catalog_smart",
    "update_provider_catalog_incremental",
    "get_provider_catalog_with_refresh",
    # Unique models with filter support
    "get_cached_unique_models_smart",
    "cache_unique_models_with_filters",
    "warm_unique_models_cache_all_variants",
    # Relationships
    "cache_model_relationships_by_unique",
    "get_cached_model_relationships_by_unique",
    "cache_model_relationships_by_provider",
    "get_cached_model_relationships_by_provider",
    # Provider metadata
    "cache_provider_metadata",
    "get_cached_provider_metadata",
    "cache_providers_index",
    "get_cached_providers_index",
    # Change detection
    "has_model_changed",
    "find_changed_models",
    "has_unique_model_changed",
    # Stats and bulk clear
    "get_catalog_cache_stats",
    "clear_all_model_caches",
    # Backward-compat wrappers
    "get_gateway_cache_metadata",
    "get_provider_cache_metadata",
    "clear_models_cache",
    "clear_providers_cache",
    # Cache invalidation helper (CA-L3)
    "invalidate_catalog_caches",
]
