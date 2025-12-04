"""
Comprehensive tests for Morpheus AI Gateway Client service
"""

import importlib

import pytest
from unittest.mock import Mock, patch


class TestMorpheusClient:
    """Test Morpheus Client service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        module = importlib.import_module("src.services.morpheus_client")

        assert module is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import morpheus_client

        assert hasattr(morpheus_client, "__name__")
        assert hasattr(morpheus_client, "get_morpheus_client")
        assert hasattr(morpheus_client, "make_morpheus_request_openai")
        assert hasattr(morpheus_client, "make_morpheus_request_openai_stream")
        assert hasattr(morpheus_client, "process_morpheus_response")
        assert hasattr(morpheus_client, "fetch_models_from_morpheus")

    def test_morpheus_base_url(self):
        """Test Morpheus base URL is correctly set"""
        from src.services.morpheus_client import MORPHEUS_BASE_URL

        assert MORPHEUS_BASE_URL == "https://api.mor.org/api/v1"

    @patch("src.services.morpheus_client.Config")
    def test_get_morpheus_client_raises_without_api_key(self, mock_config):
        """Test that get_morpheus_client raises error without API key"""
        mock_config.MORPHEUS_API_KEY = None

        from src.services.morpheus_client import get_morpheus_client

        with pytest.raises(ValueError, match="Morpheus API key not configured"):
            get_morpheus_client()

    @patch("src.services.morpheus_client.get_morpheus_pooled_client")
    @patch("src.services.morpheus_client.Config")
    def test_get_morpheus_client_returns_pooled_client(
        self, mock_config, mock_get_pooled
    ):
        """Test that get_morpheus_client returns pooled client"""
        mock_config.MORPHEUS_API_KEY = "test-key"
        mock_client = Mock()
        mock_get_pooled.return_value = mock_client

        from src.services.morpheus_client import get_morpheus_client

        client = get_morpheus_client()
        assert client == mock_client
        mock_get_pooled.assert_called_once()

    @patch("src.services.morpheus_client.get_morpheus_client")
    def test_make_morpheus_request_openai(self, mock_get_client):
        """Test making a request through Morpheus"""
        mock_client = Mock()
        mock_response = Mock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        from src.services.morpheus_client import make_morpheus_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        model = "llama-3.1-8b"

        response = make_morpheus_request_openai(messages, model, max_tokens=100)

        mock_client.chat.completions.create.assert_called_once_with(
            model=model, messages=messages, max_tokens=100
        )
        assert response == mock_response

    @patch("src.services.morpheus_client.get_morpheus_client")
    def test_make_morpheus_request_openai_stream(self, mock_get_client):
        """Test making a streaming request through Morpheus"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        from src.services.morpheus_client import make_morpheus_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        model = "llama-3.1-8b"

        stream = make_morpheus_request_openai_stream(messages, model, max_tokens=100)

        mock_client.chat.completions.create.assert_called_once_with(
            model=model, messages=messages, stream=True, max_tokens=100
        )
        assert stream == mock_stream

    def test_process_morpheus_response(self):
        """Test processing Morpheus response"""
        from src.services.morpheus_client import process_morpheus_response

        # Create mock response
        mock_message = Mock()
        mock_message.content = "Hello, I'm an AI assistant."
        mock_message.role = "assistant"
        mock_message.tool_calls = None

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_usage = Mock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30

        mock_response = Mock()
        mock_response.id = "chatcmpl-123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.1-8b"
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        result = process_morpheus_response(mock_response)

        assert result["id"] == "chatcmpl-123"
        assert result["object"] == "chat.completion"
        assert result["created"] == 1234567890
        assert result["model"] == "llama-3.1-8b"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["index"] == 0
        assert result["choices"][0]["finish_reason"] == "stop"
        # Verify message content and structure
        assert "message" in result["choices"][0]
        message = result["choices"][0]["message"]
        assert "content" in message or "role" in message
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 20
        assert result["usage"]["total_tokens"] == 30

    def test_process_morpheus_response_with_tool_calls(self):
        """Test processing Morpheus response with tool calls"""
        from src.services.morpheus_client import process_morpheus_response

        # Create mock response with tool calls
        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = '{"location": "NYC"}'

        mock_message = Mock()
        mock_message.content = None
        mock_message.role = "assistant"
        mock_message.tool_calls = [mock_tool_call]

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = mock_message
        mock_choice.finish_reason = "tool_calls"

        mock_response = Mock()
        mock_response.id = "chatcmpl-456"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.1-8b"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        result = process_morpheus_response(mock_response)

        assert result["id"] == "chatcmpl-456"
        assert result["choices"][0]["finish_reason"] == "tool_calls"
        assert "message" in result["choices"][0]
        # Usage should be empty dict when None
        assert result["usage"] == {}

    @patch("httpx.get")
    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_success(self, mock_config, mock_httpx_get):
        """Test fetching models from Morpheus API"""
        mock_config.MORPHEUS_API_KEY = "test-key"

        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "llama-3.1-8b", "context_length": 8192},
                {"id": "mistral-7b", "context_length": 4096},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_httpx_get.return_value = mock_response

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()

        assert len(models) == 2
        assert models[0]["id"] == "morpheus/llama-3.1-8b"
        assert models[0]["provider_slug"] == "morpheus"
        assert models[1]["id"] == "morpheus/mistral-7b"

    @patch("src.services.morpheus_client.Config")
    def test_fetch_models_from_morpheus_no_api_key(self, mock_config):
        """Test fetch_models returns empty list without API key"""
        mock_config.MORPHEUS_API_KEY = None

        from src.services.morpheus_client import fetch_models_from_morpheus

        models = fetch_models_from_morpheus()
        assert models == []


class TestMorpheusModelTransformations:
    """Test model ID transformations for Morpheus"""

    def test_morpheus_provider_detection(self):
        """Test that morpheus/ prefix models are detected correctly"""
        from src.services.model_transformations import detect_provider_from_model_id

        provider = detect_provider_from_model_id("morpheus/llama-3.1-8b")
        assert provider == "morpheus"

    def test_morpheus_prefix_stripping(self):
        """Test that morpheus/ prefix is stripped during transformation"""
        from src.services.model_transformations import transform_model_id

        result = transform_model_id("morpheus/llama-3.1-8b", "morpheus")
        assert result == "llama-3.1-8b"

    def test_morpheus_direct_model_passthrough(self):
        """Test that direct model names pass through"""
        from src.services.model_transformations import transform_model_id

        result = transform_model_id("llama-3.1-8b", "morpheus")
        assert result == "llama-3.1-8b"


class TestMorpheusConfig:
    """Test Morpheus configuration"""

    def test_config_has_morpheus_api_key(self):
        """Test that Config class has MORPHEUS_API_KEY attribute"""
        from src.config import Config

        assert hasattr(Config, "MORPHEUS_API_KEY")
