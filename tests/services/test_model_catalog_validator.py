"""
Tests for model catalog validation service.
"""

import pytest
from datetime import datetime, timezone, UTC
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.model_catalog_validator import (
    validate_model_availability,
    validate_models_batch,
    clear_validation_cache,
)


class TestValidateModelAvailability:
    """Test model availability validation"""

    @pytest.mark.asyncio
    async def test_validate_cerebras_model_available(self):
        """Test validating available Cerebras model"""
        with patch("src.services.model_catalog_validator.get_cerebras_client") as mock_client_fn:
            # Mock Cerebras client response
            mock_client = MagicMock()
            mock_model = MagicMock()
            mock_model.id = "llama3.1-8b"
            mock_client.models.list.return_value.data = [mock_model]
            mock_client_fn.return_value = mock_client

            result = await validate_model_availability("llama3.1-8b", "cerebras", "cerebras")

            assert result["available"] is True
            assert result["model_id"] == "llama3.1-8b"
            assert result["provider"] == "cerebras"
            assert result["error"] is None

    @pytest.mark.asyncio
    async def test_validate_cerebras_model_unavailable(self):
        """Test validating unavailable Cerebras model"""
        with patch("src.services.model_catalog_validator.get_cerebras_client") as mock_client_fn:
            # Mock Cerebras client with different models
            mock_client = MagicMock()
            mock_model = MagicMock()
            mock_model.id = "llama3.1-8b"
            mock_client.models.list.return_value.data = [mock_model]
            mock_client_fn.return_value = mock_client

            # Try to validate non-existent model
            result = await validate_model_availability(
                "non-existent-model", "cerebras", "cerebras"
            )

            assert result["available"] is False
            assert result["model_id"] == "non-existent-model"
            assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_validate_huggingface_model_available(self):
        """Test validating available HuggingFace model"""
        with patch("httpx.AsyncClient") as mock_client:
            # Mock HuggingFace Hub API response (200 = model exists)
            mock_response = MagicMock()
            mock_response.status_code = 200

            # Mock Inference Router response (200 = available on router)
            mock_inference_response = MagicMock()
            mock_inference_response.status_code = 200
            mock_inference_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_inference_response
            )

            result = await validate_model_availability(
                "meta-llama/Llama-2-7b-chat-hf", "huggingface", "huggingface"
            )

            assert result["available"] is True
            assert result["provider"] == "huggingface"

    @pytest.mark.asyncio
    async def test_validate_huggingface_model_not_on_hub(self):
        """Test model not found on HuggingFace Hub"""
        with patch("httpx.AsyncClient") as mock_client:
            # Mock 404 response from Hub
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await validate_model_availability(
                "fake/model", "huggingface", "huggingface"
            )

            assert result["available"] is False
            assert "not found on HuggingFace Hub" in result["error"]

    @pytest.mark.asyncio
    async def test_validate_huggingface_model_not_on_inference_router(self):
        """Test model exists on Hub but not available on Inference Router"""
        with patch("httpx.AsyncClient") as mock_client:
            # Mock Hub API response (200 = exists)
            mock_hub_response = MagicMock()
            mock_hub_response.status_code = 200

            # Mock Inference Router response (400 = not available)
            mock_inference_response = MagicMock()
            mock_inference_response.status_code = 400

            async def mock_get_post(url, **kwargs):
                if "huggingface.co/api" in url:
                    return mock_hub_response
                return mock_inference_response

            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get = AsyncMock(return_value=mock_hub_response)
            mock_client_instance.post = AsyncMock(return_value=mock_inference_response)

            result = await validate_model_availability(
                "some-org/some-model", "huggingface", "huggingface"
            )

            assert result["available"] is False
            assert "not available on HF Inference Router" in result["error"]

    @pytest.mark.asyncio
    async def test_validate_openrouter_model_available(self):
        """Test validating available OpenRouter model"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {"id": "openai/gpt-4"},
                    {"id": "anthropic/claude-3-opus"},
                ]
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await validate_model_availability(
                "openai/gpt-4", "openrouter", "openrouter"
            )

            assert result["available"] is True
            assert result["model_id"] == "openai/gpt-4"

    @pytest.mark.asyncio
    async def test_validate_openrouter_model_unavailable(self):
        """Test validating unavailable OpenRouter model"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": [
                    {"id": "openai/gpt-4"},
                ]
            }
            mock_response.raise_for_status = MagicMock()

            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await validate_model_availability(
                "fake/model", "openrouter", "openrouter"
            )

            assert result["available"] is False
            assert "not found in OpenRouter catalog" in result["error"]

    @pytest.mark.asyncio
    async def test_validate_unsupported_provider(self):
        """Test validation for provider without validation implementation"""
        result = await validate_model_availability(
            "some-model", "unsupported-provider", "gateway"
        )

        # Should assume available for providers without validation
        assert result["available"] is True
        assert result["model_id"] == "some-model"
        assert result["provider"] == "unsupported-provider"

    @pytest.mark.asyncio
    async def test_validation_caching(self):
        """Test that validation results are cached"""
        with patch("src.services.model_catalog_validator.get_cerebras_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_model = MagicMock()
            mock_model.id = "llama3.1-8b"
            mock_client.models.list.return_value.data = [mock_model]
            mock_client_fn.return_value = mock_client

            # First call should hit API
            result1 = await validate_model_availability("llama3.1-8b", "cerebras", "cerebras")
            assert result1["available"] is True

            # Second call should use cache
            result2 = await validate_model_availability("llama3.1-8b", "cerebras", "cerebras")
            assert result2["available"] is True

            # Should only have called API once
            assert mock_client.models.list.call_count == 1


class TestValidateModelsBatch:
    """Test batch model validation"""

    @pytest.mark.asyncio
    async def test_validate_batch_all_available(self):
        """Test validating batch where all models are available"""
        models = [
            {
                "id": "llama3.1-8b",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            },
            {
                "id": "llama3.1-70b",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            }
        ]

        with patch("src.services.model_catalog_validator.validate_model_availability") as mock_validate:
            # Mock all as available
            mock_validate.return_value = {
                "available": True,
                "model_id": "test",
                "provider": "cerebras",
                "checked_at": datetime.now(UTC),
                "error": None
            }

            result = await validate_models_batch(models)

            assert len(result) == 2
            assert all(model in result for model in models)

    @pytest.mark.asyncio
    async def test_validate_batch_some_unavailable(self):
        """Test validating batch where some models are unavailable"""
        models = [
            {
                "id": "available-model",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            },
            {
                "id": "unavailable-model",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            }
        ]

        async def mock_validate(model_id, provider, gateway):
            if model_id == "available-model":
                return {
                    "available": True,
                    "model_id": model_id,
                    "provider": provider,
                    "checked_at": datetime.now(UTC),
                    "error": None
                }
            else:
                return {
                    "available": False,
                    "model_id": model_id,
                    "provider": provider,
                    "checked_at": datetime.now(UTC),
                    "error": "Model not found"
                }

        with patch("src.services.model_catalog_validator.validate_model_availability", side_effect=mock_validate):
            result = await validate_models_batch(models)

            # Only available model should be in result
            assert len(result) == 1
            assert result[0]["id"] == "available-model"

    @pytest.mark.asyncio
    async def test_validate_batch_empty_list(self):
        """Test validating empty batch"""
        result = await validate_models_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_validate_batch_with_exceptions(self):
        """Test batch validation handles exceptions gracefully"""
        models = [
            {
                "id": "model1",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            },
            {
                "id": "model2",
                "provider_slug": "cerebras",
                "source_gateway": "cerebras"
            }
        ]

        async def mock_validate(model_id, provider, gateway):
            if model_id == "model1":
                raise Exception("Validation failed")
            return {
                "available": True,
                "model_id": model_id,
                "provider": provider,
                "checked_at": datetime.now(UTC),
                "error": None
            }

        with patch("src.services.model_catalog_validator.validate_model_availability", side_effect=mock_validate):
            result = await validate_models_batch(models)

            # Only model2 should be in result (model1 threw exception)
            assert len(result) == 1
            assert result[0]["id"] == "model2"


class TestValidationCacheManagement:
    """Test validation cache management"""

    def test_clear_specific_model_cache(self):
        """Test clearing cache for specific model"""
        from src.services.model_catalog_validator import _validation_cache

        # Populate cache
        _validation_cache["cerebras:model1"] = {"available": True}
        _validation_cache["cerebras:model2"] = {"available": True}
        _validation_cache["huggingface:model1"] = {"available": True}

        # Clear specific model
        clear_validation_cache("model1")

        # model1 entries should be cleared
        assert "cerebras:model1" not in _validation_cache
        assert "huggingface:model1" not in _validation_cache
        # model2 should remain
        assert "cerebras:model2" in _validation_cache

    def test_clear_all_validation_cache(self):
        """Test clearing all validation cache"""
        from src.services.model_catalog_validator import _validation_cache

        # Populate cache
        _validation_cache["cerebras:model1"] = {"available": True}
        _validation_cache["huggingface:model2"] = {"available": True}

        # Clear all
        clear_validation_cache()

        # All should be cleared
        assert len(_validation_cache) == 0
