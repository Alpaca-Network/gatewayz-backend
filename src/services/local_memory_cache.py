"""
Local In-Memory Cache with Stale-While-Revalidate Support

Provides a fast, thread-safe in-memory cache that serves as a fallback when
Redis is unavailable. Implements stale-while-revalidate pattern for better
availability.

Features:
- LRU eviction when max entries exceeded
- TTL-based expiration with grace period for stale data
- Thread-safe operations
- Automatic cleanup of expired entries
- Stale-while-revalidate pattern

Usage:
    cache = get_local_cache()

    # Set with TTL (60 seconds) and stale TTL (additional 300 seconds)
    cache.set("key", {"data": "value"}, ttl=60, stale_ttl=300)

    # Get returns (value, is_stale)
    value, is_stale = cache.get("key")
    if value:
        if is_stale:
            # Trigger background refresh
            pass
        return value
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with expiration tracking."""
    value: Any
    expires_at: float  # When entry becomes stale
    stale_expires_at: float  # When entry is removed entirely
    created_at: float


class LocalMemoryCache:
    """
    Thread-safe in-memory cache with LRU eviction and stale-while-revalidate.

    This cache serves as a fallback when Redis is unavailable or slow,
    ensuring the API can still respond with cached data.
    """

    def __init__(
        self,
        max_entries: int = 1000,
        default_ttl: float = 300.0,  # 5 minutes
        default_stale_ttl: float = 3600.0,  # 1 hour stale grace period
    ):
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self.default_stale_ttl = default_stale_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = Lock()
        self._stats = {
            "hits": 0,
            "stale_hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "expirations": 0,
        }

        logger.info(
            f"Local memory cache initialized: "
            f"max_entries={max_entries}, "
            f"default_ttl={default_ttl}s, "
            f"default_stale_ttl={default_stale_ttl}s"
        )

    def get(self, key: str) -> tuple[Any | None, bool]:
        """
        Get value from cache.

        Returns:
            Tuple of (value, is_stale):
            - (value, False): Fresh data
            - (value, True): Stale data (still usable, but should refresh)
            - (None, False): No data (cache miss)
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._stats["misses"] += 1
                return None, False

            now = time.time()

            # Check if completely expired (past stale window)
            if now > entry.stale_expires_at:
                # Remove expired entry
                del self._cache[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return None, False

            # Move to end (LRU update)
            self._cache.move_to_end(key)

            # Check if stale but still usable
            if now > entry.expires_at:
                self._stats["stale_hits"] += 1
                logger.debug(f"Local cache STALE HIT: {key}")
                return entry.value, True

            # Fresh data
            self._stats["hits"] += 1
            logger.debug(f"Local cache HIT: {key}")
            return entry.value, False

    def set(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
        stale_ttl: float | None = None,
    ) -> None:
        """
        Set value in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time until data is considered stale (uses default if None)
            stale_ttl: Additional time stale data is kept (uses default if None)
        """
        ttl = ttl if ttl is not None else self.default_ttl
        stale_ttl = stale_ttl if stale_ttl is not None else self.default_stale_ttl

        now = time.time()
        entry = CacheEntry(
            value=value,
            expires_at=now + ttl,
            stale_expires_at=now + ttl + stale_ttl,
            created_at=now,
        )

        with self._lock:
            # Check if we need to evict entries
            while len(self._cache) >= self.max_entries:
                # Remove oldest (LRU)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._stats["evictions"] += 1
                logger.debug(f"Local cache EVICTED: {oldest_key}")

            self._cache[key] = entry
            self._cache.move_to_end(key)
            self._stats["sets"] += 1
            logger.debug(f"Local cache SET: {key} (ttl={ttl}s, stale_ttl={stale_ttl}s)")

    def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Local cache DELETE: {key}")
                return True
            return False

    def clear(self) -> int:
        """Clear all entries from cache."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Local cache CLEARED: {count} entries removed")
            return count

    def cleanup_expired(self) -> int:
        """Remove all expired entries."""
        now = time.time()
        removed = 0

        with self._lock:
            # Create list of keys to remove (can't modify during iteration)
            expired_keys = [
                key for key, entry in self._cache.items()
                if now > entry.stale_expires_at
            ]

            for key in expired_keys:
                del self._cache[key]
                removed += 1

            if removed > 0:
                self._stats["expirations"] += removed
                logger.info(f"Local cache cleanup: {removed} expired entries removed")

        return removed

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = (
                self._stats["hits"] +
                self._stats["stale_hits"] +
                self._stats["misses"]
            )
            hit_rate = (
                (self._stats["hits"] + self._stats["stale_hits"]) / total_requests
                if total_requests > 0 else 0
            )

            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self._stats["hits"],
                "stale_hits": self._stats["stale_hits"],
                "misses": self._stats["misses"],
                "sets": self._stats["sets"],
                "evictions": self._stats["evictions"],
                "expirations": self._stats["expirations"],
                "hit_rate": round(hit_rate * 100, 2),
                "total_requests": total_requests,
            }

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        with self._lock:
            self._stats = {
                "hits": 0,
                "stale_hits": 0,
                "misses": 0,
                "sets": 0,
                "evictions": 0,
                "expirations": 0,
            }


# Global local cache instance
_local_cache: LocalMemoryCache | None = None


def get_local_cache() -> LocalMemoryCache:
    """Get the global local memory cache instance."""
    global _local_cache
    if _local_cache is None:
        _local_cache = LocalMemoryCache(
            max_entries=500,  # Keep up to 500 entries
            default_ttl=900.0,  # 15 minutes fresh
            default_stale_ttl=3600.0,  # 1 hour stale grace period
        )
    return _local_cache


# Convenience functions for catalog caching

def get_local_catalog(provider: str) -> tuple[list[dict] | None, bool]:
    """
    Get cached catalog from local memory.

    Returns:
        Tuple of (catalog, is_stale)
    """
    cache = get_local_cache()
    key = f"catalog:{provider}"
    return cache.get(key)


def set_local_catalog(
    provider: str,
    catalog: list[dict],
    ttl: float = 900.0,  # 15 minutes fresh
    stale_ttl: float = 3600.0,  # 1 hour stale
) -> None:
    """Cache catalog in local memory."""
    cache = get_local_cache()
    key = f"catalog:{provider}"
    cache.set(key, catalog, ttl=ttl, stale_ttl=stale_ttl)
