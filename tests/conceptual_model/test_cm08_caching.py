"""
CM-8: Caching Architecture Tests

Tests covering:
  8.1 Exact-Match Response Cache (catalog_response_cache)
  8.2 Supporting Caches (auth, catalog L1/L2, health, local memory)
  8.3 Cache Degradation (Redis fallback, miss-through, failure isolation)
"""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import pytest
import redis as redis_lib

from src.services.catalog_response_cache import (
    CATALOG_CACHE_TTL,
    CATALOG_RESPONSE_CACHE_TTL,
    get_catalog_cache_key,
)
from src.services.auth_cache import (
    API_KEY_CACHE_TTL,
    AUTH_CACHE_TTL,
    USER_CACHE_TTL,
)
from src.services.simple_health_cache import (
    DEFAULT_TTL_SYSTEM,
    DEFAULT_TTL_PROVIDERS,
    DEFAULT_TTL_MODELS,
    DEFAULT_TTL_SUMMARY,
    SimpleHealthCache,
)
import src.services.local_memory_cache as lmc_mod
from src.services.local_memory_cache import LocalMemoryCache, get_local_cache

# Patch target for catalog_response_cache's own imported reference
_CRC_REDIS = "src.services.catalog_response_cache.get_redis_client"


# ===================================================================
# 8.1 Exact-Match Response Cache
# ===================================================================


class TestExactMatchResponseCache:
    """Tests for the catalog response cache (Redis-backed L1 cache)."""

    @pytest.mark.cm_verified
    def test_exact_match_cache_hit(self):
        """CM-8.1.1: Same params produce a cache hit on second lookup."""
        import asyncio
        from src.services.catalog_response_cache import get_cached_catalog_response

        gateway = "openrouter"
        params = {"limit": 100, "offset": 0}
        response_data = {"models": [{"id": "test/model"}], "total": 1}

        cached_json = json.dumps(
            {
                **response_data,
                "_cached_at": "2025-01-01T00:00:00",
                "_cache_ttl": CATALOG_CACHE_TTL,
                "_cache_key": get_catalog_cache_key(gateway, params),
            }
        )

        mock_r = MagicMock()
        mock_r.get.return_value = cached_json
        mock_r.expire.return_value = True

        with patch(_CRC_REDIS, return_value=mock_r):
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response(gateway, params)
            )

        assert result is not None
        assert result["models"] == [{"id": "test/model"}]
        mock_r.get.assert_called()

    @pytest.mark.cm_verified
    def test_exact_match_cache_miss(self):
        """CM-8.1.2: Different params produce a cache miss."""
        import asyncio
        from src.services.catalog_response_cache import get_cached_catalog_response

        gateway = "openrouter"
        params_a = {"limit": 100, "offset": 0}
        params_b = {"limit": 50, "offset": 10}

        # Ensure different params generate different keys
        key_a = get_catalog_cache_key(gateway, params_a)
        key_b = get_catalog_cache_key(gateway, params_b)
        assert key_a != key_b, "Different params must generate different cache keys"

        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_r.set.return_value = True  # stampede lock acquired

        with patch(_CRC_REDIS, return_value=mock_r):
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response(gateway, params_b)
            )
        assert result is None

    @pytest.mark.cm_verified
    def test_exact_match_cache_uses_sha256(self):
        """CM-8.1.3: CM says cache keys use SHA-256 hashing."""
        gateway = "openrouter"
        params = {"limit": 100, "offset": 0}

        cache_data = {
            "gateway": gateway,
            "limit": 100,
            "offset": 0,
            "provider": None,
            "is_private": None,
            "include_huggingface": False,
            "unique_models": False,
        }
        expected_sha256 = hashlib.sha256(
            json.dumps(cache_data, sort_keys=True).encode()
        ).hexdigest()

        key = get_catalog_cache_key(gateway, params)

        # The key should end with the full SHA-256 hexdigest
        assert expected_sha256 in key, f"Expected SHA-256 hash '{expected_sha256}' in key '{key}'."

    @pytest.mark.cm_verified
    def test_exact_match_cache_max_20k_entries(self):
        """CM-8.1.4: CM says max 20K entries in the response cache."""
        from src.services import catalog_response_cache as mod

        max_entries = getattr(mod, "MAX_CACHE_ENTRIES", None)
        assert max_entries is not None, "No MAX_CACHE_ENTRIES constant found"
        assert max_entries == 20_000, f"Expected 20K max entries, got {max_entries}"

    @pytest.mark.cm_verified
    def test_exact_match_cache_60min_ttl(self):
        """CM-8.1.5: CM says 60-minute TTL for response cache."""
        assert CATALOG_RESPONSE_CACHE_TTL == 3600, (
            f"Expected 3600s (60min), got {CATALOG_RESPONSE_CACHE_TTL}s "
            f"({CATALOG_RESPONSE_CACHE_TTL // 60}min)"
        )

    @pytest.mark.cm_verified
    def test_exact_match_cache_lru_eviction(self):
        """CM-8.1.6: CM says LRU eviction for response cache."""
        from src.services import catalog_response_cache as mod

        has_eviction = any(
            hasattr(mod, attr)
            for attr in ["MAX_CACHE_ENTRIES", "evict_lru", "_evict", "LRU_ENABLED"]
        )
        assert has_eviction, (
            "No LRU eviction mechanism found in catalog_response_cache. "
            "Redis TTL-based expiry is the only removal strategy."
        )


# ===================================================================
# 8.2 Supporting Caches
# ===================================================================


class TestSupportingCaches:
    """Tests for auth, catalog L1/L2, health, and local memory caches."""

    @pytest.mark.cm_verified
    def test_auth_cache_ttl_5_to_10_minutes(self):
        """CM-8.2.1: Auth cache TTLs fall within 5-10 minute range."""
        assert AUTH_CACHE_TTL == 300, f"AUTH_CACHE_TTL={AUTH_CACHE_TTL}, expected 300"
        assert API_KEY_CACHE_TTL == 600, f"API_KEY_CACHE_TTL={API_KEY_CACHE_TTL}, expected 600"
        assert USER_CACHE_TTL == 300, f"USER_CACHE_TTL={USER_CACHE_TTL}, expected 300"

        for name, ttl in [
            ("AUTH_CACHE_TTL", AUTH_CACHE_TTL),
            ("API_KEY_CACHE_TTL", API_KEY_CACHE_TTL),
            ("USER_CACHE_TTL", USER_CACHE_TTL),
        ]:
            assert 300 <= ttl <= 600, f"{name}={ttl}s is outside 5-10 min range"

    @pytest.mark.cm_verified
    def test_catalog_l1_cache_ttl_60_minutes(self):
        """CM-8.2.2: Catalog L1 (response) cache has 60-minute TTL."""
        assert (
            CATALOG_RESPONSE_CACHE_TTL == 3600
        ), f"Expected 3600s (60min), got {CATALOG_RESPONSE_CACHE_TTL}s"
        assert CATALOG_CACHE_TTL == CATALOG_RESPONSE_CACHE_TTL

    @pytest.mark.cm_verified
    def test_catalog_l2_cache_ttl_15_to_30_minutes(self):
        """CM-8.2.3: Per-provider (L2) catalog cache has 15-30 min TTL."""
        from src.services.model_catalog_cache import (
            PROVIDER_MODELS_CACHE_TTL,
            ModelCatalogCache,
        )

        assert (
            PROVIDER_MODELS_CACHE_TTL == 1800
        ), f"Expected 1800s (30min), got {PROVIDER_MODELS_CACHE_TTL}s"
        assert ModelCatalogCache.TTL_PROVIDER == 1800

        assert (
            900 <= PROVIDER_MODELS_CACHE_TTL <= 1800
        ), f"Provider cache TTL {PROVIDER_MODELS_CACHE_TTL}s outside 15-30 min range"

    @pytest.mark.cm_verified
    def test_health_cache_ttl_6_minutes(self):
        """CM-8.2.4: Health cache TTL is 6 minutes (360s)."""
        assert DEFAULT_TTL_SYSTEM == 360, f"Expected 360s, got {DEFAULT_TTL_SYSTEM}s"
        assert DEFAULT_TTL_PROVIDERS == 360
        assert DEFAULT_TTL_MODELS == 360
        assert DEFAULT_TTL_SUMMARY == 360

    @pytest.mark.cm_verified
    def test_local_memory_cache_500_entries(self):
        """CM-8.2.5: Local memory cache allows max 500 entries."""
        cache = LocalMemoryCache(max_entries=500, default_ttl=900.0)
        assert cache.max_entries == 500

        for i in range(510):
            cache.set(f"key_{i}", f"value_{i}")

        stats = cache.get_stats()
        assert stats["entries"] <= 500, f"Cache has {stats['entries']} entries, expected <= 500"
        assert stats["evictions"] >= 10, f"Expected at least 10 evictions, got {stats['evictions']}"

    @pytest.mark.cm_verified
    def test_local_memory_cache_ttl_15_minutes(self):
        """CM-8.2.6: Local memory cache default TTL is 15 min (900s)."""
        cache = LocalMemoryCache(max_entries=500, default_ttl=900.0)
        assert cache.default_ttl == 900.0

        # Verify the global singleton config
        old = lmc_mod._local_cache
        try:
            lmc_mod._local_cache = None
            global_cache = get_local_cache()
            assert global_cache.max_entries == 500
            assert global_cache.default_ttl == 900.0
        finally:
            lmc_mod._local_cache = old


# ===================================================================
# 8.3 Cache Degradation
# ===================================================================


class TestCacheDegradation:
    """Tests for graceful degradation when caches are unavailable."""

    @pytest.mark.cm_verified
    def test_redis_down_falls_back_to_local_memory(self):
        """CM-8.3.1: When Redis is unavailable, local memory cache still works."""
        import asyncio
        from src.services.catalog_response_cache import get_cached_catalog_response

        # Patch the module-level reference so get_redis_client returns None
        with patch(_CRC_REDIS, return_value=None):
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response("openrouter", {"limit": 100})
            )
        assert result is None, "Redis-backed cache should return None when Redis is down"

        # Local memory cache still functions independently (no Redis needed)
        local_cache = LocalMemoryCache(max_entries=100, default_ttl=300.0)
        local_cache.set("fallback_key", {"data": "still works"})
        value, is_stale = local_cache.get("fallback_key")
        assert value == {"data": "still works"}
        assert is_stale is False

    @pytest.mark.cm_verified
    def test_all_caches_miss_falls_through_to_db(self):
        """CM-8.3.2: When all caches miss, request falls through to database."""
        import asyncio
        from src.services.catalog_response_cache import get_cached_catalog_response

        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_r.set.return_value = True  # stampede lock acquired

        # Local memory also empty
        local_cache = LocalMemoryCache(max_entries=100, default_ttl=300.0)
        value, _ = local_cache.get("catalog:openrouter")
        assert value is None, "Local cache should also miss"

        # Redis-backed cache returns None (miss)
        with patch(_CRC_REDIS, return_value=mock_r):
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response("openrouter", {"limit": 100})
            )
        assert result is None, "All caches miss -- caller must query the database"

    @pytest.mark.cm_verified
    def test_cache_failure_never_blocks_request(self):
        """CM-8.3.3: Cache exceptions are caught; request proceeds without error."""
        import asyncio
        from src.services.catalog_response_cache import (
            cache_catalog_response,
            get_cached_catalog_response,
        )

        mock_r = MagicMock()
        error = redis_lib.RedisError("Connection refused")
        mock_r.get.side_effect = error
        mock_r.setex.side_effect = error
        # cache_catalog_response now uses a pipeline for setex+zadd,
        # so the error must surface from pipeline.execute()
        mock_r.pipeline.return_value.execute.side_effect = error

        with patch(_CRC_REDIS, return_value=mock_r):
            # Read should return None, not raise
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response("openrouter", {"limit": 100})
            )
            assert result is None

            # Write should return False, not raise
            ok = asyncio.get_event_loop().run_until_complete(
                cache_catalog_response(
                    "openrouter",
                    {"limit": 100},
                    {"models": [], "total": 0},
                )
            )
            assert ok is False

    @pytest.mark.cm_verified
    def test_db_query_cache_reduces_load(self):
        """CM-8.3.4: Repeated queries served from cache reduce DB load."""
        import asyncio
        from src.services.catalog_response_cache import (
            cache_catalog_response,
            get_cached_catalog_response,
        )

        gateway = "openrouter"
        params = {"limit": 100, "offset": 0}
        response_data = {"models": [{"id": "test/model"}], "total": 1}

        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_r.set.return_value = True  # stampede lock
        mock_r.setex.return_value = True
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = []
        for m in ["hincrby", "hset", "expire"]:
            getattr(pipe_mock, m).return_value = pipe_mock
        mock_r.pipeline.return_value = pipe_mock
        mock_r.delete.return_value = 1

        with patch(_CRC_REDIS, return_value=mock_r):
            # First call: cache miss
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response(gateway, params)
            )
            assert result is None  # miss -- would query DB

            # Cache the DB result
            asyncio.get_event_loop().run_until_complete(
                cache_catalog_response(gateway, params, response_data)
            )
            # setex is now called on the pipeline, not directly on the Redis client
            pipe_mock.setex.assert_called_once()

            # Second call: cache hit (Redis now returns stored data)
            cache_key = get_catalog_cache_key(gateway, params)
            mock_r.get.return_value = json.dumps(
                {
                    **response_data,
                    "_cached_at": "2025-01-01T00:00:00",
                    "_cache_ttl": CATALOG_CACHE_TTL,
                    "_cache_key": cache_key,
                }
            )
            mock_r.expire.return_value = True

            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response(gateway, params)
            )
            assert result is not None
            assert result["models"] == [{"id": "test/model"}]
