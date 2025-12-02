"""
Tests for the Vercel AI SDK endpoint.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# Set test environment variables before imports
os.environ.setdefault('APP_ENV', 'testing')
os.environ.setdefault('TESTING', 'true')
os.environ.setdefault('SUPABASE_URL', 'https://test.supabase.co')
os.environ.setdefault('SUPABASE_KEY', 'test-key')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-openrouter-key')
os.environ.setdefault('ENCRYPTION_KEY', 'test-encryption-key-32-bytes-long!')
os.environ.setdefault('AI_SDK_API_KEY', 'test-ai-sdk-key')

from fastapi.testclient import TestClient
from src.main import app

# Create test client
client = TestClient(app)


class TestAISDKEndpoint:
    """Tests for the AI SDK chat completion endpoint"""

    def test_ai_sdk_endpoint_exists(self):
        """Test that the AI SDK endpoint is registered"""
        response = client.options("/api/chat/ai-sdk")
        # OPTIONS should be allowed or return method not allowed
        assert response.status_code in [200, 405]

    def test_ai_sdk_completions_endpoint_exists(self):
        """Test that the AI SDK completions endpoint is registered"""
        response = client.options("/api/chat/ai-sdk-completions")
        # OPTIONS should be allowed or return method not allowed
        assert response.status_code in [200, 405]

    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai")
    @patch("src.routes.ai_sdk.process_ai_sdk_response")
    def test_ai_sdk_chat_completion_success(
        self, mock_process, mock_request, mock_validate
    ):
        """Test successful AI SDK chat completion request"""
        # Setup mocks
        mock_validate.return_value = "test-api-key"

        # Mock response from OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_request.return_value = mock_response

        # Mock processed response
        mock_process.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        # Make request using Vercel AI Gateway model format
        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openai/gpt-5",  # Vercel AI Gateway model format
                "messages": [{"role": "user", "content": "Hello!"}],
                "temperature": 0.7,
                "max_tokens": 100,
            },
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert "usage" in data
        assert data["choices"][0]["message"]["content"] == "Hello!"

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", True)
    @patch("src.routes.ai_sdk.sentry_sdk")
    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    def test_ai_sdk_missing_api_key(self, mock_validate, mock_sentry):
        """Test that missing API key returns proper error and captures to Sentry"""
        mock_validate.side_effect = ValueError("AI_SDK_API_KEY not configured")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 503
        assert "not configured" in response.text.lower()
        # Verify error was captured to Sentry
        mock_sentry.capture_exception.assert_called_once()

    def test_ai_sdk_invalid_request_format(self):
        """Test that invalid request format returns proper error"""
        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openai/gpt-5",
                # Missing required 'messages' field
            },
        )

        assert response.status_code == 422  # Validation error

    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai_stream")
    def test_ai_sdk_streaming_request(self, mock_stream, mock_validate):
        """Test streaming response from AI SDK endpoint"""
        mock_validate.return_value = "test-api-key"

        # Mock streaming response
        mock_chunk1 = MagicMock()
        mock_chunk1.choices = [MagicMock(delta=MagicMock(content="Hello"))]

        mock_chunk2 = MagicMock()
        mock_chunk2.choices = [MagicMock(delta=MagicMock(content=" world"))]

        mock_stream.return_value = iter([mock_chunk1, mock_chunk2])

        # Make streaming request using Vercel AI Gateway model format
        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openai/gpt-5",  # Vercel AI Gateway model format
                "messages": [{"role": "user", "content": "Hello!"}],
                "stream": True,
            },
        )

        assert response.status_code == 200
        # Check content-type contains text/event-stream (may have charset appended)
        assert "text/event-stream" in response.headers["content-type"]

    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai")
    @patch("src.routes.ai_sdk.process_ai_sdk_response")
    def test_ai_sdk_endpoint_schema(self, mock_process, mock_request, mock_validate):
        """Test that endpoint properly validates request schema"""
        # Setup mocks
        mock_validate.return_value = "test-api-key"

        # Mock response from OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="I am helpful."),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=15, completion_tokens=5, total_tokens=20
        )
        mock_request.return_value = mock_response

        # Mock processed response
        mock_process.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "I am helpful."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 5,
                "total_tokens": 20,
            },
        }

        # Valid request with all optional fields using Vercel AI Gateway format
        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "anthropic/claude-sonnet-4.5",  # Vercel AI Gateway model format
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Hello!"},
                ],
                "max_tokens": 1024,
                "temperature": 0.7,
                "top_p": 0.9,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "stream": False,
            },
        )

        # Should succeed with proper mocking
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert "usage" in data

    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai")
    @patch("src.routes.ai_sdk.process_ai_sdk_response")
    def test_ai_sdk_completions_endpoint_works(
        self, mock_process, mock_request, mock_validate
    ):
        """Test that the -completions variant endpoint works identically"""
        # Setup mocks
        mock_validate.return_value = "test-api-key"

        # Mock response from OpenAI client
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello from completions!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_request.return_value = mock_response

        # Mock processed response
        mock_process.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello from completions!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        # Make request to the -completions endpoint
        response = client.post(
            "/api/chat/ai-sdk-completions",
            json={
                "model": "openai/gpt-5",
                "messages": [{"role": "user", "content": "Hello!"}],
                "temperature": 0.7,
                "max_tokens": 100,
            },
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert "usage" in data
        assert data["choices"][0]["message"]["content"] == "Hello from completions!"


class TestAISDKConfiguration:
    """Tests for AI SDK configuration"""

    def test_ai_sdk_config_variable_exists(self):
        """Test that AI_SDK_API_KEY is available in Config"""
        from src.config import Config

        # Should have the attribute (may be None if not set)
        assert hasattr(Config, "AI_SDK_API_KEY")

    def test_ai_sdk_config_loading(self):
        """Test that AI_SDK_API_KEY is properly loaded from environment"""
        from src.config import Config

        # Config should have AI_SDK_API_KEY set from conftest.py
        # It should be set to a non-empty value during testing
        assert Config.AI_SDK_API_KEY is not None
        assert isinstance(Config.AI_SDK_API_KEY, str)
        assert len(Config.AI_SDK_API_KEY) > 0


class TestAISDKModelRouting:
    """Tests for AI SDK model routing (openrouter/* models go directly to OpenRouter)"""

    def test_is_openrouter_model_auto(self):
        """Test that openrouter/auto is detected correctly"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model("openrouter/auto") is True

    def test_is_openrouter_model_other_openrouter_models(self):
        """Test that other openrouter/* models are detected"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model("openrouter/quasar-alpha") is True
        assert _is_openrouter_model("openrouter/some-model") is True

    def test_is_openrouter_model_case_insensitive(self):
        """Test that openrouter/* detection is case insensitive"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model("OpenRouter/Auto") is True
        assert _is_openrouter_model("OPENROUTER/AUTO") is True
        assert _is_openrouter_model("OpenRouter/Quasar-Alpha") is True

    def test_is_openrouter_model_false_for_regular_models(self):
        """Test that regular models are not detected as openrouter/*"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model("openai/gpt-4o") is False
        assert _is_openrouter_model("anthropic/claude-3") is False
        assert _is_openrouter_model("google/gemini-pro") is False

    def test_is_openrouter_model_false_for_empty(self):
        """Test that empty model returns False"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model("") is False

    def test_is_openrouter_model_false_for_none(self):
        """Test that None model returns False"""
        from src.routes.ai_sdk import _is_openrouter_model

        assert _is_openrouter_model(None) is False

    @patch("src.routes.ai_sdk.make_openrouter_request_openai")
    @patch("src.routes.ai_sdk.process_openrouter_response")
    def test_openrouter_auto_routes_to_openrouter(
        self, mock_process, mock_request
    ):
        """Test that openrouter/auto is routed directly to OpenRouter"""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello from OpenRouter!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        mock_request.return_value = mock_response

        mock_process.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hello from OpenRouter!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 200
        # Verify that the request was made to OpenRouter with original model
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][1] == "openrouter/auto"

    @patch("src.routes.ai_sdk.make_openrouter_request_openai_stream")
    def test_openrouter_auto_streaming_routes_to_openrouter(self, mock_stream):
        """Test that openrouter/auto streaming is routed directly to OpenRouter"""
        mock_chunk1 = MagicMock()
        mock_chunk1.choices = [MagicMock(delta=MagicMock(content="Hello"))]

        mock_chunk2 = MagicMock()
        mock_chunk2.choices = [MagicMock(delta=MagicMock(content=" from OpenRouter!"))]

        mock_stream.return_value = iter([mock_chunk1, mock_chunk2])

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": "Hello!"}],
                "stream": True,
            },
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        # Verify that the request was made to OpenRouter
        mock_stream.assert_called_once()
        call_args = mock_stream.call_args
        assert call_args[0][1] == "openrouter/auto"


class TestAISDKErrorMessages:
    """Tests for proper error messages based on the service that failed"""

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", False)
    @patch("src.routes.ai_sdk.make_openrouter_request_openai")
    def test_openrouter_missing_api_key_shows_openrouter_error(self, mock_request):
        """Test that missing OpenRouter API key shows OpenRouter-specific error message"""
        # Simulate the error raised by get_openrouter_client() when API key is missing
        mock_request.side_effect = ValueError("OpenRouter API key not configured")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 503
        # Should mention OpenRouter, not AI SDK
        assert "openrouter" in response.text.lower()
        assert "ai sdk" not in response.text.lower()

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", False)
    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    def test_ai_sdk_missing_api_key_shows_ai_sdk_error(self, mock_validate):
        """Test that missing AI SDK API key shows AI SDK-specific error message"""
        mock_validate.side_effect = ValueError("AI_SDK_API_KEY not configured")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 503
        # Should mention AI SDK, not OpenRouter
        assert "ai sdk" in response.text.lower()

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", True)
    @patch("src.routes.ai_sdk.sentry_sdk")
    @patch("src.routes.ai_sdk.make_openrouter_request_openai")
    def test_openrouter_error_captured_to_sentry(self, mock_request, mock_sentry):
        """Test that OpenRouter configuration errors are captured to Sentry"""
        mock_request.side_effect = ValueError("OpenRouter API key not configured")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 503
        # Verify error was captured to Sentry
        mock_sentry.capture_exception.assert_called_once()


class TestAISDKSentryIntegration:
    """Tests for Sentry error capture in AI SDK endpoint"""

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", True)
    @patch("src.routes.ai_sdk.sentry_sdk")
    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai")
    def test_general_error_captured_to_sentry(
        self, mock_request, mock_validate, mock_sentry
    ):
        """Test that general errors are captured to Sentry"""
        mock_validate.return_value = "test-api-key"
        mock_request.side_effect = RuntimeError("API request failed")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 500
        # Verify error was captured to Sentry
        mock_sentry.capture_exception.assert_called_once()

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", False)
    @patch("src.routes.ai_sdk.validate_ai_sdk_api_key")
    def test_error_when_sentry_unavailable(self, mock_validate):
        """Test that errors still work when Sentry is unavailable"""
        mock_validate.side_effect = ValueError("AI_SDK_API_KEY not configured")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        # Should still return proper error response
        assert response.status_code == 503
        assert "not configured" in response.text.lower()

    @patch("src.routes.ai_sdk.SENTRY_AVAILABLE", True)
    @patch("src.routes.ai_sdk.sentry_sdk")
    @patch("src.routes.ai_sdk.make_ai_sdk_request_openai_stream")
    def test_streaming_error_captured_to_sentry(self, mock_stream, mock_sentry):
        """Test that streaming errors are captured to Sentry"""
        mock_stream.side_effect = RuntimeError("Streaming failed")

        response = client.post(
            "/api/chat/ai-sdk",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello!"}],
                "stream": True,
            },
        )

        assert response.status_code == 200  # SSE response starts
        # Read the streaming response
        content = b"".join(response.iter_bytes()).decode()
        assert "error" in content.lower()
        # Verify error was captured to Sentry
        mock_sentry.capture_exception.assert_called_once()
