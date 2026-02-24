"""
Test unified Redis-based catalog caching system

Tests the enhanced ModelCatalogCache class with gateway, stats, and unique models caching.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.model_catalog_cache import (
    ModelCatalogCache,
    cache_catalog_stats,
    cache_gateway_catalog,
    cache_unique_models,
    get_cached_catalog_stats,
    get_cached_gateway_catalog,
    get_cached_unique_models,
    get_model_catalog_cache,
    invalidate_catalog_stats,
    invalidate_gateway_catalog,
    invalidate_unique_models,
)


@pytest.fixture
def mock_redis():
    """Mock Redis client"""
    with patch("src.services.model_catalog_cache.get_redis_client") as mock_client:
        redis_mock = MagicMock()
        mock_client.return_value = redis_mock
        yield redis_mock


@pytest.fixture
def mock_redis_available():
    """Mock Redis availability check"""
    with patch("src.services.model_catalog_cache.is_redis_available") as mock_available:
        mock_available.return_value = True
        yield mock_available


class TestModelCatalogCacheEnhancements:
    """Test enhanced ModelCatalogCache functionality"""

    def test_cache_gateway_catalog(self, mock_redis, mock_redis_available):
        """Test caching gateway-specific catalog"""
        cache = ModelCatalogCache()

        test_catalog = [
            {"id": "openrouter/gpt-4", "name": "GPT-4", "provider": "openrouter"},
            {"id": "openrouter/claude-3", "name": "Claude 3", "provider": "openrouter"},
        ]

        # Set gateway catalog
        result = cache.set_gateway_catalog("openrouter", test_catalog, ttl=1800)

        assert result is True
        assert mock_redis.setex.called
        # Verify the key format
        call_args = mock_redis.setex.call_args
        assert "openrouter" in str(call_args[0][0])  # Key should contain provider name
        assert call_args[0][1] == 1800  # TTL should be 1800

    def test_get_gateway_catalog_hit(self, mock_redis, mock_redis_available):
        """Test cache hit for gateway catalog"""
        import json

        cache = ModelCatalogCache()

        test_catalog = [{"id": "gpt-4", "name": "GPT-4"}]
        mock_redis.get.return_value = json.dumps(test_catalog)

        result = cache.get_gateway_catalog("openrouter")

        assert result == test_catalog
        assert cache._stats["hits"] == 1
        assert cache._stats["misses"] == 0

    def test_get_gateway_catalog_miss(self, mock_redis, mock_redis_available):
        """Test cache miss for gateway catalog"""
        cache = ModelCatalogCache()

        mock_redis.get.return_value = None

        result = cache.get_gateway_catalog("openrouter")

        assert result is None
        assert cache._stats["hits"] == 0
        assert cache._stats["misses"] == 1

    def test_invalidate_gateway_catalog(self, mock_redis, mock_redis_available):
        """Test gateway catalog invalidation"""
        cache = ModelCatalogCache()

        result = cache.invalidate_gateway_catalog("openrouter")

        assert result is True
        assert mock_redis.delete.called

    def test_cache_unique_models(self, mock_redis, mock_redis_available):
        """Test caching unique models"""
        cache = ModelCatalogCache()

        test_unique_models = [
            {
                "id": "gpt-4",
                "name": "GPT-4",
                "provider_count": 3,
                "providers": ["openrouter", "groq", "anthropic"],
            },
            {
                "id": "claude-3",
                "name": "Claude 3",
                "provider_count": 2,
                "providers": ["anthropic", "openrouter"],
            },
        ]

        result = cache.set_unique_models(test_unique_models, ttl=1800)

        assert result is True
        assert mock_redis.setex.called
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "models:unique"  # Correct key prefix
        assert call_args[0][1] == 1800  # TTL

    def test_get_unique_models(self, mock_redis, mock_redis_available):
        """Test retrieving cached unique models"""
        import json

        cache = ModelCatalogCache()

        test_unique_models = [{"id": "gpt-4", "provider_count": 3}]
        mock_redis.get.return_value = json.dumps(test_unique_models)

        result = cache.get_unique_models()

        assert result == test_unique_models
        assert cache._stats["hits"] == 1

    def test_cache_catalog_stats(self, mock_redis, mock_redis_available):
        """Test caching catalog statistics"""
        cache = ModelCatalogCache()

        test_stats = {
            "total_models": 500,
            "total_providers": 30,
            "unique_models": 234,
        }

        result = cache.set_catalog_stats(test_stats, ttl=900)

        assert result is True
        assert mock_redis.setex.called
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "models:stats"
        assert call_args[0][1] == 900

    def test_get_catalog_stats(self, mock_redis, mock_redis_available):
        """Test retrieving cached catalog statistics"""
        import json

        cache = ModelCatalogCache()

        test_stats = {"total_models": 500}
        mock_redis.get.return_value = json.dumps(test_stats)

        result = cache.get_catalog_stats()

        assert result == test_stats
        assert cache._stats["hits"] == 1

    def test_redis_unavailable_graceful_degradation(self):
        """Test graceful degradation when Redis is unavailable"""
        with patch("src.services.model_catalog_cache.is_redis_available") as mock_available:
            mock_available.return_value = False

            cache = ModelCatalogCache()

            # All operations should return None/False without errors
            assert cache.get_gateway_catalog("openrouter") is None
            assert cache.get_unique_models() is None
            assert cache.get_catalog_stats() is None
            assert cache.set_gateway_catalog("openrouter", []) is False
            assert cache.set_unique_models([]) is False
            assert cache.set_catalog_stats({}) is False

    def test_cache_stats_tracking(self, mock_redis, mock_redis_available):
        """Test that cache statistics are tracked correctly"""
        import json

        cache = ModelCatalogCache()

        # Cache hit
        mock_redis.get.return_value = json.dumps([{"id": "test"}])
        cache.get_gateway_catalog("openrouter")

        # Cache miss
        mock_redis.get.return_value = None
        cache.get_gateway_catalog("groq")

        # Cache set
        cache.set_gateway_catalog("anthropic", [])

        stats = cache.get_stats()

        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 1
        assert stats["hit_rate_percent"] > 0


class TestConvenienceFunctions:
    """Test convenience wrapper functions"""

    @patch("src.services.model_catalog_cache.get_model_catalog_cache")
    def test_cache_gateway_catalog_convenience(self, mock_get_cache):
        """Test convenience function for caching gateway catalog"""
        mock_cache = MagicMock()
        mock_cache.set_gateway_catalog.return_value = True
        mock_get_cache.return_value = mock_cache

        test_catalog = [{"id": "test"}]
        result = cache_gateway_catalog("openrouter", test_catalog, ttl=1800)

        assert result is True
        mock_cache.set_gateway_catalog.assert_called_once_with("openrouter", test_catalog, ttl=1800)

    @patch("src.services.model_catalog_cache.get_model_catalog_cache")
    def test_invalidate_gateway_catalog_convenience(self, mock_get_cache):
        """Test convenience function for invalidating gateway catalog"""
        mock_cache = MagicMock()
        mock_cache.invalidate_gateway_catalog.return_value = True
        mock_get_cache.return_value = mock_cache

        result = invalidate_gateway_catalog("openrouter")

        assert result is True
        mock_cache.invalidate_gateway_catalog.assert_called_once_with(
            "openrouter", cascade=False, debounce=False
        )

    @patch("src.services.model_catalog_cache.get_model_catalog_cache")
    def test_cache_unique_models_convenience(self, mock_get_cache):
        """Test convenience function for caching unique models"""
        mock_cache = MagicMock()
        mock_cache.set_unique_models.return_value = True
        mock_get_cache.return_value = mock_cache

        test_models = [{"id": "gpt-4", "provider_count": 3}]
        result = cache_unique_models(test_models, ttl=1800)

        assert result is True
        mock_cache.set_unique_models.assert_called_once_with(test_models, ttl=1800)

    @patch("src.services.model_catalog_cache.get_model_catalog_cache")
    def test_cache_catalog_stats_convenience(self, mock_get_cache):
        """Test convenience function for caching catalog stats"""
        mock_cache = MagicMock()
        mock_cache.set_catalog_stats.return_value = True
        mock_get_cache.return_value = mock_cache

        test_stats = {"total_models": 500}
        result = cache_catalog_stats(test_stats, ttl=900)

        assert result is True
        mock_cache.set_catalog_stats.assert_called_once_with(test_stats, ttl=900)


class TestBatchInvalidation:
    """Test batch invalidation with Redis pipeline (Issue #1098)"""

    def test_invalidate_providers_batch_success(self, mock_redis, mock_redis_available):
        """Test successful batch invalidation of multiple providers"""
        cache = ModelCatalogCache()

        # Mock pipeline
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute.return_value = [1, 1, 1]  # 3 keys deleted

        providers = ["openrouter", "groq", "anthropic"]
        result = cache.invalidate_providers_batch(providers, cascade=False)

        # Verify pipeline was created and used
        assert mock_redis.pipeline.called
        assert mock_pipeline.delete.call_count == 3
        assert mock_pipeline.execute.called

        # Verify result
        assert result["success"] is True
        assert result["providers_invalidated"] == 3
        assert result["keys_deleted"] == 3
        assert "duration_ms" in result
        assert result["cascade"] is False

    def test_invalidate_providers_batch_with_cascade(self, mock_redis, mock_redis_available):
        """Test batch invalidation with cascade to full catalog"""
        cache = ModelCatalogCache()

        # Mock pipeline
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute.return_value = [1, 1]  # 2 keys deleted

        providers = ["openrouter", "groq"]
        result = cache.invalidate_providers_batch(providers, cascade=True)

        # Verify cascade invalidation occurred
        assert result["success"] is True
        assert result["cascade"] is True
        # Should delete full catalog after provider deletions
        assert mock_redis.delete.call_count >= 1  # Full catalog invalidation

    def test_invalidate_providers_batch_empty_list(self, mock_redis, mock_redis_available):
        """Test batch invalidation with empty provider list"""
        cache = ModelCatalogCache()

        result = cache.invalidate_providers_batch([], cascade=False)

        # Should succeed but do nothing
        assert result["success"] is True
        assert result["providers_invalidated"] == 0
        assert result["keys_deleted"] == 0
        assert result["duration_ms"] == 0
        assert not mock_redis.pipeline.called

    def test_invalidate_providers_batch_redis_unavailable(self):
        """Test batch invalidation when Redis is unavailable"""
        with patch("src.services.model_catalog_cache.is_redis_available") as mock_available:
            mock_available.return_value = False

            cache = ModelCatalogCache()
            providers = ["openrouter", "groq"]
            result = cache.invalidate_providers_batch(providers, cascade=False)

            # Should fail gracefully
            assert result["success"] is False
            assert result["providers_invalidated"] == 0
            assert result["keys_deleted"] == 0
            assert "error" in result
            assert result["error"] == "Redis unavailable"

    def test_invalidate_providers_batch_pipeline_error(self, mock_redis, mock_redis_available):
        """Test batch invalidation when pipeline raises exception"""
        cache = ModelCatalogCache()

        # Mock pipeline to raise exception
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute.side_effect = Exception("Pipeline error")

        providers = ["openrouter", "groq"]
        result = cache.invalidate_providers_batch(providers, cascade=False)

        # Should fail with error details
        assert result["success"] is False
        assert result["providers_invalidated"] == 0
        assert result["keys_deleted"] == 0
        assert "error" in result
        assert "Pipeline error" in result["error"]
        assert "duration_ms" in result

    def test_invalidate_providers_batch_performance(self, mock_redis, mock_redis_available):
        """Test that batch invalidation uses single pipeline operation"""
        cache = ModelCatalogCache()

        # Mock pipeline
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute.return_value = [1] * 30  # 30 keys deleted

        # Simulate invalidating 30 providers (all gateways)
        providers = [f"provider-{i}" for i in range(30)]
        result = cache.invalidate_providers_batch(providers, cascade=False)

        # Should use only ONE pipeline call regardless of number of providers
        assert mock_redis.pipeline.call_count == 1
        assert mock_pipeline.execute.call_count == 1
        # Should queue all deletions in pipeline
        assert mock_pipeline.delete.call_count == 30

        assert result["success"] is True
        assert result["providers_invalidated"] == 30
        assert result["keys_deleted"] == 30


class TestCacheIntegration:
    """Integration tests for the unified caching system"""

    def test_cache_key_consistency(self, mock_redis, mock_redis_available):
        """Test that cache keys follow consistent naming convention"""
        cache = ModelCatalogCache()

        # Set different cache types
        cache.set_gateway_catalog("openrouter", [])
        cache.set_unique_models([])
        cache.set_catalog_stats({})

        # Check that keys follow the pattern
        calls = mock_redis.setex.call_args_list
        keys = [call[0][0] for call in calls]

        # Gateway catalog should use provider prefix
        assert any("openrouter" in key for key in keys)
        # Unique models should have unique prefix
        assert any("unique" in key for key in keys)
        # Stats should have stats prefix
        assert any("stats" in key for key in keys)

    def test_ttl_configuration(self, mock_redis, mock_redis_available):
        """Test that different cache types have appropriate TTLs"""
        cache = ModelCatalogCache()

        # Default TTLs
        cache.set_gateway_catalog("openrouter", [])
        cache.set_unique_models([])
        cache.set_catalog_stats({})

        calls = mock_redis.setex.call_args_list
        ttls = [call[0][1] for call in calls]

        # All TTLs should be positive integers
        assert all(isinstance(ttl, int) and ttl > 0 for ttl in ttls)
        # Gateway and unique should have longer TTLs than stats
        assert any(ttl >= 1800 for ttl in ttls)  # 30 minutes
        assert any(ttl >= 900 for ttl in ttls)  # 15 minutes

    def test_cache_invalidation_cascade(self, mock_redis, mock_redis_available):
        """Test that invalidating a gateway catalog also invalidates full catalog"""
        cache = ModelCatalogCache()

        # Invalidate a gateway catalog
        cache.invalidate_gateway_catalog("openrouter")

        # Should delete both gateway and full catalog
        assert mock_redis.delete.call_count >= 1
