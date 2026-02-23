"""
Test SimpliSmart models loading through the catalog endpoint.

This test verifies that SimpliSmart models:
1. Are returned when querying with gateway=simplismart
2. Have the correct source_gateway field set
3. Are included in the gateway=all response
4. Can be filtered by the frontend using source_gateway
"""

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.services.simplismart_client import fetch_models_from_simplismart


class TestSimplismartModelsLoading:
    """Test suite for SimpliSmart models loading."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)

    def test_simplismart_client_returns_models_with_source_gateway(self):
        """Verify SimpliSmart client returns models with source_gateway field."""
        models = fetch_models_from_simplismart()

        assert len(models) > 0, "SimpliSmart client should return at least one model"

        for model in models:
            assert "id" in model, f"Model {model.get('name', 'unknown')} missing 'id'"
            assert "source_gateway" in model, f"Model {model['id']} missing 'source_gateway'"
            assert model["source_gateway"] == "simplismart", \
                f"Model {model['id']} has wrong source_gateway: {model['source_gateway']}"
            assert "provider" in model, f"Model {model['id']} missing 'provider'"
            assert model["provider"] == "simplismart", \
                f"Model {model['id']} has wrong provider: {model['provider']}"

    def test_catalog_endpoint_returns_simplismart_models(self, client):
        """Verify /models endpoint returns SimpliSmart models when gateway=simplismart."""
        response = client.get("/models?gateway=simplismart")

        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        models = data.get("models", [])

        # SimpliSmart should have models
        assert len(models) > 0, "SimpliSmart gateway should return models"

        # All models should have source_gateway=simplismart
        for model in models:
            assert "source_gateway" in model, \
                f"Model {model.get('id', 'unknown')} missing source_gateway field"
            assert model["source_gateway"] == "simplismart", \
                f"Model {model['id']} has wrong source_gateway: {model['source_gateway']}"

    def test_catalog_endpoint_includes_simplismart_in_all_gateway(self, client):
        """Verify /models endpoint includes SimpliSmart models when gateway=all."""
        response = client.get("/models?gateway=all&limit=10000")

        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        models = data.get("models", [])

        # Find SimpliSmart models in the response
        simplismart_models = [
            m for m in models
            if m.get("source_gateway") == "simplismart"
        ]

        assert len(simplismart_models) > 0, \
            "gateway=all should include SimpliSmart models with source_gateway='simplismart'"

        # Verify at least one known SimpliSmart model is present
        model_ids = [m.get("id") for m in simplismart_models]
        assert any("llama" in mid.lower() for mid in model_ids), \
            f"Expected to find Llama models in SimpliSmart, got: {model_ids[:5]}"

    def test_simplismart_models_have_required_fields(self, client):
        """Verify SimpliSmart models have all required fields for frontend filtering."""
        response = client.get("/models?gateway=simplismart")

        assert response.status_code == 200
        data = response.json()
        models = data.get("models", [])

        assert len(models) > 0, "SimpliSmart should return models"

        required_fields = ["id", "name", "provider", "provider_slug", "source_gateway"]

        for model in models:
            for field in required_fields:
                assert field in model, \
                    f"Model {model.get('id', 'unknown')} missing required field: {field}"

            # Verify provider fields match
            assert model["provider"] == "simplismart", \
                f"Model {model['id']} has wrong provider: {model['provider']}"
            assert model["provider_slug"] == "simplismart", \
                f"Model {model['id']} has wrong provider_slug: {model['provider_slug']}"
            assert model["source_gateway"] == "simplismart", \
                f"Model {model['id']} has wrong source_gateway: {model['source_gateway']}"

    def test_v1_models_endpoint_returns_simplismart(self, client):
        """Verify /v1/models endpoint also returns SimpliSmart models."""
        response = client.get("/v1/models?gateway=simplismart")

        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        models = data.get("data", [])

        assert len(models) > 0, "SimpliSmart gateway should return models on /v1/models"

        # Verify models have source_gateway field
        for model in models:
            assert "id" in model, "Model missing 'id'"
            # Note: /v1/models might have different structure, so we just check basic fields
