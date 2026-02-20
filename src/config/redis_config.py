#!/usr/bin/env python3
"""
Redis Configuration Module
Handles Redis connection and configuration for rate limiting and caching.
"""

import logging
import os
import threading

import redis
from redis.connection import ConnectionPool

logger = logging.getLogger(__name__)


class RedisConfig:
    """Redis configuration and connection management"""

    def __init__(self):
        # Get Redis URL from environment
        # Priority: REDIS_URL (full connection string) > localhost fallback
        # Note: RAILWAY_SERVICE_REDIS_URL is just a hostname, not a full URL, so we use REDIS_URL
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.redis_password = os.environ.get("REDIS_PASSWORD")
        self.redis_host = os.environ.get("REDIS_HOST", "localhost")
        self.redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        self.redis_db = int(os.environ.get("REDIS_DB", "0"))
        self.redis_max_connections = int(os.environ.get("REDIS_MAX_CONNECTIONS", "50"))
        # Reduced timeouts to fail fast - with working Redis these should be quick
        # If Redis is slow, we fall back to local cache gracefully
        self.redis_socket_timeout = int(os.environ.get("REDIS_SOCKET_TIMEOUT", "5"))
        self.redis_socket_connect_timeout = int(os.environ.get("REDIS_SOCKET_CONNECT_TIMEOUT", "3"))
        self.redis_retry_on_timeout = (
            os.environ.get("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true"
        )

        self._client: redis.Redis | None = None
        self._pool: ConnectionPool | None = None

        # Cached availability check to avoid pinging Redis on every operation.
        # Without this, 30 concurrent cache reads each do a PING round-trip,
        # and a momentary Redis latency spike causes all of them to "miss"
        # and fall through to expensive DB queries (thundering herd).
        self._available_cached: bool | None = None
        self._available_cached_at: float = 0.0
        self._available_cache_ttl: float = 30.0  # seconds

    def get_connection_pool(self) -> ConnectionPool:
        """Get Redis connection pool"""
        if self._pool is None:
            # Parse Redis URL if it contains connection details
            if self.redis_url and "://" in self.redis_url:
                # Use URL-based connection for Redis Cloud (Upstash, Railway, etc.)
                # For Upstash with TLS (rediss://), we need to disable SSL cert verification
                connection_kwargs = {
                    "max_connections": self.redis_max_connections,
                    "socket_timeout": self.redis_socket_timeout,
                    "socket_connect_timeout": self.redis_socket_connect_timeout,
                    "retry_on_timeout": self.redis_retry_on_timeout,
                    "decode_responses": True,
                }

                # Add SSL configuration for rediss:// URLs (Upstash)
                if self.redis_url.startswith("rediss://"):
                    connection_kwargs["ssl_cert_reqs"] = None  # Don't verify SSL cert

                self._pool = ConnectionPool.from_url(self.redis_url, **connection_kwargs)
            else:
                # Use individual parameters for local Redis
                self._pool = ConnectionPool(
                    host=self.redis_host,
                    port=self.redis_port,
                    db=self.redis_db,
                    password=self.redis_password,
                    max_connections=self.redis_max_connections,
                    socket_timeout=self.redis_socket_timeout,
                    socket_connect_timeout=self.redis_socket_connect_timeout,
                    retry_on_timeout=self.redis_retry_on_timeout,
                    decode_responses=True,
                )
        return self._pool

    def get_client(self) -> redis.Redis:
        """Get Redis client instance"""
        if self._client is None:
            try:
                self._client = redis.Redis(
                    connection_pool=self.get_connection_pool(), decode_responses=True
                )
                # Test connection
                self._client.ping()
                logger.info("Redis connection established successfully")
            except Exception as e:
                # Log at debug level - this is expected behavior in dev/environments without Redis
                # The system gracefully falls back to local memory cache
                logger.debug(f"Redis unavailable: {e}. Using local memory cache fallback.")
                self._client = None
        return self._client

    def is_available(self) -> bool:
        """Check if Redis is available (cached for 30s to avoid PING on every operation).

        Previously, every cache read/write called this method which did a live
        PING to Redis. Under parallel load (e.g., 30 gateway catalog reads),
        this caused 30 extra round-trips and transient timeouts could cascade
        into thundering-herd DB queries.

        Now caches the result for 30 seconds. On failure, caches for only 5s
        so recovery is fast.
        """
        import time

        now = time.monotonic()
        age = now - self._available_cached_at

        # Cache hit: return cached True for 30s, cached False for 5s
        if self._available_cached is not None:
            ttl = self._available_cache_ttl if self._available_cached else 5.0
            if age < ttl:
                return self._available_cached

        # Perform actual check
        try:
            client = self.get_client()
            if client:
                client.ping()
                self._available_cached = True
                self._available_cached_at = now
                return True
        except Exception:
            pass

        self._available_cached = False
        self._available_cached_at = now
        return False

    def get_cache_key(self, prefix: str, identifier: str) -> str:
        """Generate cache key with prefix"""
        return f"{prefix}:{identifier}"

    def set_cache(self, key: str, value: any, ttl: int = 300) -> bool:
        """Set cache value with TTL"""
        try:
            client = self.get_client()
            if client:
                client.setex(key, ttl, value)
                return True
        except Exception as e:
            logger.warning(f"Failed to set cache key {key}: {e}")
        return False

    def get_cache(self, key: str) -> str | None:
        """Get cache value"""
        try:
            client = self.get_client()
            if client:
                return client.get(key)
        except Exception as e:
            logger.warning(f"Failed to get cache key {key}: {e}")
        return None

    def delete_cache(self, key: str) -> bool:
        """Delete cache key"""
        try:
            client = self.get_client()
            if client:
                client.delete(key)
                return True
        except Exception as e:
            logger.warning(f"Failed to delete cache key {key}: {e}")
        return False

    def increment_counter(self, key: str, amount: int = 1, ttl: int = 300) -> int | None:
        """Increment counter with TTL"""
        try:
            client = self.get_client()
            if client:
                pipe = client.pipeline()
                pipe.incrby(key, amount)
                pipe.expire(key, ttl)
                results = pipe.execute()
                return results[0]
        except Exception as e:
            logger.warning(f"Failed to increment counter {key}: {e}")
        return None

    def get_counter(self, key: str) -> int | None:
        """Get counter value"""
        try:
            client = self.get_client()
            if client:
                value = client.get(key)
                return int(value) if value else 0
        except Exception as e:
            logger.warning(f"Failed to get counter {key}: {e}")
        return None

    def set_hash(self, key: str, field: str, value: any, ttl: int = 300) -> bool:
        """Set hash field value with TTL"""
        try:
            client = self.get_client()
            if client:
                pipe = client.pipeline()
                pipe.hset(key, field, value)
                pipe.expire(key, ttl)
                pipe.execute()
                return True
        except Exception as e:
            logger.warning(f"Failed to set hash {key}.{field}: {e}")
        return False

    def get_hash(self, key: str, field: str) -> str | None:
        """Get hash field value"""
        try:
            client = self.get_client()
            if client:
                return client.hget(key, field)
        except Exception as e:
            logger.warning(f"Failed to get hash {key}.{field}: {e}")
        return None

    def get_all_hash(self, key: str) -> dict:
        """Get all hash fields"""
        try:
            client = self.get_client()
            if client:
                return client.hgetall(key)
        except Exception as e:
            logger.warning(f"Failed to get all hash {key}: {e}")
        return {}

    def delete_hash(self, key: str, field: str) -> bool:
        """Delete hash field"""
        try:
            client = self.get_client()
            if client:
                client.hdel(key, field)
                return True
        except Exception as e:
            logger.warning(f"Failed to delete hash field {key}.{field}: {e}")
        return False

    def add_to_set(self, key: str, value: str, ttl: int = 300) -> bool:
        """Add value to set with TTL"""
        try:
            client = self.get_client()
            if client:
                pipe = client.pipeline()
                pipe.sadd(key, value)
                pipe.expire(key, ttl)
                pipe.execute()
                return True
        except Exception as e:
            logger.warning(f"Failed to add to set {key}: {e}")
        return False

    def is_in_set(self, key: str, value: str) -> bool:
        """Check if value is in set"""
        try:
            client = self.get_client()
            if client:
                return client.sismember(key, value)
        except Exception as e:
            logger.warning(f"Failed to check set membership {key}: {e}")
        return False

    def remove_from_set(self, key: str, value: str) -> bool:
        """Remove value from set"""
        try:
            client = self.get_client()
            if client:
                client.srem(key, value)
                return True
        except Exception as e:
            logger.warning(f"Failed to remove from set {key}: {e}")
        return False

    def get_set_size(self, key: str) -> int:
        """Get set size"""
        try:
            client = self.get_client()
            if client:
                return client.scard(key)
        except Exception as e:
            logger.warning(f"Failed to get set size {key}: {e}")
        return 0

    def cleanup_expired_keys(self, pattern: str = "*") -> int:
        """Clean up expired keys matching pattern.

        Uses SCAN instead of KEYS to avoid blocking Redis under large keyspaces.
        SCAN is cursor-based and processes keys in batches, keeping Redis
        responsive while iterating over the full keyspace.
        """
        client = self.get_client()
        if not client:
            return 0
        try:
            total_deleted = 0
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    total_deleted += client.delete(*keys)
                if cursor == 0:
                    break
            return total_deleted
        except Exception as e:
            logger.error(f"Error cleaning up keys: {e}")
            return 0


# Global Redis configuration instance
_redis_config = None
_redis_config_lock = threading.Lock()


def get_redis_config() -> RedisConfig:
    """Get global Redis configuration instance (thread-safe singleton).

    Uses double-checked locking so that the common case (instance already
    created) never acquires the lock, while still preventing two threads from
    racing to create the first instance simultaneously.
    """
    global _redis_config
    if _redis_config is None:
        with _redis_config_lock:
            if _redis_config is None:
                _redis_config = RedisConfig()
    return _redis_config


def get_redis_client() -> redis.Redis | None:
    """Get Redis client instance"""
    config = get_redis_config()
    return config.get_client()


def is_redis_available() -> bool:
    """Check if Redis is available"""
    config = get_redis_config()
    return config.is_available()


def get_redis_manager() -> RedisConfig:
    """Get Redis manager instance (alias for get_redis_config)"""
    return get_redis_config()
