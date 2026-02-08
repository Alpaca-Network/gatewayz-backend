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

import json
import logging
import threading
from typing import Any

from src.config.redis_config import get_redis_client, is_redis_available

logger = logging.getLogger(__name__)


class ModelCatalogCache:
    """High-performance model catalog caching with Redis backend"""

    # Cache key prefixes
    PREFIX_FULL_CATALOG = "models:catalog:full"
    PREFIX_PROVIDER = "models:provider"
    PREFIX_MODEL = "models:model"
    PREFIX_PRICING = "models:pricing"
    PREFIX_GATEWAY = "models:gateway"
    PREFIX_STATS = "models:stats"
    PREFIX_UNIQUE = "models:unique"

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

    def _generate_key(self, prefix: str, identifier: str = "") -> str:
        """Generate cache key with prefix and optional identifier"""
        if identifier:
            return f"{prefix}:{identifier}"
        return prefix

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
                logger.debug("Cache HIT: Full model catalog")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug("Cache MISS: Full model catalog")
                return None

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache GET error for full catalog: {e}")
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
            serialized_data = json.dumps(catalog)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.info(f"Cache SET: Full model catalog ({len(catalog)} models, TTL: {ttl}s)")
            return True

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for full catalog: {e}")
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

        except Exception as e:
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
                logger.debug(f"Cache HIT: Provider catalog for {provider_name}")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug(f"Cache MISS: Provider catalog for {provider_name}")
                return None

        except Exception as e:
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
            serialized_data = json.dumps(catalog)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(
                f"Cache SET: Provider catalog for {provider_name} "
                f"({len(catalog)} models, TTL: {ttl}s)"
            )
            return True

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for provider {provider_name}: {e}")
            return False

    def invalidate_provider_catalog(self, provider_name: str) -> bool:
        """Invalidate cached catalog for a specific provider.

        Args:
            provider_name: Provider name

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not is_redis_available():
            return False

        key = self._generate_key(self.PREFIX_PROVIDER, provider_name)

        try:
            self.redis_client.delete(key)
            self._stats["invalidations"] += 1
            # Also invalidate full catalog since it depends on provider catalogs
            self.invalidate_full_catalog()
            logger.info(f"Cache INVALIDATE: Provider catalog for {provider_name}")
            return True

        except Exception as e:
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
                logger.debug(f"Cache HIT: Model {model_id}")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug(f"Cache MISS: Model {model_id}")
                return None

        except Exception as e:
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
            serialized_data = json.dumps(model_data)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Model {model_id} (TTL: {ttl}s)")
            return True

        except Exception as e:
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

        except Exception as e:
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
                logger.debug(f"Cache HIT: Pricing for {model_id}")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug(f"Cache MISS: Pricing for {model_id}")
                return None

        except Exception as e:
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
            serialized_data = json.dumps(pricing_data)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Pricing for {model_id} (TTL: {ttl}s)")
            return True

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache SET error for pricing {model_id}: {e}")
            return False

    # Batch Operations

    def invalidate_all_models(self) -> int:
        """Invalidate all cached model data.

        Returns:
            Number of keys deleted
        """
        if not self.redis_client or not is_redis_available():
            return 0

        try:
            total_deleted = 0

            # Invalidate all model caches
            for prefix in [self.PREFIX_MODEL, self.PREFIX_PRICING, self.PREFIX_PROVIDER]:
                pattern = f"{prefix}:*"
                keys = self.redis_client.keys(pattern)
                if keys:
                    deleted = self.redis_client.delete(*keys)
                    total_deleted += deleted

            # Invalidate full catalog
            self.redis_client.delete(self.PREFIX_FULL_CATALOG)
            total_deleted += 1

            self._stats["invalidations"] += total_deleted
            logger.warning(f"Cache INVALIDATE ALL: {total_deleted} model cache keys deleted")
            return total_deleted

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE ALL error: {e}")
            return 0

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
                stats["provider_catalogs_count"] = len(
                    self.redis_client.keys(f"{self.PREFIX_PROVIDER}:*")
                )
                stats["models_cached_count"] = len(self.redis_client.keys(f"{self.PREFIX_MODEL}:*"))
                stats["pricing_cached_count"] = len(
                    self.redis_client.keys(f"{self.PREFIX_PRICING}:*")
                )
            except Exception as e:
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

    def invalidate_gateway_catalog(self, gateway_name: str) -> bool:
        """Invalidate cached catalog for a specific gateway.

        This is an alias for invalidate_provider_catalog to support consistent naming.

        Args:
            gateway_name: Gateway name

        Returns:
            True if successful, False otherwise
        """
        return self.invalidate_provider_catalog(gateway_name)

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
                logger.debug("Cache HIT: Catalog statistics")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug("Cache MISS: Catalog statistics")
                return None

        except Exception as e:
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
            serialized_data = json.dumps(stats)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Catalog statistics (TTL: {ttl}s)")
            return True

        except Exception as e:
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

        except Exception as e:
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
                logger.debug("Cache HIT: Unique models")
                return json.loads(cached_data)
            else:
                self._stats["misses"] += 1
                logger.debug("Cache MISS: Unique models")
                return None

        except Exception as e:
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
            serialized_data = json.dumps(unique_models)
            self.redis_client.setex(key, ttl, serialized_data)
            self._stats["sets"] += 1
            logger.debug(f"Cache SET: Unique models ({len(unique_models)} models, TTL: {ttl}s)")
            return True

        except Exception as e:
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

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"Cache INVALIDATE error for unique models: {e}")
            return False


# Global cache instance
_model_catalog_cache: ModelCatalogCache | None = None

# Stampede protection locks — prevent multiple threads from rebuilding cache simultaneously
# after invalidation. Only one thread fetches from DB while others wait for the result.
_rebuild_lock_full_catalog = threading.Lock()
_rebuild_lock_unique_models = threading.Lock()
_rebuild_locks_provider: dict[str, threading.Lock] = {}


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

    step_logger = StepLogger("Cache: Fetch Full Catalog", total_steps=5)
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

        step_logger.step(3, "Fetching from database", cache_layer="database")
        try:
            from src.db.models_catalog_db import (
                get_all_models_for_catalog,
                transform_db_models_batch,
            )

            db_models = get_all_models_for_catalog(include_inactive=False)
            step_logger.success(db_models=len(db_models))

            # Step 4: Transform to API format
            step_logger.step(4, "Transforming models to API format", count=len(db_models))
            api_models = transform_db_models_batch(db_models)
            step_logger.success(api_models=len(api_models))

            # Step 5: Populate caches
            step_logger.step(5, "Populating caches", targets="redis+local")
            cache.set_full_catalog(api_models, ttl=900)
            set_local_catalog("all", api_models)
            step_logger.success(redis="updated", local="updated", ttl=900)

            step_logger.complete(source="database", models=len(api_models), cache_status="populated")
            return api_models

        except Exception as e:
            step_logger.failure(e, source="database")
            logger.error(f"Error fetching catalog from database: {e}")
            return []


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
                cache.set_provider_catalog(provider_name, fresh_data, ttl=1800)
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

    lock = _rebuild_locks_provider.setdefault(provider_name, threading.Lock())
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

            # Fetch from database
            db_models = get_models_by_gateway_for_catalog(
                gateway_slug=provider_name,
                include_inactive=False
            )

            # Transform to API format
            api_models = transform_db_models_batch(db_models)

            # Cache in both Redis and local memory
            cache.set_provider_catalog(provider_name, api_models, ttl=1800)
            set_local_catalog(provider_name, api_models)

            logger.info(f"Fetched {len(api_models)} models for {provider_name} from database and cached")

            return api_models

        except Exception as e:
            logger.error(f"Error fetching {provider_name} catalog from database: {e}")
            return []


def invalidate_provider_catalog(provider_name: str) -> bool:
    """Invalidate cached provider catalog"""
    cache = get_model_catalog_cache()
    return cache.invalidate_provider_catalog(provider_name)


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
                cache.set_gateway_catalog(gateway_name, fresh_data, ttl=1800)
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

    lock = _rebuild_locks_provider.setdefault(gateway_name, threading.Lock())
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

            # Fetch from database
            db_models = get_models_by_gateway_for_catalog(
                gateway_slug=gateway_name,
                include_inactive=False
            )

            # Transform to API format
            api_models = transform_db_models_batch(db_models)

            # Cache in both Redis and local memory
            cache.set_gateway_catalog(gateway_name, api_models, ttl=1800)
            set_local_catalog(gateway_name, api_models)

            logger.info(f"Fetched {len(api_models)} models for {gateway_name} from database and cached")

            return api_models

        except Exception as e:
            logger.error(f"Error fetching {gateway_name} catalog from database: {e}")
            return []


def invalidate_gateway_catalog(gateway_name: str) -> bool:
    """Invalidate cached gateway catalog"""
    cache = get_model_catalog_cache()
    return cache.invalidate_gateway_catalog(gateway_name)


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
            cache.set_unique_models(api_models, ttl=1800)
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
