"""
Basic integration test for unique models feature.

Tests the new unique_models parameter functionality across all layers:
- Database query
- Service layer caching
- Route layer API endpoint
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client."""
    from src.main import create_app

    app = create_app()
    return TestClient(app)


class TestUniqueModelsBasic:
    """Basic tests for unique models functionality."""

    def test_database_layer_get_unique_models(self):
        """Test database layer can fetch unique models."""
        from src.db.models_catalog_db import get_all_unique_models_for_catalog

        # Fetch unique models from database
        db_models = get_all_unique_models_for_catalog(include_inactive=False)

        # Should return a list
        assert isinstance(db_models, list), "Should return a list"

        # If we have data, verify structure
        if db_models:
            model = db_models[0]
            assert "unique_model_id" in model, "Should have unique_model_id"
            assert "model_name" in model, "Should have model_name"
            assert "providers" in model, "Should have providers array"
            assert isinstance(model["providers"], list), "Providers should be a list"

            # Verify provider structure if providers exist
            if model["providers"]:
                provider = model["providers"][0]
                assert "provider_slug" in provider, "Provider should have slug"
                assert "pricing_prompt" in provider, "Provider should have pricing"

    def test_database_layer_transformation(self):
        """Test database-to-API transformation."""
        from src.db.models_catalog_db import (
            get_all_unique_models_for_catalog,
            transform_unique_model_to_api_format,
        )

        # Get a model from database
        db_models = get_all_unique_models_for_catalog(include_inactive=False)

        if db_models:
            # Transform first model
            api_model = transform_unique_model_to_api_format(db_models[0])

            # Verify API format
            assert "id" in api_model, "Should have id"
            assert "name" in api_model, "Should have name"
            assert "providers" in api_model, "Should have providers array"
            assert "provider_count" in api_model, "Should have provider_count"
            assert "cheapest_provider" in api_model, "Should have cheapest_provider"
            assert "fastest_provider" in api_model, "Should have fastest_provider"

            # Verify provider_count matches array length
            assert api_model["provider_count"] == len(api_model["providers"])

    def test_service_layer_unique_models_cache(self):
        """Test service layer unique models caching."""
        from src.services.models import get_cached_unique_models_catalog

        # Fetch unique models via service layer
        models = get_cached_unique_models_catalog()

        # Should return a list
        assert isinstance(models, list), "Should return a list"

        # If we have models, verify API format
        if models:
            model = models[0]
            assert "id" in model, "Should have id"
            assert "providers" in model, "Should have providers array"
            assert isinstance(model["providers"], list), "Providers should be a list"

    def test_service_layer_get_cached_models_flat_mode(self):
        """Test backward compatibility - flat mode."""
        from src.services.models import get_cached_models

        # Fetch in flat mode (default)
        models = get_cached_models(gateway="all", use_unique_models=False)

        # Should return a list
        assert isinstance(models, list), "Should return a list"

    def test_service_layer_get_cached_models_unique_mode(self):
        """Test unique mode via get_cached_models."""
        from src.services.models import get_cached_models

        # Fetch in unique mode
        models = get_cached_models(gateway="all", use_unique_models=True)

        # Should return a list
        assert isinstance(models, list), "Should return a list"

        # If we have models, verify unique format
        if models:
            model = models[0]
            assert "providers" in model, "Should have providers array"
            assert "provider_count" in model, "Should have provider_count"

    def test_route_layer_default_behavior(self, client):
        """Test /models endpoint default behavior (backward compatibility)."""
        response = client.get("/models?gateway=all&limit=5")

        assert response.status_code == 200, f"Should return 200, got {response.status_code}"
        models = response.json()
        assert isinstance(models, list), "Should return a list"

    def test_route_layer_unique_models_false(self, client):
        """Test /models endpoint with unique_models=false."""
        response = client.get("/models?gateway=all&unique_models=false&limit=5")

        assert response.status_code == 200, f"Should return 200, got {response.status_code}"
        models = response.json()
        assert isinstance(models, list), "Should return a list"

    def test_route_layer_unique_models_true(self, client):
        """Test /models endpoint with unique_models=true."""
        response = client.get("/models?gateway=all&unique_models=true&limit=5")

        assert response.status_code == 200, f"Should return 200, got {response.status_code}"
        models = response.json()
        assert isinstance(models, list), "Should return a list"

        # If we have models, verify unique structure
        if models:
            model = models[0]
            assert "providers" in model, "Should have providers array"
            assert isinstance(model["providers"], list), "Providers should be a list"
            assert "provider_count" in model, "Should have provider_count"

    def test_route_layer_unique_models_with_specific_gateway(self, client):
        """Test that unique_models is ignored for provider-specific queries."""
        # Fetch with unique_models=true for a specific gateway
        response = client.get("/models?gateway=openrouter&unique_models=true&limit=5")

        assert response.status_code == 200, f"Should return 200, got {response.status_code}"
        # Should behave like flat catalog for provider-specific
        models = response.json()
        assert isinstance(models, list), "Should return a list"

    def test_deduplication_check(self, client):
        """Test that unique_models=true actually deduplicates."""
        # Fetch flat catalog
        flat_response = client.get("/models?gateway=all&unique_models=false&limit=1000")
        flat_models = flat_response.json()

        # Fetch unique catalog
        unique_response = client.get("/models?gateway=all&unique_models=true&limit=1000")
        unique_models = unique_response.json()

        # Unique catalog should have fewer or equal entries
        if flat_models and unique_models:
            assert len(unique_models) <= len(
                flat_models
            ), "Unique catalog should have fewer or equal entries than flat catalog"

            # All unique model names should be unique
            unique_names = [m["name"] for m in unique_models]
            assert len(unique_names) == len(
                set(unique_names)
            ), "All model names in unique catalog should be unique"
