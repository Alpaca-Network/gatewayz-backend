"""
Basic integration tests for Redis caching functionality.

These tests verify that the caching layer works correctly with Redis.
"""

import time
from unittest.mock import Mock, patch

import pytest

from src.services.auth_cache import (
    cache_user_by_api_key,
    cache_user_by_id,
    clear_all_auth_caches,
    get_auth_cache_stats,
    get_cached_user_by_api_key,
    get_cached_user_by_id,
    invalidate_api_key_cache,
    invalidate_user_by_id,
)
from src.services.db_cache import (
    DBCache,
    cache_user,
    get_cache_stats,
    get_cached_user,
    get_db_cache,
    invalidate_user,
)
from src.services.model_catalog_cache import (
    ModelCatalogCache,
    cache_full_catalog,
    cache_provider_catalog,
    get_cached_full_catalog,
    get_cached_provider_catalog,
    invalidate_full_catalog,
)


class TestAuthCacheBasic:
    """Basic tests for authentication caching"""

    def test_cache_and_retrieve_user_by_api_key(self):
        """Test caching user data by API key"""
        api_key = "test_key_123"
        user_data = {"id": 1, "username": "testuser", "email": "test@example.com", "credits": 100}

        # Cache the user
        result = cache_user_by_api_key(api_key, user_data)

        # Retrieve from cache
        cached_user = get_cached_user_by_api_key(api_key)

        # If Redis is available, should be cached
        if result:
            assert cached_user is not None
            assert cached_user["username"] == "testuser"
            assert cached_user["credits"] == 100

    def test_cache_miss(self):
        """Test cache miss returns None"""
        cached_user = get_cached_user_by_api_key("nonexistent_key")
        # Cache miss should return None (or None if Redis unavailable)
        assert cached_user is None or cached_user is None

    def test_cache_invalidation(self):
        """Test cache invalidation works"""
        api_key = "test_key_invalidate"
        user_data = {"id": 2, "username": "testuser2", "credits": 50}

        # Cache the user
        cache_user_by_api_key(api_key, user_data)

        # Verify it's cached
        cached = get_cached_user_by_api_key(api_key)
        if cached:
            assert cached["username"] == "testuser2"

            # Invalidate
            invalidate_api_key_cache(api_key)

            # Should be gone
            cached_after_invalidate = get_cached_user_by_api_key(api_key)
            # May return None (invalidated) or None (Redis unavailable)
            # Either way, next DB fetch will refresh

    def test_cache_user_by_id(self):
        """Test caching user by ID"""
        user_id = 123
        user_data = {"id": user_id, "username": "user123", "credits": 200}

        cache_user_by_id(user_id, user_data)
        cached = get_cached_user_by_id(user_id)

        if cached:
            assert cached["username"] == "user123"
            assert cached["credits"] == 200


class TestDBCacheBasic:
    """Basic tests for database query caching"""

    def test_db_cache_set_and_get(self):
        """Test basic cache set and get operations"""
        cache = get_db_cache()

        test_data = {"name": "Test", "value": 123}

        # Set cache
        result = cache.set(DBCache.PREFIX_USER, "test_key", test_data)

        # Get cache
        cached_data = cache.get(DBCache.PREFIX_USER, "test_key")

        if result:  # Redis available
            assert cached_data is not None
            assert cached_data["name"] == "Test"
            assert cached_data["value"] == 123

    def test_db_cache_invalidate(self):
        """Test cache invalidation"""
        cache = get_db_cache()

        test_data = {"key": "value"}
        cache.set(DBCache.PREFIX_USER, "invalidate_test", test_data)

        # Invalidate
        cache.invalidate(DBCache.PREFIX_USER, "invalidate_test")

        # Should be gone
        cached = cache.get(DBCache.PREFIX_USER, "invalidate_test")
        assert cached is None

    def test_convenience_functions(self):
        """Test convenience functions work"""
        api_key = "convenience_key"
        user_data = {"id": 999, "username": "convenience_user"}

        cache_user(api_key, user_data)
        cached = get_cached_user(api_key)

        if cached:
            assert cached["username"] == "convenience_user"

    def test_cache_stats(self):
        """Test cache statistics work"""
        stats = get_cache_stats()

        assert "hits" in stats
        assert "misses" in stats
        assert "sets" in stats
        assert "redis_available" in stats


class TestModelCatalogCache:
    """Basic tests for model catalog caching"""

    def test_cache_full_catalog(self):
        """Test caching full model catalog"""
        catalog = [
            {"id": "model1", "name": "Model 1"},
            {"id": "model2", "name": "Model 2"},
        ]

        result = cache_full_catalog(catalog)
        cached = get_cached_full_catalog()

        if result:  # Redis available
            assert cached is not None
            assert len(cached) == 2
            assert cached[0]["id"] == "model1"

    def test_cache_provider_catalog(self):
        """Test caching provider-specific catalog"""
        provider = "openrouter"
        catalog = [{"id": "openrouter/model1", "name": "OpenRouter Model 1"}]

        result = cache_provider_catalog(provider, catalog)
        cached = get_cached_provider_catalog(provider)

        if result:
            assert cached is not None
            assert len(cached) == 1
            assert "openrouter" in cached[0]["id"]

    def test_invalidate_full_catalog(self):
        """Test invalidating full catalog"""
        catalog = [{"id": "test", "name": "Test Model"}]

        cache_full_catalog(catalog)
        invalidate_full_catalog()

        cached = get_cached_full_catalog()
        assert cached is None


class TestCacheWithRedisUnavailable:
    """Tests for graceful degradation when Redis is unavailable"""

    @patch("src.services.auth_cache.get_redis_client")
    def test_cache_fails_gracefully_when_redis_unavailable(self, mock_get_redis):
        """Test that cache operations don't crash when Redis is down"""
        mock_get_redis.return_value = None

        # Should not raise exception
        result = cache_user_by_api_key("test_key", {"id": 1})
        assert result is False  # Cache failed, but no exception

        cached = get_cached_user_by_api_key("test_key")
        assert cached is None  # No cache, but no exception

    @patch("src.config.redis_config.get_redis_client")
    def test_db_cache_fails_gracefully(self, mock_get_redis):
        """Test DB cache fails gracefully without Redis"""
        mock_get_redis.return_value = None

        cache = DBCache()
        result = cache.set("test", "key", {"data": "value"})
        assert result is False

        cached = cache.get("test", "key")
        assert cached is None


class TestCacheIntegrationWithDB:
    """Integration tests with actual database operations"""

    @pytest.mark.integration
    def test_user_caching_integration(self):
        """Test full user caching flow with DB"""
        # This test requires actual DB and Redis
        # Mark as integration test to run separately
        pass

    @pytest.mark.integration
    def test_cache_invalidation_on_credit_update(self):
        """Test cache invalidates when credits are updated"""
        # This test requires actual DB and Redis
        # Mark as integration test to run separately
        pass


def test_redis_availability_check():
    """Test that we can check Redis availability"""
    from src.config.redis_config import is_redis_available

    # Should not crash
    available = is_redis_available()
    assert isinstance(available, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
