"""
Comprehensive tests for Ranking routes

Tests graceful degradation behavior when database is unavailable.
"""
import pytest
from unittest.mock import patch, MagicMock

from src.routes import ranking
from src.routes.ranking import (
    router,
    get_ranking_models,
    get_ranking_apps,
    _is_cache_valid,
    _update_cache,
    _get_cached_data,
)


class TestRankingRoutes:
    """Test Ranking route handlers"""

    def test_router_exists(self):
        """Test that router is defined"""
        assert router is not None
        assert hasattr(router, 'routes')

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.routes.ranking
        assert src.routes.ranking is not None


class TestCacheFunctions:
    """Test cache helper functions"""

    def test_cache_valid_empty(self):
        """Test that empty cache is invalid"""
        cache = {"data": None, "timestamp": 0, "ttl": 300}
        assert _is_cache_valid(cache) is False

    def test_cache_valid_fresh(self):
        """Test that fresh cache is valid"""
        import time
        cache = {"data": [{"id": 1}], "timestamp": time.time(), "ttl": 300}
        assert _is_cache_valid(cache) is True

    def test_cache_valid_stale(self):
        """Test that stale cache is invalid"""
        import time
        cache = {"data": [{"id": 1}], "timestamp": time.time() - 400, "ttl": 300}
        assert _is_cache_valid(cache) is False

    def test_update_cache(self):
        """Test cache update"""
        cache = {"data": None, "timestamp": 0, "ttl": 300}
        _update_cache(cache, [{"id": 1}])
        assert cache["data"] == [{"id": 1}]
        assert cache["timestamp"] > 0

    def test_get_cached_data(self):
        """Test getting cached data"""
        cache = {"data": [{"id": 1}], "timestamp": 100, "ttl": 300}
        assert _get_cached_data(cache) == [{"id": 1}]

    def test_get_cached_data_empty(self):
        """Test getting cached data when empty"""
        cache = {"data": None, "timestamp": 0, "ttl": 300}
        assert _get_cached_data(cache) is None


class TestRankingModelsGracefulDegradation:
    """Test graceful degradation for /ranking/models endpoint"""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test"""
        ranking._models_cache = {"data": None, "timestamp": 0, "ttl": 300}
        yield
        ranking._models_cache = {"data": None, "timestamp": 0, "ttl": 300}

    @pytest.mark.asyncio
    async def test_database_available_returns_fresh_data(self):
        """When database is available, return fresh data"""
        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.return_value = [{"id": "1", "name": "model1", "rank": 1}]
            result = await get_ranking_models(limit=None, offset=0)

            assert result["success"] is True
            assert result["source"] == "database"
            assert len(result["data"]) == 1
            assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_database_unavailable_with_cache_returns_cached(self):
        """When database is unavailable but cache exists, return cached data"""
        # Populate cache first
        _update_cache(ranking._models_cache, [{"id": "1", "name": "model1"}])

        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.side_effect = RuntimeError("Supabase unavailable")
            result = await get_ranking_models(limit=None, offset=0)

            assert result["success"] is True
            assert result["source"] == "cache"
            assert result["database_available"] is False
            assert len(result["data"]) == 1
            assert "cache_age_seconds" in result

    @pytest.mark.asyncio
    async def test_database_unavailable_no_cache_returns_empty(self):
        """When database is unavailable and no cache, return empty list with success=True"""
        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.side_effect = RuntimeError("Supabase unavailable")
            result = await get_ranking_models(limit=None, offset=0)

            # Key: should return success=True even when no data
            assert result["success"] is True
            assert result["source"] == "none"
            assert result["database_available"] is False
            assert len(result["data"]) == 0
            assert "message" in result

    @pytest.mark.asyncio
    async def test_unexpected_error_with_cache_returns_cached(self):
        """When unexpected error occurs but cache exists, return cached data"""
        _update_cache(ranking._models_cache, [{"id": "2", "name": "model2"}])

        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.side_effect = Exception("Unexpected error")
            result = await get_ranking_models(limit=None, offset=0)

            assert result["success"] is True
            assert result["source"] == "cache"
            assert result["error_occurred"] is True

    @pytest.mark.asyncio
    async def test_unexpected_error_no_cache_returns_empty(self):
        """When unexpected error occurs and no cache, return empty list"""
        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.side_effect = Exception("Unexpected error")
            result = await get_ranking_models(limit=None, offset=0)

            assert result["success"] is True
            assert result["source"] == "none"
            assert result["error_occurred"] is True
            assert len(result["data"]) == 0

    @pytest.mark.asyncio
    async def test_pagination_from_cache(self):
        """Test that pagination works on cached data"""
        _update_cache(
            ranking._models_cache,
            [{"id": str(i), "rank": i} for i in range(10)]
        )

        with patch("src.routes.ranking.get_all_latest_models") as mock_get:
            mock_get.side_effect = RuntimeError("Supabase unavailable")
            result = await get_ranking_models(limit=3, offset=2)

            assert result["success"] is True
            assert len(result["data"]) == 3
            assert result["data"][0]["id"] == "2"


class TestRankingAppsGracefulDegradation:
    """Test graceful degradation for /ranking/apps endpoint"""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test"""
        ranking._apps_cache = {"data": None, "timestamp": 0, "ttl": 300}
        yield
        ranking._apps_cache = {"data": None, "timestamp": 0, "ttl": 300}

    @pytest.mark.asyncio
    async def test_database_available_returns_fresh_data(self):
        """When database is available, return fresh app data"""
        with patch("src.routes.ranking.get_all_latest_apps") as mock_get:
            mock_get.return_value = [{"id": "1", "name": "app1"}]
            result = await get_ranking_apps()

            assert result["success"] is True
            assert result["source"] == "database"
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_database_unavailable_no_cache_returns_empty(self):
        """When database is unavailable and no cache, return empty list"""
        with patch("src.routes.ranking.get_all_latest_apps") as mock_get:
            mock_get.side_effect = RuntimeError("Supabase unavailable")
            result = await get_ranking_apps()

            assert result["success"] is True
            assert result["source"] == "none"
            assert len(result["data"]) == 0
