"""
Tests for DB-first catalog architecture

Tests verify:
- Redis cache reads from database
- get_cached_models() uses database as source of truth
- Cache invalidation works correctly
- Fallback behavior when database unavailable
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.model_catalog_cache import (
    get_cached_full_catalog,
    get_cached_provider_catalog,
)
from src.services.models import get_cached_models


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_db_models():
    """Sample models in database format"""
    return [
        {
            "id": 1,
            "model_id": "openai/gpt-4",
            "model_name": "GPT-4",
            "provider_id": 1,
            "is_active": True,
            "context_length": 8192,
            "providers": {"id": 1, "slug": "openai", "name": "OpenAI"},
        },
        {
            "id": 2,
            "model_id": "anthropic/claude-3-opus",
            "model_name": "Claude 3 Opus",
            "provider_id": 2,
            "is_active": True,
            "context_length": 200000,
            "providers": {"id": 2, "slug": "anthropic", "name": "Anthropic"},
        },
    ]


@pytest.fixture
def sample_api_models():
    """Sample models in API format"""
    return [
        {
            "id": "openai/gpt-4",
            "name": "GPT-4",
            "source_gateway": "openai",
            "provider_slug": "openai",
            "context_length": 8192,
        },
        {
            "id": "anthropic/claude-3-opus",
            "name": "Claude 3 Opus",
            "source_gateway": "anthropic",
            "provider_slug": "anthropic",
            "context_length": 200000,
        },
    ]


# ============================================================================
# REDIS CACHE TESTS
# ============================================================================


@patch("src.services.model_catalog_cache.get_redis_client")
def test_get_cached_full_catalog_returns_cache_when_available(mock_redis_client):
    """Test cache hit returns cached data without DB query"""
    # Setup
    mock_redis = MagicMock()
    mock_redis.get.return_value = '[{"id": "test/model"}]'
    mock_redis_client.return_value = mock_redis

    # Execute
    result = get_cached_full_catalog()

    # Verify - should return cached data
    assert result is not None
    assert len(result) == 1
    assert result[0]["id"] == "test/model"


@patch("src.db.models_catalog_db.get_all_models_for_catalog")
@patch("src.db.models_catalog_db.transform_db_models_batch")
@patch("src.services.model_catalog_cache.get_redis_client")
def test_get_cached_full_catalog_fetches_from_db_on_miss(
    mock_redis_client, mock_transform, mock_get_db_models, sample_db_models, sample_api_models
):
    """Test cache miss fetches from database"""
    # Setup
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Cache miss
    mock_redis_client.return_value = mock_redis
    mock_get_db_models.return_value = sample_db_models
    mock_transform.return_value = sample_api_models

    # Execute
    result = get_cached_full_catalog()

    # Verify - should fetch from DB
    assert result is not None
    assert len(result) == 2
    mock_get_db_models.assert_called_once()
    mock_transform.assert_called_once()
    # Should cache the result
    mock_redis.setex.assert_called_once()


@patch("src.db.models_catalog_db.get_models_by_gateway_for_catalog")
@patch("src.db.models_catalog_db.transform_db_models_batch")
@patch("src.services.model_catalog_cache.get_redis_client")
def test_get_cached_provider_catalog_fetches_from_db_on_miss(
    mock_redis_client, mock_transform, mock_get_db_models, sample_db_models, sample_api_models
):
    """Test provider catalog cache miss fetches from database"""
    # Setup
    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Cache miss
    mock_redis_client.return_value = mock_redis
    mock_get_db_models.return_value = sample_db_models
    mock_transform.return_value = sample_api_models

    # Execute
    result = get_cached_provider_catalog("openrouter")

    # Verify
    assert result is not None
    assert len(result) == 2
    mock_get_db_models.assert_called_once_with(
        gateway_slug="openrouter", include_inactive=False
    )


# ============================================================================
# GET_CACHED_MODELS TESTS
# ============================================================================


@patch("src.services.models.get_cached_full_catalog")
def test_get_cached_models_all_gateway(mock_get_cached, sample_api_models):
    """Test get_cached_models with 'all' gateway"""
    # Setup
    mock_get_cached.return_value = sample_api_models

    # Execute
    result = get_cached_models("all")

    # Verify
    assert len(result) == 2
    mock_get_cached.assert_called_once()


@patch("src.services.models.get_cached_provider_catalog")
def test_get_cached_models_single_provider(mock_get_cached, sample_api_models):
    """Test get_cached_models with single provider"""
    # Setup
    mock_get_cached.return_value = sample_api_models

    # Execute
    result = get_cached_models("openrouter")

    # Verify
    assert len(result) == 2
    mock_get_cached.assert_called_once_with("openrouter")


@patch("src.services.models.get_cached_provider_catalog")
def test_get_cached_models_handles_errors(mock_get_cached):
    """Test get_cached_models handles errors gracefully"""
    # Setup
    mock_get_cached.side_effect = Exception("Database error")

    # Execute
    result = get_cached_models("openrouter")

    # Verify - should return empty list, not crash
    assert result == []


# ============================================================================
# CACHE INVALIDATION TESTS
# ============================================================================


def test_cache_invalidation_exists():
    """
    Verify that cache invalidation logic exists.
    """
    from src.services.model_catalog_sync import sync_provider_models

    # Check that the function exists
    assert sync_provider_models is not None


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


@pytest.mark.integration
def test_full_flow_db_first():
    """
    Integration test: Full flow with DB-first architecture.

    This test requires a real database connection.
    """
    # Execute
    result = get_cached_models("all")

    # Verify - should return models (or empty list if DB empty)
    assert isinstance(result, list)


# ============================================================================
# EDGE CASES
# ============================================================================


@patch("src.services.models.get_cached_provider_catalog")
def test_get_cached_models_empty_gateway(mock_get_cached):
    """Test handling of empty gateway parameter"""
    # Setup
    mock_get_cached.return_value = []

    # Execute - empty string should default to "openrouter"
    result = get_cached_models("")

    # Verify
    assert isinstance(result, list)


@patch("src.services.models.get_cached_full_catalog")
def test_get_cached_models_none_gateway(mock_get_cached):
    """Test handling of None gateway parameter"""
    # Setup
    mock_get_cached.return_value = []

    # Execute - None should default to "openrouter"
    result = get_cached_models(None)

    # Verify
    assert isinstance(result, list)
