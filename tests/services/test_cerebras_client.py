"""
Comprehensive tests for Cerebras Client service
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"


class TestCerebrasClient:
    """Test Cerebras Client service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.cerebras_client

        assert src.services.cerebras_client is not None

    def test_default_models_defined(self):
        """Test that default models are defined"""
        from src.services.cerebras_client import DEFAULT_CEREBRAS_MODELS

        assert isinstance(DEFAULT_CEREBRAS_MODELS, list)
        assert len(DEFAULT_CEREBRAS_MODELS) > 0

    def test_default_models_have_required_fields(self):
        """Test that default models have required fields"""
        from src.services.cerebras_client import DEFAULT_CEREBRAS_MODELS

        for model in DEFAULT_CEREBRAS_MODELS:
            assert "id" in model
            assert "name" in model
            assert "owned_by" in model
            assert "context_length" in model

    def test_default_supported_parameters(self):
        """Test default supported parameters"""
        from src.services.cerebras_client import DEFAULT_SUPPORTED_PARAMETERS

        assert isinstance(DEFAULT_SUPPORTED_PARAMETERS, list)
        assert "max_tokens" in DEFAULT_SUPPORTED_PARAMETERS
        assert "temperature" in DEFAULT_SUPPORTED_PARAMETERS
        assert "stream" in DEFAULT_SUPPORTED_PARAMETERS

    def test_get_cerebras_client_no_api_key(self):
        """Test client raises ValueError without API key"""
        from src.services.cerebras_client import get_cerebras_client

        with patch("src.config.Config") as mock_config:
            mock_config.CEREBRAS_API_KEY = None
            with pytest.raises(ValueError, match="Cerebras API key not configured"):
                get_cerebras_client()

    def test_normalize_cerebras_model(self):
        """Test model normalization"""
        from src.services.cerebras_client import _normalize_cerebras_model

        model = {
            "id": "llama3.1-8b",
            "name": "Llama 3.1 8B",
            "owned_by": "meta",
            "context_length": 131072,
        }

        result = _normalize_cerebras_model(model)
        assert result is not None
        assert result["id"] == "llama3.1-8b"
        assert result["source_gateway"] == "cerebras"
        assert "pricing" in result
        assert "architecture" in result

    def test_normalize_cerebras_model_missing_id(self):
        """Test normalization uses name as fallback ID when id is missing"""
        from src.services.cerebras_client import _normalize_cerebras_model

        model = {"name": "Test Model"}
        result = _normalize_cerebras_model(model)
        assert result is not None
        assert result.get("id") == "Test Model"

    def test_cleanup_model_id(self):
        """Test model ID cleanup"""
        from src.services.cerebras_client import _cleanup_model_id

        assert _cleanup_model_id("cerebras/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("@cerebras/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("models/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("api/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("llama3.1-8b") == "llama3.1-8b"

    def test_normalize_pricing(self):
        """Test pricing normalization"""
        from src.services.cerebras_client import _normalize_pricing

        pricing = {"prompt": 0.001, "completion": 0.002, "request": "0.0001"}
        result = _normalize_pricing(pricing)
        assert result["prompt"] == "0.001"
        assert result["completion"] == "0.002"
        assert result["request"] == "0.0001"

        result = _normalize_pricing(None)
        assert result["prompt"] is None
        assert result["completion"] is None

    def test_extract_supported_parameters(self):
        """Test parameter extraction"""
        from src.services.cerebras_client import _extract_supported_parameters

        payload = {"supported_parameters": ["max_tokens", "temperature"]}
        result = _extract_supported_parameters(payload)
        assert "max_tokens" in result
        assert "temperature" in result

        payload = {"capabilities": {"streaming": True, "tools": ["function_calling"]}}
        result = _extract_supported_parameters(payload)
        assert "streaming" in result

        result = _extract_supported_parameters({})
        assert len(result) > 0

    def test_coerce_sequence(self):
        """Test sequence coercion"""
        from src.services.cerebras_client import _coerce_sequence

        assert _coerce_sequence(None) == []
        assert _coerce_sequence([1, 2, 3]) == [1, 2, 3]
        assert _coerce_sequence((1, 2, 3)) == [1, 2, 3]
        assert _coerce_sequence("test") == ["t", "e", "s", "t"]

    def test_fallback_cerebras_models(self):
        """Test fallback to static models"""
        from src.services.cerebras_client import _fallback_cerebras_models

        result = _fallback_cerebras_models("test_reason")
        assert result is not None
        assert len(result) > 0
        assert all("id" in m for m in result)

    def test_fetch_models_from_cerebras_normalizes_sdk_response(self, monkeypatch):
        """Ensure fetch_models_from_cerebras returns normalized entries when SDK responds."""
        from src.services import cerebras_client

        fake_model = MagicMock()
        fake_model.model_dump.return_value = {
            "id": "@cerebras/llama3.1-8b",
            "owned_by": "@cerebras",
            "pricing": {"prompt": "0.1", "completion": "0.2"},
            "capabilities": {"inference": ["temperature", "max_tokens"]},
        }

        fake_page = MagicMock()
        fake_page.data = [fake_model]

        fake_client = MagicMock()
        fake_client.models.list.return_value = fake_page

        cache = {"data": None, "timestamp": None, "ttl": 60, "stale_ttl": 120}

        monkeypatch.setattr(cerebras_client, "get_cerebras_client", lambda: fake_client)
        monkeypatch.setattr(cerebras_client, "_cerebras_models_cache", cache, raising=False)

        models = cerebras_client.fetch_models_from_cerebras()

        assert models, "Expected normalized models from SDK response"
        assert models[0]["id"] == "llama3.1-8b"
        assert models[0]["provider_slug"] == "cerebras"
        assert models[0]["pricing"]["prompt"] == "0.1"
        assert cache["data"] == models

    def test_fetch_models_from_cerebras_uses_fallback_on_error(self, monkeypatch):
        """Ensure fallback catalog is used when client initialization fails."""
        from src.services import cerebras_client

        cache = {"data": None, "timestamp": None, "ttl": 60, "stale_ttl": 120}

        monkeypatch.setattr(
            cerebras_client,
            "get_cerebras_client",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        monkeypatch.setattr(
            cerebras_client,
            "DEFAULT_CEREBRAS_MODELS",
            [{"id": "llama3.1-8b"}],
            raising=False,
        )
        monkeypatch.setattr(cerebras_client, "_cerebras_models_cache", cache, raising=False)

        models = cerebras_client.fetch_models_from_cerebras()

        assert models, "Fallback catalog should provide at least one model"
        assert models[0]["id"] == "llama3.1-8b"
        assert cache["data"] == models
