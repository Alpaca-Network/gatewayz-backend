"""
Cache Warming Service

Proactively refreshes caches before they expire to prevent thundering herd problems.
Runs background tasks to keep hot caches fresh without user-facing latency.

Features:
- Background refresh of catalog caches before expiration
- Prevents cache stampede during peak load
- Graceful degradation if background refresh fails
- Request coalescing to prevent duplicate fetches
"""

import asyncio
import logging
import time
from threading import Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CacheWarmer:
    """
    Background cache warming service with request coalescing.

    Prevents thundering herd by:
    1. Refreshing caches before they expire (proactive warming)
    2. Coalescing concurrent requests for the same key (only one fetch at a time)
    3. Serving stale data while refreshing in background
    """

    def __init__(self):
        # Track in-flight refresh operations to prevent duplicate work
        self._in_flight: dict[str, asyncio.Lock] = {}
        self._in_flight_lock = Lock()

        # Track last refresh times to prevent too-frequent refreshes
        self._last_refresh: dict[str, float] = {}
        self._last_refresh_lock = Lock()

        # Minimum time between refreshes (prevents excessive API calls)
        self.min_refresh_interval = 30.0  # 30 seconds

        self._stats = {
            "refreshes": 0,
            "coalesced": 0,
            "errors": 0,
            "skipped": 0,
        }

    def _get_or_create_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a specific cache key."""
        with self._in_flight_lock:
            if key not in self._in_flight:
                self._in_flight[key] = asyncio.Lock()
            return self._in_flight[key]

    def _should_refresh(self, key: str) -> bool:
        """Check if enough time has passed since last refresh."""
        with self._last_refresh_lock:
            last_time = self._last_refresh.get(key, 0)
            now = time.time()

            if now - last_time < self.min_refresh_interval:
                return False

            self._last_refresh[key] = now
            return True

    async def warm_cache(
        self,
        cache_key: str,
        fetch_fn: Callable[[], Any],
        set_cache_fn: Callable[[Any], None],
        force: bool = False,
    ) -> bool:
        """
        Warm a cache by fetching fresh data and updating the cache.

        Args:
            cache_key: Unique identifier for this cache operation
            fetch_fn: Function to fetch fresh data
            set_cache_fn: Function to update the cache with fresh data
            force: Force refresh even if recently refreshed

        Returns:
            True if cache was refreshed, False otherwise
        """
        # Check if we should refresh (rate limiting)
        if not force and not self._should_refresh(cache_key):
            self._stats["skipped"] += 1
            logger.debug(f"Skipping cache warm for {cache_key} (too soon since last refresh)")
            return False

        # Get lock for this cache key (request coalescing)
        lock = self._get_or_create_lock(cache_key)

        # Try to acquire lock (non-blocking)
        if lock.locked():
            # Another task is already refreshing this cache
            self._stats["coalesced"] += 1
            logger.debug(f"Cache refresh already in progress for {cache_key}, coalescing request")
            return False

        # Acquire lock and refresh
        async with lock:
            try:
                logger.info(f"Starting cache warm for {cache_key}")

                # Fetch fresh data using dedicated DB executor to avoid starving
                # the default thread pool used by asyncio.to_thread()
                from src.services.background_tasks import _db_executor

                loop = asyncio.get_event_loop()
                fresh_data = await loop.run_in_executor(_db_executor, fetch_fn)

                if fresh_data is not None:
                    # Update cache
                    set_cache_fn(fresh_data)
                    self._stats["refreshes"] += 1
                    logger.info(f"Successfully warmed cache for {cache_key}")
                    return True
                else:
                    logger.warning(f"Cache warm returned no data for {cache_key}")
                    return False

            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Error warming cache for {cache_key}: {e}")
                return False

    def warm_cache_sync(
        self,
        cache_key: str,
        fetch_fn: Callable[[], Any],
        set_cache_fn: Callable[[Any], None],
        force: bool = False,
    ) -> bool:
        """
        Synchronous version of warm_cache for use in non-async contexts.

        Note: This uses basic threading locks instead of asyncio locks.
        """
        # Check if we should refresh
        if not force and not self._should_refresh(cache_key):
            self._stats["skipped"] += 1
            logger.debug(f"Skipping cache warm for {cache_key} (too soon)")
            return False

        # Simple in-flight check (not as robust as async version)
        with self._in_flight_lock:
            if cache_key in self._in_flight:
                self._stats["coalesced"] += 1
                logger.debug(f"Cache refresh in progress for {cache_key}")
                return False
            self._in_flight[cache_key] = True  # type: ignore

        try:
            logger.info(f"Starting sync cache warm for {cache_key}")

            # Fetch fresh data
            fresh_data = fetch_fn()

            if fresh_data is not None:
                # Update cache
                set_cache_fn(fresh_data)
                self._stats["refreshes"] += 1
                logger.info(f"Successfully warmed cache for {cache_key}")
                return True
            else:
                logger.warning(f"Cache warm returned no data for {cache_key}")
                return False

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Error warming cache for {cache_key}: {e}")
            return False

        finally:
            # Remove from in-flight
            with self._in_flight_lock:
                if cache_key in self._in_flight:
                    del self._in_flight[cache_key]

    def get_stats(self) -> dict[str, Any]:
        """Get cache warmer statistics."""
        return {
            "refreshes": self._stats["refreshes"],
            "coalesced": self._stats["coalesced"],
            "errors": self._stats["errors"],
            "skipped": self._stats["skipped"],
            "in_flight": len(self._in_flight),
        }

    def reset_stats(self):
        """Reset statistics."""
        self._stats = {
            "refreshes": 0,
            "coalesced": 0,
            "errors": 0,
            "skipped": 0,
        }


# Global cache warmer instance
_cache_warmer: CacheWarmer | None = None


def get_cache_warmer() -> CacheWarmer:
    """Get the global cache warmer instance."""
    global _cache_warmer
    if _cache_warmer is None:
        _cache_warmer = CacheWarmer()
    return _cache_warmer
