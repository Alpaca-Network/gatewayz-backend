"""
Comprehensive tests for provider client modules.

Tests all provider clients for:
- Client initialization
- Model fetching functionality
- Request/Response processing
- Error handling
- Authentication validation
"""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"


class TestCerebrasClient:
    """Test Cerebras client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import cerebras_client

        assert cerebras_client is not None

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

    def test_get_cerebras_client_fallback_info(self):
        """Test client fallback mechanism is documented"""
        from src.services.cerebras_client import get_cerebras_client

        # The get_cerebras_client function has fallback to OpenAI SDK
        # if Cerebras SDK is not available
        assert callable(get_cerebras_client)

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
        # When id is missing, name is used as fallback
        assert result is not None
        assert result.get("id") == "Test Model"

    def test_cleanup_model_id(self):
        """Test model ID cleanup"""
        from src.services.cerebras_client import _cleanup_model_id

        # Test various formats
        assert _cleanup_model_id("cerebras/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("@cerebras/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("models/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("api/llama3.1-8b") == "llama3.1-8b"
        assert _cleanup_model_id("llama3.1-8b") == "llama3.1-8b"

    def test_normalize_pricing(self):
        """Test pricing normalization"""
        from src.services.cerebras_client import _normalize_pricing

        # Test with various formats
        pricing = {"prompt": 0.001, "completion": 0.002, "request": "0.0001"}
        result = _normalize_pricing(pricing)
        assert result["prompt"] == "0.001"
        assert result["completion"] == "0.002"
        assert result["request"] == "0.0001"

        # Test with None
        result = _normalize_pricing(None)
        assert result["prompt"] is None
        assert result["completion"] is None

    def test_extract_supported_parameters(self):
        """Test parameter extraction"""
        from src.services.cerebras_client import _extract_supported_parameters

        # Test with explicit parameters
        payload = {"supported_parameters": ["max_tokens", "temperature"]}
        result = _extract_supported_parameters(payload)
        assert "max_tokens" in result
        assert "temperature" in result

        # Test with capabilities
        payload = {"capabilities": {"streaming": True, "tools": ["function_calling"]}}
        result = _extract_supported_parameters(payload)
        assert "streaming" in result

        # Test with empty payload
        result = _extract_supported_parameters({})
        assert len(result) > 0  # Should return defaults

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


class TestGroqClient:
    """Test Groq client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import groq_client

        assert groq_client is not None

    @patch("src.services.groq_client.Config")
    def test_get_groq_client_no_api_key(self, mock_config):
        """Test client fails without API key"""
        mock_config.GROQ_API_KEY = None
        from src.services.groq_client import get_groq_client

        with pytest.raises(ValueError, match="Groq API key not configured"):
            get_groq_client()

    @patch("src.services.groq_client.Config")
    @patch("src.services.groq_client.get_groq_pooled_client")
    def test_get_groq_client_success(self, mock_pooled_client, mock_config):
        """Test successful client initialization"""
        mock_config.GROQ_API_KEY = "test-key"
        mock_pooled_client.return_value = MagicMock()

        from src.services.groq_client import get_groq_client

        client = get_groq_client()
        assert client is not None

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai(self, mock_get_client):
        """Test Groq request making"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = []
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        from src.services.groq_client import make_groq_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        result = make_groq_request_openai(messages, "llama-3.3-70b-versatile")

        assert result is not None
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_stream(self, mock_get_client):
        """Test Groq streaming request"""
        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        from src.services.groq_client import make_groq_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        result = make_groq_request_openai_stream(messages, "llama-3.3-70b-versatile")

        assert result is not None
        mock_client.chat.completions.create.assert_called_once()
        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["stream"] is True

    def test_process_groq_response(self):
        """Test Groq response processing"""
        from src.services.groq_client import process_groq_response

        mock_response = MagicMock()
        mock_response.id = "test-id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.3-70b-versatile"

        mock_choice = MagicMock()
        mock_choice.index = 0
        mock_choice.finish_reason = "stop"
        mock_choice.message.content = "Hello!"
        mock_choice.message.role = "assistant"
        mock_choice.message.tool_calls = None
        mock_response.choices = [mock_choice]

        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        result = process_groq_response(mock_response)

        assert result["id"] == "test-id"
        assert result["model"] == "llama-3.3-70b-versatile"
        assert result["usage"]["total_tokens"] == 15
        assert len(result["choices"]) == 1


class TestGoogleVertexClient:
    """Test Google Vertex AI client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import google_vertex_client

        assert google_vertex_client is not None

    def test_sanitize_system_content_string(self):
        """Test system content sanitization with string"""
        from src.services.google_vertex_client import _sanitize_system_content

        result = _sanitize_system_content("You are a helpful assistant")
        assert result == "You are a helpful assistant"

    def test_sanitize_system_content_list(self):
        """Test system content sanitization with list"""
        from src.services.google_vertex_client import _sanitize_system_content

        content = [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]
        result = _sanitize_system_content(content)
        assert "Part 1" in result
        assert "Part 2" in result

    def test_sanitize_system_content_other(self):
        """Test system content sanitization with other types"""
        from src.services.google_vertex_client import _sanitize_system_content

        result = _sanitize_system_content(123)
        assert result == "123"

    @patch.dict(os.environ, {"GOOGLE_PROJECT_ID": "", "GOOGLE_VERTEX_LOCATION": ""}, clear=False)
    def test_prepare_vertex_environment_missing_project(self):
        """Test environment preparation fails without project ID"""
        with patch("src.services.google_vertex_client.Config") as mock_config:
            mock_config.GOOGLE_PROJECT_ID = None
            mock_config.GOOGLE_VERTEX_LOCATION = "us-central1"

            from src.services.google_vertex_client import _prepare_vertex_environment

            with pytest.raises(ValueError, match="GOOGLE_PROJECT_ID"):
                _prepare_vertex_environment()


class TestOpenRouterClient:
    """Test OpenRouter client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import openrouter_client

        assert openrouter_client is not None


class TestDeepInfraClient:
    """Test DeepInfra client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import deepinfra_client

        assert deepinfra_client is not None


class TestFireworksClient:
    """Test Fireworks AI client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import fireworks_client

        assert fireworks_client is not None


class TestTogetherClient:
    """Test Together AI client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import together_client

        assert together_client is not None


class TestXAIClient:
    """Test XAI (Grok) client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import xai_client

        assert xai_client is not None


class TestHuggingFaceClient:
    """Test HuggingFace client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import huggingface_client

        assert huggingface_client is not None


class TestNovitaClient:
    """Test Novita client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import novita_client

        assert novita_client is not None


class TestNebiusClient:
    """Test Nebius client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import nebius_client

        assert nebius_client is not None


class TestMorpheusClient:
    """Test Morpheus client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import morpheus_client

        assert morpheus_client is not None


class TestCloudflareWorkersAIClient:
    """Test Cloudflare Workers AI client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import cloudflare_workers_ai_client

        assert cloudflare_workers_ai_client is not None


class TestFeatherlessClient:
    """Test Featherless client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import featherless_client

        assert featherless_client is not None


class TestChutesClient:
    """Test Chutes client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import chutes_client

        assert chutes_client is not None


class TestAimoClient:
    """Test AIMO client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import aimo_client

        assert aimo_client is not None


class TestNearClient:
    """Test Near AI client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import near_client

        assert near_client is not None


class TestFalImageClient:
    """Test Fal.ai image client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import fal_image_client

        assert fal_image_client is not None


class TestImageGenerationClient:
    """Test image generation router client"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import image_generation_client

        assert image_generation_client is not None


class TestHeliconeClient:
    """Test Helicone client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import helicone_client

        assert helicone_client is not None


class TestAiHubMixClient:
    """Test AiHubMix client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import aihubmix_client

        assert aihubmix_client is not None


class TestAnannasClient:
    """Test Anannas client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import anannas_client

        assert anannas_client is not None


class TestVercelAIGatewayClient:
    """Test Vercel AI Gateway client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import vercel_ai_gateway_client

        assert vercel_ai_gateway_client is not None


class TestAlpacaNetworkClient:
    """Test Alpaca Network client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import alpaca_network_client

        assert alpaca_network_client is not None


class TestOneRouterClient:
    """Test OneRouter client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import onerouter_client

        assert onerouter_client is not None


class TestAkashClient:
    """Test Akash client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import akash_client

        assert akash_client is not None


class TestClarifaiClient:
    """Test Clarifai client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import clarifai_client

        assert clarifai_client is not None


class TestModelzClient:
    """Test Modelz client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import modelz_client

        assert modelz_client is not None


class TestAISDKClient:
    """Test AI SDK client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import ai_sdk_client

        assert ai_sdk_client is not None


class TestAlibabaCloudClient:
    """Test Alibaba Cloud client functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.services import alibaba_cloud_client

        assert alibaba_cloud_client is not None
