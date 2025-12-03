"""
Cache Invalidation Service

Manages cache invalidation strategies and refresh patterns for health data
to ensure data freshness while maintaining performance.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from src.redis_config import get_redis_client
from src.services.health_cache_service import (
    health_cache_service,
    CACHE_PREFIX_DASHBOARD,
    CACHE_PREFIX_GATEWAY,
    CACHE_PREFIX_MODELS,
    CACHE_PREFIX_PROVIDERS,
    CACHE_PREFIX_SUMMARY,
    CACHE_PREFIX_SYSTEM,
)

logger = logging.getLogger(__name__)


class CacheInvalidationService:
    """Service for managing cache invalidation and refresh strategies"""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.invalidation_callbacks: dict[str, list[Callable]] = {}
        self.refresh_tasks: dict[str, asyncio.Task] = {}

    def register_invalidation_callback(self, cache_key: str, callback: Callable) -> None:
        """Register a callback to be called when cache is invalidated"""
        if cache_key not in self.invalidation_callbacks:
            self.invalidation_callbacks[cache_key] = []
        self.invalidation_callbacks[cache_key].append(callback)
        logger.debug(f"Registered invalidation callback for {cache_key}")

    async def trigger_invalidation(self, cache_key: str) -> None:
        """Trigger invalidation callbacks for a cache key"""
        if cache_key in self.invalidation_callbacks:
            for callback in self.invalidation_callbacks[cache_key]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback()
                    else:
                        callback()
                except Exception as e:
                    logger.error(f"Error in invalidation callback: {e}")

    def invalidate_cache(self, cache_key: str) -> bool:
        """Invalidate a specific cache entry"""
        try:
            health_cache_service.delete_cache(cache_key)
            logger.info(f"Invalidated cache: {cache_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate cache {cache_key}: {e}")
            return False

    def invalidate_all_health_cache(self) -> bool:
        """Invalidate all health-related cache"""
        try:
            health_cache_service.clear_health_cache()
            logger.info("Invalidated all health cache")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate all health cache: {e}")
            return False

    def invalidate_system_health(self) -> bool:
        """Invalidate system health cache"""
        return self.invalidate_cache(CACHE_PREFIX_SYSTEM)

    def invalidate_providers_health(self) -> bool:
        """Invalidate providers health cache"""
        return self.invalidate_cache(CACHE_PREFIX_PROVIDERS)

    def invalidate_models_health(self) -> bool:
        """Invalidate models health cache"""
        return self.invalidate_cache(CACHE_PREFIX_MODELS)

    def invalidate_summary(self) -> bool:
        """Invalidate health summary cache"""
        return self.invalidate_cache(CACHE_PREFIX_SUMMARY)

    def invalidate_dashboard(self) -> bool:
        """Invalidate health dashboard cache"""
        return self.invalidate_cache(CACHE_PREFIX_DASHBOARD)

    def invalidate_gateway_health(self) -> bool:
        """Invalidate gateway health cache"""
        return self.invalidate_cache(CACHE_PREFIX_GATEWAY)

    def invalidate_dependent_caches(self, primary_cache_key: str) -> None:
        """Invalidate caches that depend on a primary cache"""
        # Dashboard depends on system, providers, and models
        if primary_cache_key in [CACHE_PREFIX_SYSTEM, CACHE_PREFIX_PROVIDERS, CACHE_PREFIX_MODELS]:
            self.invalidate_dashboard()
            self.invalidate_summary()

        # Summary depends on system, providers, and models
        if primary_cache_key in [CACHE_PREFIX_SYSTEM, CACHE_PREFIX_PROVIDERS, CACHE_PREFIX_MODELS]:
            self.invalidate_summary()

    async def schedule_cache_refresh(
        self,
        cache_key: str,
        refresh_func: Callable,
        interval: int = 60,
    ) -> None:
        """
        Schedule periodic cache refresh

        Args:
            cache_key: Cache key to refresh
            refresh_func: Async function to call for refresh
            interval: Refresh interval in seconds
        """
        task_name = f"refresh_{cache_key}"

        # Cancel existing task if any
        if task_name in self.refresh_tasks:
            self.refresh_tasks[task_name].cancel()

        async def refresh_loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    logger.debug(f"Refreshing cache: {cache_key}")
                    await refresh_func()
                except asyncio.CancelledError:
                    logger.debug(f"Cache refresh cancelled for {cache_key}")
                    break
                except Exception as e:
                    logger.error(f"Error refreshing cache {cache_key}: {e}")

        task = asyncio.create_task(refresh_loop())
        self.refresh_tasks[task_name] = task
        logger.info(f"Scheduled cache refresh for {cache_key} every {interval}s")

    def cancel_cache_refresh(self, cache_key: str) -> None:
        """Cancel scheduled cache refresh"""
        task_name = f"refresh_{cache_key}"
        if task_name in self.refresh_tasks:
            self.refresh_tasks[task_name].cancel()
            del self.refresh_tasks[task_name]
            logger.info(f"Cancelled cache refresh for {cache_key}")

    def get_cache_status(self) -> dict:
        """Get status of all cached items"""
        try:
            status = {
                "system_health": health_cache_service.get_system_health() is not None,
                "providers_health": health_cache_service.get_providers_health() is not None,
                "models_health": health_cache_service.get_models_health() is not None,
                "summary": health_cache_service.get_health_summary() is not None,
                "dashboard": health_cache_service.get_health_dashboard() is not None,
                "gateway_health": health_cache_service.get_gateway_health() is not None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return status
        except Exception as e:
            logger.error(f"Failed to get cache status: {e}")
            return {}

    def get_cache_statistics(self) -> dict:
        """Get cache statistics including compression ratios"""
        try:
            stats = health_cache_service.get_all_cache_stats()
            return {
                "cache_stats": stats,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get cache statistics: {e}")
            return {}


# Global cache invalidation service instance
cache_invalidation_service = CacheInvalidationService()
