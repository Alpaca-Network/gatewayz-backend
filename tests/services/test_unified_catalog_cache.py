"""
Test unified Redis-based catalog caching system

Tests the enhanced ModelCatalogCache class with gateway, stats, and unique models caching.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.model_catalog_cache import (
    ModelCatalogCache,
    get_model_catalog_cache,
    get_cached_gateway_catalog,
    get_cached_unique_models,
    cache_gateway_catalog,
    cache_unique_models,
    invalidate_gateway_catalog,
    invalidate_unique_models,
    get_cached_catalog_stats,
    cache_catalog_stats,
    invalidate_catalog_stats,
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
        mock_cache.invalidate_gateway_catalog.assert_called_once_with("openrouter")

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
        assert any(ttl >= 900 for ttl in ttls)   # 15 minutes

    def test_cache_invalidation_cascade(self, mock_redis, mock_redis_available):
        """Test that invalidating a gateway catalog also invalidates full catalog"""
        cache = ModelCatalogCache()

        # Invalidate a gateway catalog
        cache.invalidate_gateway_catalog("openrouter")

        # Should delete both gateway and full catalog
        assert mock_redis.delete.call_count >= 1
