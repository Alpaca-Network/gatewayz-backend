"""Unit tests for OneRouter client"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
from datetime import UTC


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


class TestParseTokenLimit:
    """Test _parse_token_limit helper function"""

    def test_parse_token_limit_int(self):
        """Test parsing integer value"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(131072) == 131072
        assert _parse_token_limit(4096) == 4096

    def test_parse_token_limit_string(self):
        """Test parsing string value"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("131072") == 131072
        assert _parse_token_limit("4096") == 4096

    def test_parse_token_limit_string_with_commas(self):
        """Test parsing string value with commas"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("131,072") == 131072
        assert _parse_token_limit("1,048,576") == 1048576

    def test_parse_token_limit_none(self):
        """Test parsing None value"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(None) == 4096

    def test_parse_token_limit_invalid_string(self):
        """Test parsing invalid string values returns default"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit("") == 4096
        assert _parse_token_limit("unlimited") == 4096
        assert _parse_token_limit("N/A") == 4096
        assert _parse_token_limit("abc") == 4096

    def test_parse_token_limit_float(self):
        """Test parsing float values"""
        from src.services.onerouter_client import _parse_token_limit

        assert _parse_token_limit(128000.0) == 128000
        assert _parse_token_limit(4096.5) == 4096


class TestParsePricing:
    """Test _parse_pricing helper function"""

    def test_parse_pricing_with_dollar_sign(self):
        """Test parsing pricing with $ sign"""
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$0.10") == "0.10"
        assert _parse_pricing("$2.50") == "2.50"

    def test_parse_pricing_without_dollar_sign(self):
        """Test parsing pricing without $ sign"""
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("0.10") == "0.10"
        assert _parse_pricing("2.50") == "2.50"

    def test_parse_pricing_zero(self):
        """Test parsing zero pricing"""
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$0") == "0"
        assert _parse_pricing("0") == "0"

    def test_parse_pricing_none(self):
        """Test parsing None value"""
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing(None) == "0"

    def test_parse_pricing_with_commas(self):
        """Test parsing pricing with commas"""
        from src.services.onerouter_client import _parse_pricing

        assert _parse_pricing("$1,000.50") == "1000.50"
        assert _parse_pricing("1,234.56") == "1234.56"


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

    def test_fetch_models_success_with_caching_and_pricing(self, mock_onerouter_api_key):
        """Test successful model fetch with pricing enrichment from display_models"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache
        from datetime import datetime, timezone

        # Mock /v1/models response (complete list)
        mock_v1_models_response = {
            "data": [
                {"id": "gemini-2.0-flash", "object": "model", "created": 1234567890, "owned_by": "google"},
                {"id": "deepseek-v3-250324", "object": "model", "created": 1234567890, "owned_by": "deepseek"},
                {"id": "model-without-pricing", "object": "model", "created": 1234567890, "owned_by": "test"}
            ]
        }

        # Mock display_models response (pricing data for some models)
        mock_display_models_response = {
            "data": [
                {
                    "invoke_name": "gemini-2.0-flash",
                    "name": "gemini-2.0-flash",
                    "sale_input_cost": "$0",
                    "sale_output_cost": "$0",
                    "retail_input_cost": "$0.10",
                    "retail_output_cost": "$0.40",
                    "input_token_limit": "1048576",
                    "output_token_limit": "8192",
                    "input_modalities": "Text, Code, Images",
                    "output_modalities": "Text, Code",
                },
                {
                    "invoke_name": "deepseek-v3-250324",
                    "name": "deepseek-v3-250324",
                    "sale_input_cost": "$1.14",
                    "sale_output_cost": "$4.56",
                    "retail_input_cost": "$1.14",
                    "retail_output_cost": "$4.56",
                    "input_token_limit": "16,384",
                    "output_token_limit": "65,536",
                    "input_modalities": "Text",
                    "output_modalities": "Text",
                }
            ]
        }

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            # Return different responses based on URL
            def side_effect(url, **kwargs):
                mock_response = Mock()
                mock_response.raise_for_status = Mock()
                if "v1/models" in url:
                    mock_response.json.return_value = mock_v1_models_response
                else:
                    mock_response.json.return_value = mock_display_models_response
                return mock_response

            mock_get.side_effect = side_effect

            # Verify cache is empty before fetch
            assert _onerouter_models_cache["data"] is None

            # Fetch models
            models = fetch_models_from_onerouter()

            # Verify all 3 models were returned
            assert len(models) == 3

            # First model - has pricing and multimodal info from display_models
            assert models[0]["id"] == "onerouter/gemini-2.0-flash"
            assert models[0]["slug"] == "gemini-2.0-flash"
            assert models[0]["context_length"] == 1048576
            assert models[0]["max_completion_tokens"] == 8192
            assert models[0]["pricing"]["prompt"] == "0.10"
            assert models[0]["pricing"]["completion"] == "0.40"
            assert models[0]["architecture"]["modality"] == "text+image->text"
            assert "images" in models[0]["architecture"]["input_modalities"]

            # Second model - has pricing from display_models (text only)
            assert models[1]["id"] == "onerouter/deepseek-v3-250324"
            assert models[1]["pricing"]["prompt"] == "1.14"
            assert models[1]["pricing"]["completion"] == "4.56"
            assert models[1]["architecture"]["modality"] == "text->text"

            # Third model - no pricing data, uses defaults
            assert models[2]["id"] == "onerouter/model-without-pricing"
            assert models[2]["context_length"] == 128000  # default
            assert models[2]["pricing"]["prompt"] == "0"  # default
            assert models[2]["architecture"]["modality"] == "text->text"  # default

            # Verify cache was populated
            assert _onerouter_models_cache["data"] == models
            assert _onerouter_models_cache["timestamp"] is not None
            assert isinstance(_onerouter_models_cache["timestamp"], datetime)

            # Verify timestamp is recent (within last 5 seconds)
            cache_age = (datetime.now(UTC) - _onerouter_models_cache["timestamp"]).total_seconds()
            assert cache_age < 5

    def test_fetch_models_context_length_priority(self, mock_onerouter_api_key):
        """Test that context_length is prioritized over context_window"""
        # TODO: Add implementation
        pytest.skip("Test implementation pending")

    def test_fetch_models_skip_empty_model_id(self, mock_onerouter_api_key):
        """Test that models without id are skipped"""
        from src.services.onerouter_client import fetch_models_from_onerouter

        mock_v1_models_response = {
            "data": [
                {"id": "", "object": "model", "created": 1234567890, "owned_by": "test"},
                {"id": "valid-model", "object": "model", "created": 1234567890, "owned_by": "test"}
            ]
        }

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            def side_effect(url, **kwargs):
                mock_response = Mock()
                mock_response.raise_for_status = Mock()
                if "v1/models" in url:
                    mock_response.json.return_value = mock_v1_models_response
                else:
                    mock_response.json.return_value = {"data": []}
                return mock_response

            mock_get.side_effect = side_effect

            models = fetch_models_from_onerouter()

            # Only valid model should be included
            assert len(models) == 1
            assert models[0]["id"] == "onerouter/valid-model"
            assert models[0]["slug"] == "valid-model"

    def test_fetch_models_http_error_with_caching(self, mock_onerouter_api_key):
        """Test HTTP error handling and verify cache is still updated"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache
        import httpx

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            # Create a proper mock response for HTTPStatusError
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"

            # Simulate HTTP error
            mock_get.side_effect = httpx.HTTPStatusError(
                "404 Not Found",
                request=Mock(),
                response=mock_response
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

    def test_fetch_models_uses_correct_endpoints(self, mock_onerouter_api_key):
        """Test that fetch_models_from_onerouter calls both /v1/models and display_models"""
        from src.services.onerouter_client import fetch_models_from_onerouter

        with patch('src.services.onerouter_client.httpx.get') as mock_get:
            def side_effect(url, **kwargs):
                mock_response = Mock()
                mock_response.raise_for_status = Mock()
                mock_response.json.return_value = {"data": []}
                return mock_response

            mock_get.side_effect = side_effect

            fetch_models_from_onerouter()

            # Verify both endpoints were called
            assert mock_get.call_count == 2

            # Check the /v1/models call (first call)
            v1_call = mock_get.call_args_list[0]
            assert v1_call[0][0] == "https://api.infron.ai/v1/models"
            assert "Authorization" in v1_call[1]["headers"]
            assert v1_call[1]["headers"]["Authorization"] == "Bearer test_onerouter_key_123"

            # Check the display_models call (second call - from pricing enrichment)
            display_call = mock_get.call_args_list[1]
            assert display_call[0][0] == "https://app.infron.ai/api/display_models/"

    def test_fetch_models_missing_api_key(self):
        """Test that fetch returns empty list when API key is not configured"""
        from src.services.onerouter_client import fetch_models_from_onerouter
        from src.cache import _onerouter_models_cache

        with patch('src.services.onerouter_client.Config') as mock_config:
            mock_config.ONEROUTER_API_KEY = None

            models = fetch_models_from_onerouter()

            # Should return empty list when API key is missing
            assert models == []
            # Cache should be populated with empty list
            assert _onerouter_models_cache["data"] == []
