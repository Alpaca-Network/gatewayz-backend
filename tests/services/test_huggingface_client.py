"""Tests for Hugging Face Inference API client"""

from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from src.services.huggingface_client import (
    _prepare_model,
    _resolve_model_id_case,
    get_huggingface_client,
    make_huggingface_request_openai,
    make_huggingface_request_openai_stream,
    process_huggingface_response,
)


class TestHuggingFaceClient:
    """Test Hugging Face Inference API client functionality"""

    @patch("src.services.huggingface_client.Config.HUG_API_KEY", "hf_test_token")
    def test_get_huggingface_client(self):
        """Test getting Hugging Face client"""
        client = get_huggingface_client()
        try:
            assert client is not None
            assert str(client.base_url).rstrip("/") == "https://router.huggingface.co/v1"
        finally:
            client.close()

    @patch("src.services.huggingface_client.Config.HUG_API_KEY", None)
    def test_get_huggingface_client_no_key(self):
        """Test getting Hugging Face client without API key"""
        with pytest.raises(ValueError, match="Hugging Face API key"):
            get_huggingface_client()

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_make_huggingface_request_openai(self, mock_get_client):
        """Test making request to Hugging Face"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "test_id",
            "object": "chat.completion",
            "created": 123,
            "model": "meta-llama/Llama-2-7b-chat-hf:hf-inference",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_huggingface_request_openai(messages, "meta-llama/Llama-2-7b-chat-hf")

        assert response["id"] == "test_id"
        mock_client.post.assert_called_once()
        sent_payload = mock_client.post.call_args.kwargs["json"]
        assert sent_payload["model"] == "meta-llama/Llama-2-7b-chat-hf:hf-inference"
        mock_client.close.assert_called_once()

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_make_huggingface_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Hugging Face"""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_stream_context = MagicMock()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            'data: {"id":"abc","object":"chat.completion.chunk","created":123,"model":"meta-llama/Llama-2-7b-chat-hf:hf-inference","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}',
            "data: [DONE]",
        ]
        mock_response.raise_for_status.return_value = None
        mock_stream_context.__enter__.return_value = mock_response
        mock_stream_context.__exit__.return_value = False
        mock_client.stream.return_value = mock_stream_context
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_huggingface_request_openai_stream(messages, "meta-llama/Llama-2-7b-chat-hf")
        chunk = next(stream)
        # Exhaust generator to trigger cleanup
        for _ in stream:
            pass

        assert chunk.model == "meta-llama/Llama-2-7b-chat-hf:hf-inference"
        assert chunk.choices[0].delta.content == "Hello"
        mock_client.stream.assert_called_once()
        stream_payload = mock_client.stream.call_args.kwargs["json"]
        assert stream_payload["model"] == "meta-llama/Llama-2-7b-chat-hf:hf-inference"
        mock_client.close.assert_called_once()

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_make_huggingface_request_openai_foreign_namespace(self, mock_get_client):
        """Models scoped to other providers should not be routed through Hugging Face"""
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(HTTPException) as excinfo:
            make_huggingface_request_openai(messages, "openrouter/auto")

        assert excinfo.value.status_code == 404
        mock_client.post.assert_not_called()
        mock_client.close.assert_called_once()

    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_make_huggingface_request_openai_stream_foreign_namespace(self, mock_get_client):
        """Streaming helper should also reject foreign provider models"""
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        stream = make_huggingface_request_openai_stream(
            [{"role": "user", "content": "Hello"}], "openrouter/auto"
        )

        with pytest.raises(HTTPException) as excinfo:
            next(stream)

        assert excinfo.value.status_code == 404
        mock_client.stream.assert_not_called()
        mock_client.close.assert_called_once()

    def test_process_huggingface_response(self):
        """Test processing Hugging Face response"""
        mock_response = {
            "id": "test_id",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "meta-llama/Llama-2-7b-chat-hf",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

        processed = process_huggingface_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "meta-llama/Llama-2-7b-chat-hf"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30


class TestModelIdCaseResolution:
    """Test model ID case resolution for HuggingFace Router API"""

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_resolve_model_id_case_with_cached_model(self, mock_cache):
        """Test that lowercase model IDs are resolved to correct case from cache"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
            {"id": "meta-llama/Llama-3.3-70B-Instruct"},
        ]

        # Lowercase input should be resolved to correct case
        result = _resolve_model_id_case("allenai/olmo-3.1-32b-instruct")
        assert result == "allenai/OLMo-3.1-32B-Instruct"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_resolve_model_id_case_preserves_suffix(self, mock_cache):
        """Test that :hf-inference suffix is preserved after case resolution"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
        ]

        result = _resolve_model_id_case("allenai/olmo-3.1-32b-instruct:hf-inference")
        assert result == "allenai/OLMo-3.1-32B-Instruct:hf-inference"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_resolve_model_id_case_not_in_cache(self, mock_cache):
        """Test that model IDs not in cache are returned as-is"""
        mock_cache.get.return_value = [
            {"id": "meta-llama/Llama-3.3-70B-Instruct"},
        ]

        # Model not in cache should be returned as-is
        result = _resolve_model_id_case("some-org/unknown-model")
        assert result == "some-org/unknown-model"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_resolve_model_id_case_empty_cache(self, mock_cache):
        """Test that model IDs are returned as-is when cache is empty"""
        mock_cache.get.return_value = None

        result = _resolve_model_id_case("allenai/olmo-3.1-32b-instruct")
        assert result == "allenai/olmo-3.1-32b-instruct"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_resolve_model_id_case_already_correct(self, mock_cache):
        """Test that correctly-cased model IDs are returned unchanged"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
        ]

        result = _resolve_model_id_case("allenai/OLMo-3.1-32B-Instruct")
        assert result == "allenai/OLMo-3.1-32B-Instruct"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_prepare_model_resolves_case_and_adds_suffix(self, mock_cache):
        """Test that _prepare_model resolves case and adds suffix"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
        ]

        result = _prepare_model("allenai/olmo-3.1-32b-instruct")
        assert result == "allenai/OLMo-3.1-32B-Instruct:hf-inference"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    def test_prepare_model_does_not_double_suffix(self, mock_cache):
        """Test that _prepare_model doesn't add suffix if already present"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
        ]

        result = _prepare_model("allenai/olmo-3.1-32b-instruct:hf-inference")
        assert result == "allenai/OLMo-3.1-32B-Instruct:hf-inference"

    @patch("src.services.huggingface_client._huggingface_models_cache")
    @patch("src.services.huggingface_client.get_huggingface_client")
    def test_request_uses_correct_case(self, mock_get_client, mock_cache):
        """Test that requests use the correctly-cased model ID"""
        mock_cache.get.return_value = [
            {"id": "allenai/OLMo-3.1-32B-Instruct"},
        ]

        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "test",
            "model": "allenai/OLMo-3.1-32B-Instruct:hf-inference",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "usage": {},
        }
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Make request with lowercase model ID
        make_huggingface_request_openai(
            [{"role": "user", "content": "Hello"}],
            "allenai/olmo-3.1-32b-instruct",  # lowercase
        )

        # Verify the request used the correct case
        sent_payload = mock_client.post.call_args.kwargs["json"]
        assert sent_payload["model"] == "allenai/OLMo-3.1-32B-Instruct:hf-inference"
