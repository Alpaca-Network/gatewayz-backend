#!/usr/bin/env python3
"""
Integration tests for /api/models/detail endpoint

This endpoint provides frontend compatibility for fetching model details
using query parameters instead of path parameters.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestApiModelsDetailEndpoint:
    """Test /api/models/detail endpoint"""

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_model_id(self, mock_providers, mock_fetch):
        """Test getting model detail using modelId parameter"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "z-ai/glm-4-7",
            "name": "GLM-4-7",
            "description": "Test model",
            "source_gateway": "openrouter",
        }

        response = client.get("/api/models/detail?modelId=z-ai/glm-4-7")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == "z-ai/glm-4-7"
        assert data["provider"] == "z-ai"
        assert data["model"] == "glm-4-7"

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_separate_params(self, mock_providers, mock_fetch):
        """Test getting model detail using separate developer and modelName params"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "openai/gpt-4",
            "name": "GPT-4",
            "source_gateway": "openrouter",
        }

        response = client.get("/api/models/detail?developer=openai&modelName=gpt-4")

        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4"

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_all_params(self, mock_providers, mock_fetch):
        """Test with both modelId and separate params (separate params take precedence)"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "anthropic/claude-3",
            "name": "Claude 3",
            "source_gateway": "openrouter",
        }

        # When both are provided, developer/modelName override parsed values from modelId
        response = client.get(
            "/api/models/detail?modelId=wrong/model&developer=anthropic&modelName=claude-3"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-3"

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_gateway(self, mock_providers, mock_fetch):
        """Test getting model detail with specific gateway"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "meta-llama/llama-2-70b",
            "name": "Llama 2 70B",
            "source_gateway": "deepinfra",
        }

        response = client.get("/api/models/detail?modelId=meta-llama/llama-2-70b&gateway=deepinfra")

        assert response.status_code == 200
        data = response.json()
        assert data["gateway"] == "deepinfra"

    @patch("src.routes.api_models.fetch_specific_model")
    def test_get_model_detail_not_found(self, mock_fetch):
        """Test 404 when model not found"""
        mock_fetch.return_value = None

        response = client.get("/api/models/detail?modelId=nonexistent/model")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    def test_get_model_detail_missing_params(self):
        """Test 400 when required parameters are missing"""
        response = client.get("/api/models/detail")

        assert response.status_code == 400
        data = response.json()
        assert "missing required parameters" in data["detail"].lower()

    def test_get_model_detail_only_developer(self):
        """Test 400 when only developer is provided"""
        response = client.get("/api/models/detail?developer=openai")

        assert response.status_code == 400

    def test_get_model_detail_only_model_name(self):
        """Test when only modelName is provided without developer"""
        # modelName alone should fail because we need both
        response = client.get("/api/models/detail?modelName=gpt-4")

        assert response.status_code == 400

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_huggingface(self, mock_providers, mock_fetch):
        """Test including HuggingFace data"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "meta-llama/Llama-2-7b",
            "name": "Llama 2 7B",
            "hugging_face_id": "meta-llama/Llama-2-7b-hf",
            "source_gateway": "hug",
        }

        response = client.get(
            "/api/models/detail?modelId=meta-llama/Llama-2-7b&include_huggingface=true"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["include_huggingface"] is True

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_returns_providers_list(self, mock_providers, mock_fetch):
        """Test that providers list is included in response"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "openai/gpt-4",
            "name": "GPT-4",
            "source_gateway": "openrouter",
            "source_gateways": ["openrouter", "deepinfra"],
        }

        response = client.get("/api/models/detail?modelId=openai/gpt-4")

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)
        assert "openrouter" in data["providers"]

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_url_encoded_model_id(self, mock_providers, mock_fetch):
        """Test with URL-encoded modelId (as sent by frontend)"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "z-ai/glm-4-7",
            "name": "GLM-4-7",
            "source_gateway": "openrouter",
        }

        # Frontend sends URL-encoded values like z-ai%2Fglm-4-7
        # TestClient should handle this, but let's verify with explicit encoding
        response = client.get("/api/models/detail?modelId=z-ai%2Fglm-4-7")

        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "z-ai"
        assert data["model"] == "glm-4-7"

    @patch("src.routes.api_models.fetch_specific_model")
    @patch("src.routes.api_models.get_cached_providers")
    def test_get_model_detail_with_complex_model_name(self, mock_providers, mock_fetch):
        """Test with complex model names containing multiple slashes"""
        mock_providers.return_value = []
        mock_fetch.return_value = {
            "id": "zai-org/glm-4-6",
            "name": "GLM-4-6",
            "source_gateway": "near",
        }

        response = client.get("/api/models/detail?modelId=zai-org/glm-4-6")

        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "zai-org"
        assert data["model"] == "glm-4-6"


class TestApiModelsDetailErrorHandling:
    """Test error handling in /api/models/detail endpoint"""

    @patch("src.routes.api_models.fetch_specific_model")
    def test_internal_error_handling(self, mock_fetch):
        """Test that internal errors return 500"""
        mock_fetch.side_effect = Exception("Database error")

        response = client.get("/api/models/detail?modelId=openai/gpt-4")

        assert response.status_code == 500
        data = response.json()
        assert "failed to get model data" in data["detail"].lower()
