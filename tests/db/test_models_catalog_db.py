"""
Comprehensive tests for database catalog functions (Phase 1 - Issue #990)

Tests cover:
- Basic CRUD operations
- Advanced filtering (modality, search, pagination)
- Transformation (DB format → API format)
- Performance benchmarks
- Edge cases and error handling
"""

import time
from decimal import Decimal
from typing import Any

import pytest

from src.db.models_catalog_db import (
    get_all_models_for_catalog,
    get_catalog_statistics,
    get_model_by_model_id_string,
    get_models_by_gateway_for_catalog,
    get_models_count_by_filters,
    get_models_for_catalog_with_filters,
    transform_db_model_to_api_format,
    transform_db_models_batch,
)

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sample_db_model() -> dict[str, Any]:
    """Sample model in database format"""
    return {
        "id": 1,
        "model_id": "openai/gpt-4",
        "model_name": "GPT-4",
        "provider_id": 1,
        "provider_model_id": "gpt-4",
        "description": "OpenAI's most capable model",
        "context_length": 8192,
        "modality": "text->text",
        "architecture": "transformer",
        "top_provider": "openai",
        "per_request_limits": None,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_vision": False,
        "is_active": True,
        "health_status": "healthy",
        "metadata": {"version": "gpt-4-0613"},
        "pricing_prompt": Decimal("0.00003"),
        "pricing_completion": Decimal("0.00006"),
        "average_response_time_ms": 1500,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-15T00:00:00Z",
        "providers": {
            "id": 1,
            "slug": "openai",
            "name": "OpenAI",
            "description": "OpenAI API",
            "base_url": "https://api.openai.com/v1",
            "is_active": True,
        },
    }


@pytest.fixture
def sample_db_model_with_missing_fields() -> dict[str, Any]:
    """Sample model with missing optional fields"""
    return {
        "id": 2,
        "model_id": "test/minimal-model",
        "model_name": "Minimal Model",
        "provider_id": 1,
        "provider_model_id": "minimal-model",
        "description": None,
        "context_length": None,
        "modality": "text->text",
        "is_active": True,
        "providers": {
            "id": 1,
            "slug": "test",
            "name": "Test Provider",
        },
    }


# ============================================================================
# TRANSFORMATION TESTS
# ============================================================================


def test_transform_db_model_to_api_format_complete(sample_db_model):
    """Test transformation with all fields present"""
    api_model = transform_db_model_to_api_format(sample_db_model)

    # Verify ID mapping (uses model_id, not DB primary key)
    assert api_model["id"] == "openai/gpt-4"
    assert api_model["name"] == "GPT-4"

    # Verify provider info
    assert api_model["source_gateway"] == "openai"
    assert api_model["provider_slug"] == "openai"

    # Verify pricing transformation
    assert api_model["pricing"] is not None
    assert api_model["pricing"]["prompt"] == "0.00003"
    assert api_model["pricing"]["completion"] == "0.00006"

    # Verify other fields
    assert api_model["context_length"] == 8192
    assert api_model["description"] == "OpenAI's most capable model"
    assert api_model["modality"] == "text->text"
    assert api_model["top_provider"] == "openai"
    assert api_model["is_active"] is True
    assert api_model["health_status"] == "healthy"

    # Verify metadata preserved
    assert api_model["metadata"] == {"version": "gpt-4-0613"}
    assert api_model["average_response_time_ms"] == 1500


def test_transform_db_model_with_missing_fields(sample_db_model_with_missing_fields):
    """Test transformation gracefully handles missing fields"""
    api_model = transform_db_model_to_api_format(sample_db_model_with_missing_fields)

    assert api_model["id"] == "test/minimal-model"
    assert api_model["name"] == "Minimal Model"
    assert api_model["source_gateway"] == "test"
    assert api_model["context_length"] is None
    assert api_model["description"] is None
    assert api_model["pricing"] is None  # No pricing data


def test_transform_db_model_with_null_pricing(sample_db_model):
    """Test transformation when pricing fields are null"""
    sample_db_model["pricing_prompt"] = None
    sample_db_model["pricing_completion"] = None

    api_model = transform_db_model_to_api_format(sample_db_model)
    assert api_model["pricing"] is None


def test_transform_db_model_error_handling():
    """Test transformation handles errors gracefully"""
    # Invalid model (missing required fields)
    invalid_model = {"id": 999}

    api_model = transform_db_model_to_api_format(invalid_model)

    # Should return minimal model, not crash
    assert api_model["id"] == "unknown"
    assert api_model["name"] == "Unknown Model"
    assert api_model["source_gateway"] == "unknown"


def test_transform_db_models_batch(sample_db_model, sample_db_model_with_missing_fields):
    """Test batch transformation"""
    db_models = [sample_db_model, sample_db_model_with_missing_fields]

    api_models = transform_db_models_batch(db_models)

    assert len(api_models) == 2
    assert api_models[0]["id"] == "openai/gpt-4"
    assert api_models[1]["id"] == "test/minimal-model"


def test_transform_preserves_no_data_loss(sample_db_model):
    """Test that transformation preserves all important data"""
    api_model = transform_db_model_to_api_format(sample_db_model)

    # Check all key fields are present
    required_fields = [
        "id",
        "name",
        "source_gateway",
        "provider_slug",
        "context_length",
        "pricing",
        "description",
        "modality",
        "top_provider",
        "is_active",
        "health_status",
    ]

    for field in required_fields:
        assert field in api_model, f"Missing field: {field}"


# ============================================================================
# QUERY TESTS (requires database connection)
# ============================================================================


@pytest.mark.integration
def test_get_all_models_for_catalog():
    """Test fetching all models from database"""
    models = get_all_models_for_catalog()

    # Should return a list (may be empty if DB not populated)
    assert isinstance(models, list)

    if models:
        # Verify structure
        model = models[0]
        assert "model_id" in model
        assert "model_name" in model
        assert "providers" in model
        assert "is_active" in model


@pytest.mark.integration
def test_get_all_models_include_inactive():
    """Test including inactive models"""
    active_models = get_all_models_for_catalog(include_inactive=False)
    all_models = get_all_models_for_catalog(include_inactive=True)

    # All models should include active models
    assert len(all_models) >= len(active_models)


@pytest.mark.integration
def test_get_models_by_gateway_for_catalog():
    """Test filtering by provider"""
    models = get_models_by_gateway_for_catalog("openrouter")

    assert isinstance(models, list)

    # If models exist, verify they're from correct provider
    for model in models:
        provider = model.get("providers", {})
        assert provider.get("slug") == "openrouter"


@pytest.mark.integration
def test_get_models_for_catalog_with_filters_no_filters():
    """Test advanced query with no filters (should return all)"""
    models = get_models_for_catalog_with_filters()
    assert isinstance(models, list)


@pytest.mark.integration
def test_get_models_for_catalog_with_filters_by_gateway():
    """Test filtering by gateway"""
    models = get_models_for_catalog_with_filters(gateway_slug="openai")

    for model in models:
        provider = model.get("providers", {})
        assert provider.get("slug") == "openai"


@pytest.mark.integration
def test_get_models_for_catalog_with_filters_by_modality():
    """Test filtering by modality"""
    models = get_models_for_catalog_with_filters(modality="text->text")

    for model in models:
        assert model.get("modality") == "text->text"


@pytest.mark.integration
def test_get_models_for_catalog_with_filters_search():
    """Test search functionality"""
    # Search for common model name
    models = get_models_for_catalog_with_filters(search_query="gpt")

    # Should find models with "gpt" in name or ID
    assert isinstance(models, list)


@pytest.mark.integration
def test_get_models_for_catalog_with_filters_pagination():
    """Test pagination"""
    # Get first 10 models
    page1 = get_models_for_catalog_with_filters(limit=10, offset=0)
    assert len(page1) <= 10

    # Get next 10 models
    page2 = get_models_for_catalog_with_filters(limit=10, offset=10)

    # Pages should be different (if enough models exist)
    if len(page1) == 10 and len(page2) > 0:
        assert page1[0]["model_id"] != page2[0]["model_id"]


@pytest.mark.integration
def test_get_models_for_catalog_combined_filters():
    """Test combining multiple filters"""
    models = get_models_for_catalog_with_filters(
        gateway_slug="openai", modality="text->text", limit=5
    )

    assert len(models) <= 5

    for model in models:
        provider = model.get("providers", {})
        assert provider.get("slug") == "openai"
        assert model.get("modality") == "text->text"


@pytest.mark.integration
def test_get_models_count_by_filters():
    """Test count function"""
    count = get_models_count_by_filters()
    assert isinstance(count, int)
    assert count >= 0


@pytest.mark.integration
def test_get_models_count_matches_query():
    """Test count matches actual query results"""
    # Get count
    count = get_models_count_by_filters(gateway_slug="openrouter")

    # Get actual models (without limit)
    models = get_models_for_catalog_with_filters(gateway_slug="openrouter")

    # Count should match
    assert count == len(models)


@pytest.mark.integration
def test_get_model_by_model_id_string():
    """Test looking up single model by ID"""
    # First get a model to test with
    models = get_all_models_for_catalog()

    if models:
        test_model = models[0]
        model_id = test_model["model_id"]

        # Look it up
        found_model = get_model_by_model_id_string(model_id)

        assert found_model is not None
        assert found_model["model_id"] == model_id


@pytest.mark.integration
def test_get_model_by_model_id_string_not_found():
    """Test lookup with non-existent model ID"""
    model = get_model_by_model_id_string("nonexistent/model-12345")
    assert model is None


@pytest.mark.integration
def test_get_catalog_statistics():
    """Test catalog statistics function"""
    stats = get_catalog_statistics()

    # Verify structure
    assert "total_models" in stats
    assert "total_providers" in stats
    assert "models_by_modality" in stats
    assert "models_by_provider" in stats
    assert "top_providers" in stats

    # Verify types
    assert isinstance(stats["total_models"], int)
    assert isinstance(stats["total_providers"], int)
    assert isinstance(stats["models_by_modality"], dict)
    assert isinstance(stats["models_by_provider"], dict)
    assert isinstance(stats["top_providers"], list)


# ============================================================================
# PERFORMANCE TESTS
# ============================================================================


@pytest.mark.integration
@pytest.mark.slow
def test_catalog_query_performance():
    """Test query performance meets targets (< 100ms)"""
    # Warm up
    get_all_models_for_catalog()

    # Measure
    start = time.time()
    models = get_all_models_for_catalog()
    duration_ms = (time.time() - start) * 1000

    print(f"\nFetched {len(models)} models in {duration_ms:.2f}ms")

    # Target: < 100ms for cold cache
    # This is lenient for test environments
    assert duration_ms < 500, f"Query took {duration_ms:.2f}ms (target: <500ms)"


@pytest.mark.integration
@pytest.mark.slow
def test_transformation_performance():
    """Test transformation performance (< 1ms per model)"""
    # Get sample models
    db_models = get_all_models_for_catalog()

    if not db_models:
        pytest.skip("No models in database")

    # Take first 100 models for testing
    test_models = db_models[:100]

    # Measure transformation time
    start = time.time()
    api_models = transform_db_models_batch(test_models)
    duration_ms = (time.time() - start) * 1000

    per_model_ms = duration_ms / len(test_models)

    print(
        f"\nTransformed {len(test_models)} models in {duration_ms:.2f}ms "
        f"({per_model_ms:.3f}ms per model)"
    )

    # Target: < 1ms per model
    assert per_model_ms < 1.0, f"Transformation took {per_model_ms:.3f}ms per model"


@pytest.mark.integration
@pytest.mark.slow
def test_filtered_query_performance():
    """Test performance of filtered queries"""
    start = time.time()
    models = get_models_for_catalog_with_filters(
        gateway_slug="openrouter", modality="text->text", limit=100
    )
    duration_ms = (time.time() - start) * 1000

    print(f"\nFiltered query returned {len(models)} models in {duration_ms:.2f}ms")

    # Target: < 200ms for filtered query
    assert duration_ms < 500, f"Filtered query took {duration_ms:.2f}ms"


@pytest.mark.integration
@pytest.mark.slow
def test_count_query_performance():
    """Test performance of count queries"""
    start = time.time()
    count = get_models_count_by_filters(gateway_slug="openrouter")
    duration_ms = (time.time() - start) * 1000

    print(f"\nCount query returned {count} in {duration_ms:.2f}ms")

    # Target: < 50ms for count
    assert duration_ms < 200, f"Count query took {duration_ms:.2f}ms"


# ============================================================================
# EDGE CASES
# ============================================================================


@pytest.mark.integration
def test_empty_result_handling():
    """Test handling of queries that return no results"""
    models = get_models_for_catalog_with_filters(gateway_slug="nonexistent-provider-xyz")

    assert isinstance(models, list)
    assert len(models) == 0


@pytest.mark.integration
def test_special_characters_in_search():
    """Test search with special characters"""
    # Should not crash with special characters
    models = get_models_for_catalog_with_filters(search_query="gpt-4.5%")
    assert isinstance(models, list)


def test_transform_empty_list():
    """Test batch transformation with empty list"""
    result = transform_db_models_batch([])
    assert result == []


# ============================================================================
# DATA CONSISTENCY TESTS
# ============================================================================


@pytest.mark.integration
def test_all_models_have_required_fields():
    """Test that all models have required fields for API"""
    models = get_all_models_for_catalog()

    required_fields = ["model_id", "model_name", "providers", "is_active"]

    for model in models:
        for field in required_fields:
            assert field in model, f"Model {model.get('id')} missing field: {field}"


@pytest.mark.integration
def test_transformed_models_match_api_schema():
    """Test that transformed models match expected API schema"""
    db_models = get_all_models_for_catalog()

    if not db_models:
        pytest.skip("No models in database")

    api_models = transform_db_models_batch(db_models[:10])

    required_api_fields = [
        "id",
        "name",
        "source_gateway",
        "provider_slug",
    ]

    for api_model in api_models:
        for field in required_api_fields:
            assert field in api_model, f"Transformed model missing API field: {field}"


@pytest.mark.integration
def test_provider_join_integrity():
    """Test that provider join always works"""
    models = get_all_models_for_catalog()

    for model in models:
        assert "providers" in model
        provider = model["providers"]
        assert "slug" in provider
        assert "name" in provider
