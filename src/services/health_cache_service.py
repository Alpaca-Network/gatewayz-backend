"""
Health Data Caching Service with Compression

Provides efficient caching of health monitoring data using Redis with
compression to reduce payload size and improve availability.
"""

import gzip
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from src.redis_config import get_redis_client

logger = logging.getLogger(__name__)

# Cache key prefixes
CACHE_PREFIX_HEALTH = "health:"
CACHE_PREFIX_SYSTEM = "health:system"
CACHE_PREFIX_PROVIDERS = "health:providers"
CACHE_PREFIX_MODELS = "health:models"
CACHE_PREFIX_SUMMARY = "health:summary"
CACHE_PREFIX_DASHBOARD = "health:dashboard"
CACHE_PREFIX_GATEWAY = "health:gateway"

# Default TTLs (in seconds)
DEFAULT_TTL_SYSTEM = 60  # 1 minute for system health
DEFAULT_TTL_PROVIDERS = 60  # 1 minute for provider health
DEFAULT_TTL_MODELS = 120  # 2 minutes for model health
DEFAULT_TTL_SUMMARY = 60  # 1 minute for summary
DEFAULT_TTL_DASHBOARD = 30  # 30 seconds for dashboard (most frequently accessed)
DEFAULT_TTL_GATEWAY = 120  # 2 minutes for gateway health


class HealthCacheService:
    """Service for caching health data with compression"""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.compression_enabled = True
        self.compression_threshold = 1024  # Compress if > 1KB

    def _compress_data(self, data: str) -> bytes:
        """Compress JSON string using gzip"""
        try:
            return gzip.compress(data.encode("utf-8"), compresslevel=6)
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            return data.encode("utf-8")

    def _decompress_data(self, data: bytes) -> str:
        """Decompress gzip data"""
        try:
            return gzip.decompress(data).decode("utf-8")
        except Exception as e:
            logger.error(f"Decompression failed: {e}")
            # Fallback: try to decode as plain string
            try:
                return data.decode("utf-8")
            except Exception:
                return ""

    def _serialize_data(self, data: Any) -> str:
        """Serialize data to JSON string"""
        try:
            if isinstance(data, dict):
                # Handle dataclass instances in dict values
                return json.dumps(data, default=self._json_serializer)
            else:
                return json.dumps(asdict(data), default=self._json_serializer)
        except Exception as e:
            logger.error(f"Serialization failed: {e}")
            return ""

    def _json_serializer(self, obj: Any) -> Any:
        """Custom JSON serializer for complex types"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return str(obj)

    def set_cache(
        self, key: str, data: Any, ttl: int = 60, compress: bool = True
    ) -> bool:
        """
        Store data in Redis cache with optional compression

        Args:
            key: Cache key
            data: Data to cache (dict or dataclass)
            ttl: Time to live in seconds
            compress: Whether to compress data

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client:
            logger.debug("Redis client not available, skipping cache")
            return False

        try:
            # Serialize data
            serialized = self._serialize_data(data)
            if not serialized:
                return False

            # Decide whether to compress
            should_compress = (
                compress
                and self.compression_enabled
                and len(serialized) > self.compression_threshold
            )

            if should_compress:
                # Store compressed data with marker
                compressed = self._compress_data(serialized)
                self.redis_client.setex(
                    f"{key}:compressed", ttl, compressed
                )
                # Store metadata
                self.redis_client.setex(
                    f"{key}:meta",
                    ttl,
                    json.dumps({
                        "compressed": True,
                        "original_size": len(serialized),
                        "compressed_size": len(compressed),
                        "compression_ratio": len(compressed) / len(serialized),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }),
                )
                logger.debug(
                    f"Cached {key} (compressed: {len(compressed)}B, "
                    f"ratio: {len(compressed)/len(serialized):.2%})"
                )
            else:
                # Store uncompressed
                self.redis_client.setex(key, ttl, serialized)
                logger.debug(f"Cached {key} (uncompressed: {len(serialized)}B)")

            return True

        except Exception as e:
            logger.error(f"Failed to cache {key}: {e}")
            return False

    def get_cache(self, key: str) -> Optional[dict]:
        """
        Retrieve data from Redis cache with automatic decompression

        Args:
            key: Cache key

        Returns:
            Deserialized data or None if not found
        """
        if not self.redis_client:
            logger.debug("Redis client not available, skipping cache retrieval")
            return None

        try:
            # Try to get compressed version first
            compressed_data = self.redis_client.get(f"{key}:compressed")
            if compressed_data:
                decompressed = self._decompress_data(compressed_data)
                return json.loads(decompressed)

            # Try to get uncompressed version
            data = self.redis_client.get(key)
            if data:
                return json.loads(data)

            return None

        except Exception as e:
            logger.error(f"Failed to retrieve cache {key}: {e}")
            return None

    def delete_cache(self, key: str) -> bool:
        """Delete cache entry and metadata"""
        if not self.redis_client:
            return False

        try:
            self.redis_client.delete(key, f"{key}:compressed", f"{key}:meta")
            logger.debug(f"Deleted cache {key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete cache {key}: {e}")
            return False

    def clear_health_cache(self) -> bool:
        """Clear all health-related cache entries"""
        if not self.redis_client:
            return False

        try:
            # Get all health cache keys
            pattern = f"{CACHE_PREFIX_HEALTH}*"
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"Cleared {len(keys)} health cache entries")
            return True
        except Exception as e:
            logger.error(f"Failed to clear health cache: {e}")
            return False

    def get_cache_stats(self, key: str) -> Optional[dict]:
        """Get compression statistics for a cache entry"""
        if not self.redis_client:
            return None

        try:
            meta = self.redis_client.get(f"{key}:meta")
            if meta:
                return json.loads(meta)
            return None
        except Exception as e:
            logger.error(f"Failed to get cache stats for {key}: {e}")
            return None

    def cache_system_health(self, data: dict, ttl: int = DEFAULT_TTL_SYSTEM) -> bool:
        """Cache system health data"""
        return self.set_cache(CACHE_PREFIX_SYSTEM, data, ttl)

    def get_system_health(self) -> Optional[dict]:
        """Retrieve cached system health data"""
        return self.get_cache(CACHE_PREFIX_SYSTEM)

    def cache_providers_health(self, data: list, ttl: int = DEFAULT_TTL_PROVIDERS) -> bool:
        """Cache providers health data"""
        return self.set_cache(CACHE_PREFIX_PROVIDERS, {"providers": data}, ttl)

    def get_providers_health(self) -> Optional[list]:
        """Retrieve cached providers health data"""
        cached = self.get_cache(CACHE_PREFIX_PROVIDERS)
        return cached.get("providers") if cached else None

    def cache_models_health(self, data: list, ttl: int = DEFAULT_TTL_MODELS) -> bool:
        """Cache models health data"""
        return self.set_cache(CACHE_PREFIX_MODELS, {"models": data}, ttl)

    def get_models_health(self) -> Optional[list]:
        """Retrieve cached models health data"""
        cached = self.get_cache(CACHE_PREFIX_MODELS)
        return cached.get("models") if cached else None

    def cache_health_summary(self, data: dict, ttl: int = DEFAULT_TTL_SUMMARY) -> bool:
        """Cache complete health summary"""
        return self.set_cache(CACHE_PREFIX_SUMMARY, data, ttl)

    def get_health_summary(self) -> Optional[dict]:
        """Retrieve cached health summary"""
        return self.get_cache(CACHE_PREFIX_SUMMARY)

    def cache_health_dashboard(self, data: dict, ttl: int = DEFAULT_TTL_DASHBOARD) -> bool:
        """Cache health dashboard data (most frequently accessed)"""
        return self.set_cache(CACHE_PREFIX_DASHBOARD, data, ttl)

    def get_health_dashboard(self) -> Optional[dict]:
        """Retrieve cached health dashboard data"""
        return self.get_cache(CACHE_PREFIX_DASHBOARD)

    def cache_gateway_health(self, data: dict, ttl: int = DEFAULT_TTL_GATEWAY) -> bool:
        """Cache gateway health data"""
        return self.set_cache(CACHE_PREFIX_GATEWAY, data, ttl)

    def get_gateway_health(self) -> Optional[dict]:
        """Retrieve cached gateway health data"""
        return self.get_cache(CACHE_PREFIX_GATEWAY)

    def get_all_cache_stats(self) -> dict:
        """Get statistics for all health cache entries"""
        if not self.redis_client:
            return {}

        try:
            stats = {}
            keys = [
                CACHE_PREFIX_SYSTEM,
                CACHE_PREFIX_PROVIDERS,
                CACHE_PREFIX_MODELS,
                CACHE_PREFIX_SUMMARY,
                CACHE_PREFIX_DASHBOARD,
                CACHE_PREFIX_GATEWAY,
            ]

            for key in keys:
                meta = self.get_cache_stats(key)
                if meta:
                    stats[key] = meta

            return stats
        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {}


# Global cache service instance
health_cache_service = HealthCacheService()
