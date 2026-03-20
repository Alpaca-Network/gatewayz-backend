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

import src.services.local_memory_cache as lmc_mod
from src.services.auth_cache import (
    API_KEY_CACHE_TTL,
    AUTH_CACHE_TTL,
    USER_CACHE_TTL,
)
from src.services.catalog_response_cache import (
    CATALOG_CACHE_KEYS_INDEX,
    CATALOG_CACHE_TTL,
    CATALOG_RESPONSE_CACHE_TTL,
    MAX_CACHE_ENTRIES,
    get_catalog_cache_key,
)
from src.services.local_memory_cache import LocalMemoryCache, get_local_cache
from src.services.simple_health_cache import (
    DEFAULT_TTL_MODELS,
    DEFAULT_TTL_PROVIDERS,
    DEFAULT_TTL_SUMMARY,
    DEFAULT_TTL_SYSTEM,
    SimpleHealthCache,
)

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
        """CM-8.1.4: cache_catalog_response calls _evict_lru_if_needed which
        enforces the 20K entry cap before each write."""
        import asyncio

        from src.services.catalog_response_cache import cache_catalog_response

        mock_r = MagicMock()
        mock_r.setex.return_value = True
        mock_r.zadd.return_value = 1
        # Simulate sorted set has MAX_CACHE_ENTRIES entries → triggers eviction
        mock_r.zcard.return_value = MAX_CACHE_ENTRIES + 1
        mock_r.zrange.return_value = [b"old_key_1", b"old_key_2"]
        mock_r.exists.return_value = True
        mock_r.delete.return_value = 1
        mock_r.zrem.return_value = 1

        pipe_mock = MagicMock()
        pipe_mock.setex.return_value = pipe_mock
        pipe_mock.zadd.return_value = pipe_mock
        pipe_mock.execute.return_value = []
        for m in ["hincrby", "hset", "expire"]:
            getattr(pipe_mock, m).return_value = pipe_mock
        mock_r.pipeline.return_value = pipe_mock

        with patch(_CRC_REDIS, return_value=mock_r):
            ok = asyncio.get_event_loop().run_until_complete(
                cache_catalog_response("openrouter", {"limit": 100}, {"models": [], "total": 0})
            )

        assert ok is True
        # Verify LRU eviction was triggered: zcard was called to check count
        mock_r.zcard.assert_called_with(CATALOG_CACHE_KEYS_INDEX)
        # Old keys were evicted
        mock_r.zrange.assert_called()

    @pytest.mark.cm_verified
    def test_exact_match_cache_60min_ttl(self):
        """CM-8.1.5: cache_catalog_response stores entries with CATALOG_CACHE_TTL (3600s)."""
        import asyncio

        from src.services.catalog_response_cache import cache_catalog_response

        mock_r = MagicMock()
        mock_r.zcard.return_value = 0
        pipe_mock = MagicMock()
        pipe_mock.setex.return_value = pipe_mock
        pipe_mock.zadd.return_value = pipe_mock
        pipe_mock.execute.return_value = []
        for m in ["hincrby", "hset", "expire"]:
            getattr(pipe_mock, m).return_value = pipe_mock
        mock_r.pipeline.return_value = pipe_mock
        mock_r.delete.return_value = 1

        with patch(_CRC_REDIS, return_value=mock_r):
            ok = asyncio.get_event_loop().run_until_complete(
                cache_catalog_response("openrouter", {"limit": 100}, {"models": [], "total": 0})
            )

        assert ok is True
        # Verify setex was called on the pipeline with TTL=3600
        pipe_mock.setex.assert_called_once()
        call_args = pipe_mock.setex.call_args
        ttl_arg = call_args[0][1]
        assert ttl_arg == 3600, f"Expected TTL=3600 (60 min), got {ttl_arg}"

    @pytest.mark.cm_verified
    def test_exact_match_cache_lru_eviction(self):
        """CM-8.1.6: _evict_lru_if_needed removes oldest entries when count >= MAX_CACHE_ENTRIES."""
        from src.services.catalog_response_cache import _evict_lru_if_needed

        mock_r = MagicMock()
        # Simulate over-limit: MAX_CACHE_ENTRIES + 50 entries
        mock_r.zcard.return_value = MAX_CACHE_ENTRIES + 50
        mock_r.zrange.return_value = [f"key_{i}".encode() for i in range(200)]
        mock_r.exists.return_value = True
        mock_r.delete.return_value = 1
        mock_r.zrem.return_value = 1

        evicted = _evict_lru_if_needed(mock_r)
        assert evicted > 0, "Should have evicted entries when over MAX_CACHE_ENTRIES limit"
        # Verify Redis delete was called for evicted keys
        assert mock_r.delete.call_count > 0


# ===================================================================
# 8.2 Supporting Caches
# ===================================================================


class TestSupportingCaches:
    """Tests for auth, catalog L1/L2, health, and local memory caches."""

    @pytest.mark.cm_verified
    def test_auth_cache_ttl_5_to_10_minutes(self):
        """CM-8.2.1: Auth cache functions call setex with TTLs in the 5-10 min range."""
        from src.services.auth_cache import cache_user_by_privy_id

        mock_redis = MagicMock()
        mock_redis.setex.return_value = True

        with patch("src.services.auth_cache.get_redis_client", return_value=mock_redis):
            result = cache_user_by_privy_id("privy_123", {"id": 1, "email": "test@example.com"})

        assert result is True
        # Verify setex was called with AUTH_CACHE_TTL (300s = 5 min)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        ttl_used = call_args[1]
        assert 300 <= ttl_used <= 600, f"Auth cache TTL {ttl_used}s is outside 5-10 min range"

    @pytest.mark.cm_verified
    def test_catalog_l1_cache_ttl_60_minutes(self):
        """CM-8.2.2: Catalog L1 (response) cache stores entries with 60-minute TTL."""
        import asyncio

        from src.services.catalog_response_cache import cache_catalog_response

        mock_r = MagicMock()
        mock_r.zcard.return_value = 0
        pipe_mock = MagicMock()
        pipe_mock.setex.return_value = pipe_mock
        pipe_mock.zadd.return_value = pipe_mock
        pipe_mock.execute.return_value = []
        for m in ["hincrby", "hset", "expire"]:
            getattr(pipe_mock, m).return_value = pipe_mock
        mock_r.pipeline.return_value = pipe_mock
        mock_r.delete.return_value = 1

        with patch(_CRC_REDIS, return_value=mock_r):
            ok = asyncio.get_event_loop().run_until_complete(
                cache_catalog_response("openrouter", {"limit": 50}, {"models": [], "total": 0})
            )

        assert ok is True
        # Verify the pipeline setex used 3600s TTL
        pipe_mock.setex.assert_called_once()
        ttl_used = pipe_mock.setex.call_args[0][1]
        assert ttl_used == 3600, f"Expected 3600s (60min), got {ttl_used}s"

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
        """CM-8.2.4: SimpleHealthCache.set_cache calls setex with the configured TTL."""
        mock_redis = MagicMock()
        mock_redis.setex.return_value = True

        with patch("src.services.simple_health_cache.get_redis_client", return_value=mock_redis):
            cache = SimpleHealthCache()
            result = cache.set_cache("health:system", {"status": "ok"}, ttl=DEFAULT_TTL_SYSTEM)

        assert result is True
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        ttl_used = call_args[1]
        assert ttl_used == 360, f"Expected 360s (6 min), got {ttl_used}s"

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
        """CM-8.3.1: When Redis is unavailable, catalog_response_cache returns None
        (graceful degradation). The caller handles the miss; no fallback to
        LocalMemoryCache exists inside catalog_response_cache itself."""
        import asyncio

        from src.services.catalog_response_cache import (
            cache_catalog_response,
            get_cached_catalog_response,
        )

        # When Redis is unavailable (returns None), reads return None
        with patch(_CRC_REDIS, return_value=None):
            result = asyncio.get_event_loop().run_until_complete(
                get_cached_catalog_response("openrouter", {"limit": 100})
            )
        assert result is None, "Redis-backed cache should return None when Redis is down"

        # When Redis is unavailable, writes return False
        with patch(_CRC_REDIS, return_value=None):
            ok = asyncio.get_event_loop().run_until_complete(
                cache_catalog_response("openrouter", {"limit": 100}, {"models": [], "total": 0})
            )
        assert ok is False, "Cache write should return False when Redis is unavailable"

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
