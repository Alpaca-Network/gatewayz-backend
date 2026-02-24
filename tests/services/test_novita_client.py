"""
Comprehensive tests for src/services/novita_client.py
"""

from unittest.mock import MagicMock, patch

import pytest


class TestNovitaSDKImport:
    """Test SDK import and availability detection"""

    def test_sdk_availability_flag_exists(self):
        """Test that NOVITA_SDK_AVAILABLE flag is defined"""
        from src.services.novita_client import NOVITA_SDK_AVAILABLE

        assert isinstance(NOVITA_SDK_AVAILABLE, bool)

    def test_module_imports_successfully(self):
        """Test that module can be imported without errors"""
        from src.services import novita_client

        assert novita_client is not None
        assert hasattr(novita_client, "fetch_models_from_novita")
        assert hasattr(novita_client, "get_novita_sdk_client")
        assert hasattr(novita_client, "fetch_image_models_from_novita_sdk")
        assert hasattr(novita_client, "generate_image_with_novita_sdk")


class TestGetNovitaSDKClient:
    """Test get_novita_sdk_client function"""

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", False)
    def test_returns_none_when_sdk_not_available(self):
        """Test that function returns None when SDK is not installed"""
        from src.services.novita_client import get_novita_sdk_client

        result = get_novita_sdk_client()
        assert result is None

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.config.Config")
    def test_raises_error_when_api_key_missing(self, mock_config):
        """Test that function raises ValueError when API key is not configured"""
        from src.services.novita_client import get_novita_sdk_client

        mock_config.NOVITA_API_KEY = None

        with pytest.raises(ValueError, match="NOVITA_API_KEY not configured"):
            get_novita_sdk_client()

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.NovitaClient")
    @patch("src.config.Config")
    def test_returns_client_when_sdk_available_and_key_configured(
        self, mock_config, mock_novita_client
    ):
        """Test that function returns NovitaClient instance when everything is configured"""
        from src.services.novita_client import get_novita_sdk_client

        mock_config.NOVITA_API_KEY = "test_api_key"
        mock_client_instance = MagicMock()
        mock_novita_client.return_value = mock_client_instance

        result = get_novita_sdk_client()

        assert result is mock_client_instance
        mock_novita_client.assert_called_once_with(api_key="test_api_key")


class TestFetchModelsFromNovita:
    """Test fetch_models_from_novita function (LLM models)"""

    @patch("src.config.Config")
    def test_returns_fallback_when_api_key_missing(self, mock_config):
        """Test that function returns fallback models when API key is not configured"""
        from src.services.novita_client import fetch_models_from_novita

        mock_config.NOVITA_API_KEY = None

        result = fetch_models_from_novita()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0
        # Check that default models are returned
        model_ids = [m["id"] for m in result]
        assert "qwen3-235b-thinking" in model_ids or "qwen3-max" in model_ids

    @patch("openai.OpenAI")
    @patch("src.config.Config")
    def test_fetches_models_from_openai_api(self, mock_config, mock_openai):
        """Test that function fetches models from OpenAI-compatible API"""
        from src.services.novita_client import fetch_models_from_novita

        mock_config.NOVITA_API_KEY = "test_api_key"

        # Mock OpenAI client response
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        mock_model = MagicMock()
        mock_model.id = "test-model"
        mock_model.name = "Test Model"
        mock_model.owned_by = "test-provider"

        mock_response = MagicMock()
        mock_response.data = [mock_model]
        mock_client.models.list.return_value = mock_response

        result = fetch_models_from_novita()

        assert result is not None
        assert isinstance(result, list)
        mock_openai.assert_called_once()
        mock_client.models.list.assert_called_once()

    @patch("openai.OpenAI")
    @patch("src.config.Config")
    def test_returns_fallback_on_api_error(self, mock_config, mock_openai):
        """Test that function returns fallback models when API call fails"""
        from src.services.novita_client import fetch_models_from_novita

        mock_config.NOVITA_API_KEY = "test_api_key"
        mock_openai.side_effect = Exception("API Error")

        result = fetch_models_from_novita()

        assert result is not None
        assert isinstance(result, list)


class TestFetchImageModelsFromNovitaSDK:
    """Test fetch_image_models_from_novita_sdk function"""

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", False)
    def test_returns_none_when_sdk_not_available(self):
        """Test that function returns None when SDK is not installed"""
        from src.services.novita_client import fetch_image_models_from_novita_sdk

        result = fetch_image_models_from_novita_sdk()
        assert result is None

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_returns_none_when_client_initialization_fails(self, mock_get_client):
        """Test that function returns None when client initialization fails"""
        from src.services.novita_client import fetch_image_models_from_novita_sdk

        mock_get_client.return_value = None

        result = fetch_image_models_from_novita_sdk()
        assert result is None

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_fetches_models_using_sdk(self, mock_get_client):
        """Test that function fetches models using SDK's models_v3 method"""
        from src.services.novita_client import fetch_image_models_from_novita_sdk

        # Mock SDK client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock model list response
        mock_model = MagicMock()
        mock_model.name = "test-model"
        mock_model_list = MagicMock()
        mock_model_list.models = [mock_model]

        mock_client.models_v3.return_value = mock_model_list

        result = fetch_image_models_from_novita_sdk()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 1
        mock_client.models_v3.assert_called_once_with(refresh=True)

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_returns_none_on_sdk_error(self, mock_get_client):
        """Test that function returns None when SDK call fails"""
        from src.services.novita_client import fetch_image_models_from_novita_sdk

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.models_v3.side_effect = Exception("SDK Error")

        result = fetch_image_models_from_novita_sdk()
        assert result is None


class TestGenerateImageWithNovitaSDK:
    """Test generate_image_with_novita_sdk function"""

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", False)
    def test_raises_import_error_when_sdk_not_available(self):
        """Test that function raises ImportError when SDK is not installed"""
        from src.services.novita_client import generate_image_with_novita_sdk

        with pytest.raises(ImportError, match="Novita SDK not installed"):
            generate_image_with_novita_sdk(prompt="test prompt")

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_raises_value_error_when_client_initialization_fails(self, mock_get_client):
        """Test that function raises ValueError when client initialization fails"""
        from src.services.novita_client import generate_image_with_novita_sdk

        mock_get_client.return_value = None

        with pytest.raises(ValueError, match="Failed to initialize Novita SDK client"):
            generate_image_with_novita_sdk(prompt="test prompt")

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_generates_image_with_default_parameters(self, mock_get_client):
        """Test that function generates image with default parameters"""
        from src.services.novita_client import generate_image_with_novita_sdk

        # Mock SDK client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response
        mock_response = MagicMock()
        mock_client.txt2img_v3.return_value = mock_response

        result = generate_image_with_novita_sdk(prompt="test prompt")

        assert result is mock_response
        mock_client.txt2img_v3.assert_called_once()
        call_kwargs = mock_client.txt2img_v3.call_args[1]
        assert call_kwargs["prompt"] == "test prompt"
        assert call_kwargs["model_name"] == "dreamshaper_8_93211.safetensors"
        assert call_kwargs["width"] == 512
        assert call_kwargs["height"] == 512

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_generates_image_with_custom_parameters(self, mock_get_client):
        """Test that function generates image with custom parameters"""
        from src.services.novita_client import generate_image_with_novita_sdk

        # Mock SDK client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response
        mock_response = MagicMock()
        mock_client.txt2img_v3.return_value = mock_response

        result = generate_image_with_novita_sdk(
            prompt="custom prompt",
            model_name="custom-model",
            width=1024,
            height=768,
            steps=50,
            guidance_scale=10.0,
            negative_prompt="ugly",
        )

        assert result is mock_response
        call_kwargs = mock_client.txt2img_v3.call_args[1]
        assert call_kwargs["prompt"] == "custom prompt"
        assert call_kwargs["model_name"] == "custom-model"
        assert call_kwargs["width"] == 1024
        assert call_kwargs["height"] == 768
        assert call_kwargs["steps"] == 50
        assert call_kwargs["guidance_scale"] == 10.0
        assert call_kwargs["negative_prompt"] == "ugly"

    @patch("src.services.novita_client.NOVITA_SDK_AVAILABLE", True)
    @patch("src.services.novita_client.get_novita_sdk_client")
    def test_raises_error_on_generation_failure(self, mock_get_client):
        """Test that function raises error when image generation fails"""
        from src.services.novita_client import generate_image_with_novita_sdk

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.txt2img_v3.side_effect = Exception("Generation failed")

        with pytest.raises(Exception, match="Generation failed"):
            generate_image_with_novita_sdk(prompt="test prompt")


class TestDefaultModels:
    """Test default Novita models configuration"""

    def test_default_models_are_defined(self):
        """Test that default models list is defined"""
        from src.services.novita_client import DEFAULT_NOVITA_MODELS

        assert DEFAULT_NOVITA_MODELS is not None
        assert isinstance(DEFAULT_NOVITA_MODELS, list)
        assert len(DEFAULT_NOVITA_MODELS) > 0

    def test_default_models_have_required_fields(self):
        """Test that default models have required fields"""
        from src.services.novita_client import DEFAULT_NOVITA_MODELS

        for model in DEFAULT_NOVITA_MODELS:
            assert "id" in model
            assert "name" in model
            assert "owned_by" in model
            assert "context_length" in model

            assert isinstance(model["id"], str)
            assert isinstance(model["name"], str)
            assert isinstance(model["context_length"], int)


class TestHelperFunctions:
    """Test helper functions"""

    def test_cleanup_model_id_removes_prefixes(self):
        """Test that _cleanup_model_id removes various prefixes"""
        from src.services.novita_client import _cleanup_model_id

        assert _cleanup_model_id("@novita/model") == "model"
        assert _cleanup_model_id("novita/model") == "model"
        assert _cleanup_model_id("models/model") == "model"
        assert _cleanup_model_id("api/model") == "model"
        assert _cleanup_model_id("model") == "model"

    def test_normalize_pricing_handles_none(self):
        """Test that _normalize_pricing handles None input"""
        from src.services.novita_client import _normalize_pricing

        result = _normalize_pricing(None)

        assert result is not None
        assert isinstance(result, dict)
        assert "prompt" in result
        assert "completion" in result

    def test_normalize_pricing_extracts_values(self):
        """Test that _normalize_pricing extracts pricing values"""
        from src.services.novita_client import _normalize_pricing

        pricing_data = {"prompt": "0.001", "completion": "0.002"}

        result = _normalize_pricing(pricing_data)

        assert result["prompt"] == "0.001"
        assert result["completion"] == "0.002"


class TestModuleDocumentation:
    """Test module and function documentation"""

    def test_module_has_docstring(self):
        """Test that module has proper docstring"""
        from src.services import novita_client

        assert novita_client.__doc__ is not None
        assert "Novita AI" in novita_client.__doc__
        assert "OpenAI-compatible" in novita_client.__doc__

    def test_functions_have_docstrings(self):
        """Test that all main functions have docstrings"""
        from src.services.novita_client import (
            fetch_image_models_from_novita_sdk,
            fetch_models_from_novita,
            generate_image_with_novita_sdk,
            get_novita_sdk_client,
        )

        assert get_novita_sdk_client.__doc__ is not None
        assert fetch_models_from_novita.__doc__ is not None
        assert fetch_image_models_from_novita_sdk.__doc__ is not None
        assert generate_image_with_novita_sdk.__doc__ is not None
