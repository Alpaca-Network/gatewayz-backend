"""Unit tests for OneRouter client"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
from datetime 


@pytest.fixture
def mock_onerouter_api_key():
    """Mock the OneRouter API key"""
    with patch('src.services.onerouter_client.Config') as mock_config:
        mock_config.ONEROUTER_API_KEY = "test_onerouter_key_123"
        yield mock_config


@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI-style response"""
    mock_response = Mock()
    mock_response.id = "chatcmpl-test123"
    mock_response.object = "chat.completion"
    mock_response.created = 1234567890
    mock_response.model = "claude-3-5-sonnet@20240620"

    # Mock choice
    mock_choice = Mock()
    mock_choice.index = 0
    mock_choice.message = Mock()
    mock_choice.message.role = "assistant"
    mock_choice.message.content = "Hello! How can I help you?"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    # Mock usage
    mock_response.usage = Mock()
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 8
    mock_response.usage.total_tokens = 18

    return mock_response


@pytest.fixture
def mock_openai_response_no_usage():
    """Create a mock response without usage data"""
    mock_response = Mock()
    mock_response.id = "chatcmpl-test123"
    mock_response.object = "chat.completion"
    mock_response.created = 1234567890
    mock_response.model = "claude-3-5-sonnet@20240620"

    mock_choice = Mock()
    mock_choice.index = 0
    mock_choice.message = Mock()
    mock_choice.message.role = "assistant"
    mock_choice.message.content = "Test response"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    mock_response.usage = None

    return mock_response


class TestGetOneRouterClient:
    """Test get_onerouter_client function"""

    def test_get_onerouter_client_success(self, mock_onerouter_api_key):
        """Test successful client initialization"""
        from src.services.onerouter_client import get_onerouter_client

        mock_client = Mock()
        with patch('src.services.onerouter_client.get_onerouter_pooled_client') as mock_get_pooled:
            mock_get_pooled.return_value = mock_client

            client = get_onerouter_client()

            assert client is mock_client
            mock_get_pooled.assert_called_once()

    def test_get_onerouter_client_missing_key(self):
        """Test client initialization with missing API key"""
        from src.services.onerouter_client import get_onerouter_client

        with patch('src.services.onerouter_client.Config') as mock_config:
            mock_config.ONEROUTER_API_KEY = None

            with pytest.raises(ValueError, match="OneRouter API key not configured"):
                get_onerouter_client()


class TestMakeOneRouterRequest:
    """Test make_onerouter_request_openai function"""

    def test_make_onerouter_request_openai_forwards_args(
        self, mock_onerouter_api_key, mock_openai_response
    ):
        """Test that request forwards all arguments correctly"""
        from src.services.onerouter_client import make_onerouter_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        model = "claude-3-5-sonnet@20240620"

        with patch('src.services.onerouter_client.get_onerouter_client') as mock_get_client:
            mock_client = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_openai_response
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            response = make_onerouter_request_openai(
                messages=messages,
                model=model,
                max_tokens=100,
                temperature=0.7
            )

            assert response == mock_openai_response
            mock_completions.create.assert_called_once_with(
                model=model,
                messages=messages,
                max_tokens=100,
                temperature=0.7
            )

    def test_make_onerouter_request_openai_error(self, mock_onerouter_api_key):
        """Test request error handling"""
        from src.services.onerouter_client import make_onerouter_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        model = "claude-3-5-sonnet@20240620"

        with patch('src.services.onerouter_client.get_onerouter_client') as mock_get_client:
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception, match="API error"):
                make_onerouter_request_openai(messages=messages, model=model)


class TestMakeOneRouterRequestStream:
    """Test make_onerouter_request_openai_stream function"""

    def test_make_onerouter_request_openai_stream(self, mock_onerouter_api_key):
        """Test streaming request"""
        from src.services.onerouter_client import make_onerouter_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        model = "claude-3-5-sonnet@20240620"
        mock_stream = Mock()

        with patch('src.services.onerouter_client.get_onerouter_client') as mock_get_client:
            mock_client = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_stream
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            stream = make_onerouter_request_openai_stream(
                messages=messages,
                model=model,
                max_tokens=100
            )

            assert stream == mock_stream
            mock_completions.create.assert_called_once_with(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=100
            )

    def test_make_onerouter_request_openai_stream_error(self, mock_onerouter_api_key):
        """Test streaming request error handling"""
        from src.services.onerouter_client import make_onerouter_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        model = "claude-3-5-sonnet@20240620"

        with patch('src.services.onerouter_client.get_onerouter_client') as mock_get_client:
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("Streaming error")
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception, match="Streaming error"):
                make_onerouter_request_openai_stream(messages=messages, model=model)


class TestProcessOneRouterResponse:
    """Test process_onerouter_response function"""

    def test_process_onerouter_response_happy(self, mock_openai_response):
        """Test processing a normal response"""
        from src.services.onerouter_client import process_onerouter_response

        processed = process_onerouter_response(mock_openai_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["created"] == 1234567890
        assert processed["model"] == "claude-3-5-sonnet@20240620"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["role"] == "assistant"
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 10
        assert processed["usage"]["completion_tokens"] == 8
        assert processed["usage"]["total_tokens"] == 18

    def test_process_onerouter_response_no_usage(self, mock_openai_response_no_usage):
        """Test processing response without usage data"""
        from src.services.onerouter_client import process_onerouter_response

        processed = process_onerouter_response(mock_openai_response_no_usage)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["model"] == "claude-3-5-sonnet@20240620"
        assert len(processed["choices"]) == 1
        assert processed["usage"] == {}

    def test_process_onerouter_response_error(self):
        """Test processing error handling"""
        from src.services.onerouter_client import process_onerouter_response

        bad_response = Mock()
        bad_response.id = None  # This will cause an error

        # The function doesn't explicitly handle errors, so it should raise
        with pytest.raises(Exception):
            process_onerouter_response(bad_response)


class TestFetchModelsFromOneRouter:
    """Test fetch_models_from_onerouter function with caching"""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the cache before each test"""
        from src.cache import _onerouter_models_cache
        _onerouter_models_cache["data"] = None
        _onerouter_models_cache["timestamp"] = None
        yield
        # Clean up after test
        _onerouter_models_cache["data"] = None
        _onerouter_models_cache["timestamp"] = None

    def test_fetch_models_success_with_caching(self, mock_onerouter_api_key):
        """Test successful model fetch and verify cache is populated"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache
        from datetime import datetime, timezone

        mock_models_response = {
            "data": [
                {
                    "id": "claude-3-5-sonnet@20240620",
                    "context_length": 200000,
                    "owned_by": "anthropic"
                },
                {
                    "id": "gpt-4o@latest",
                    "context_window": 128000,
                    "owned_by": "openai"
                }
            ]
        }

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = mock_models_response
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            # Verify cache is empty before fetch
            assert _onerouter_models_cache["data"] is None
            assert _onerouter_models_cache["timestamp"] is None

            # Fetch models
            models = fetch_models_from_onerouter()

            # Verify models were returned
            assert len(models) == 2
            assert models[0]["id"] == "claude-3-5-sonnet@20240620"
            assert models[0]["context_length"] == 200000
            assert models[1]["id"] == "gpt-4o@latest"
            assert models[1]["context_length"] == 128000  # Should use context_window fallback

            # Verify cache was populated
            assert _onerouter_models_cache["data"] == models
            assert _onerouter_models_cache["timestamp"] is not None
            assert isinstance(_onerouter_models_cache["timestamp"], datetime)

            # Verify timestamp is recent (within last 5 seconds)
            cache_age = (datetime.now(timezone.utc) - _onerouter_models_cache["timestamp"]).total_seconds()
            assert cache_age < 5

    def test_fetch_models_context_length_priority(self, mock_onerouter_api_key):
        """Test that context_length is prioritized over context_window"""
        from src.services.onerouter_client import fetch_models_from_onerouter

        mock_models_response = {
            "data": [
                {
                    "id": "test-model",
                    "context_length": 100000,
                    "context_window": 50000  # Should be ignored
                }
            ]
        }

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = mock_models_response
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            models = fetch_models_from_onerouter()

            # Should use context_length, not context_window
            assert models[0]["context_length"] == 100000

    def test_fetch_models_no_api_key(self):
        """Test fetch when API key is not configured"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache

        with patch('src.services.onerouter_client.Config') as mock_config:
            mock_config.ONEROUTER_API_KEY = None

            models = fetch_models_from_onerouter()

            # Should return empty list
            assert models == []

            # Cache should still be populated (with empty list)
            assert _onerouter_models_cache["data"] == []
            assert _onerouter_models_cache["timestamp"] is not None

    def test_fetch_models_http_error_with_caching(self, mock_onerouter_api_key):
        """Test HTTP error handling and verify cache is still updated"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache
        import httpx

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            # Simulate HTTP error
            mock_get.side_effect = httpx.HTTPStatusError(
                "404 Not Found",
                request=Mock(),
                response=Mock(status_code=404)
            )

            models = fetch_models_from_onerouter()

            # Should return empty list on error
            assert models == []

            # Cache should be populated with empty list to prevent repeated failed requests
            assert _onerouter_models_cache["data"] == []
            assert _onerouter_models_cache["timestamp"] is not None

    def test_fetch_models_generic_error_with_caching(self, mock_onerouter_api_key):
        """Test generic error handling and verify cache is updated"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            # Simulate generic error
            mock_get.side_effect = Exception("Network timeout")

            models = fetch_models_from_onerouter()

            # Should return empty list on error
            assert models == []

            # Cache should be populated to prevent repeated errors
            assert _onerouter_models_cache["data"] == []
            assert _onerouter_models_cache["timestamp"] is not None
