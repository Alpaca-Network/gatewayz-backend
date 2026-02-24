"""
Comprehensive tests for Helicone Client service
"""

from unittest.mock import Mock, patch

import pytest


class TestHeliconeClient:
    """Test Helicone Client service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.helicone_client

        assert src.services.helicone_client is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import helicone_client

        assert hasattr(helicone_client, "get_helicone_client")
        assert hasattr(helicone_client, "make_helicone_request_openai")
        assert hasattr(helicone_client, "make_helicone_request_openai_stream")
        assert hasattr(helicone_client, "process_helicone_response")
        assert hasattr(helicone_client, "fetch_model_pricing_from_helicone")
        assert hasattr(helicone_client, "get_provider_pricing_for_helicone_model")


class TestGetHeliconeClient:
    """Test get_helicone_client function"""

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test-key")
    def test_get_helicone_client(self):
        """Test getting Helicone client with valid API key"""
        from src.services.helicone_client import get_helicone_client

        client = get_helicone_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://ai-gateway.helicone.ai/v1"

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", None)
    def test_get_helicone_client_no_key(self):
        """Test getting Helicone client without API key raises error"""
        from src.services.helicone_client import get_helicone_client

        with pytest.raises(ValueError, match="Helicone AI Gateway API key not configured"):
            get_helicone_client()

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "")
    def test_get_helicone_client_empty_key(self):
        """Test getting Helicone client with empty API key raises error"""
        from src.services.helicone_client import get_helicone_client

        with pytest.raises(ValueError, match="Helicone AI Gateway API key not configured"):
            get_helicone_client()


class TestMakeHeliconeRequest:
    """Test make_helicone_request_openai function"""

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request(self, mock_get_client):
        """Test making request to Helicone AI Gateway"""
        from src.services.helicone_client import make_helicone_request_openai

        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "gpt-4o-mini"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_helicone_request_openai(messages, "gpt-4o-mini")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request_with_kwargs(self, mock_get_client):
        """Test making request with additional parameters"""
        from src.services.helicone_client import make_helicone_request_openai

        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_helicone_request_openai(
            messages, "gpt-4o-mini", max_tokens=100, temperature=0.7
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request_error(self, mock_get_client):
        """Test handling errors from Helicone"""
        from src.services.helicone_client import make_helicone_request_openai

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_helicone_request_openai(messages, "gpt-4o-mini")


class TestMakeHeliconeRequestStream:
    """Test make_helicone_request_openai_stream function"""

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request_stream(self, mock_get_client):
        """Test making streaming request to Helicone"""
        from src.services.helicone_client import make_helicone_request_openai_stream

        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_helicone_request_openai_stream(messages, "gpt-4o-mini")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini", messages=messages, stream=True
        )

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request with additional parameters"""
        from src.services.helicone_client import make_helicone_request_openai_stream

        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_helicone_request_openai_stream(
            messages, "gpt-4o-mini", max_tokens=100, temperature=0.5
        )

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini",
            messages=messages,
            stream=True,
            max_tokens=100,
            temperature=0.5,
        )

    @patch("src.services.helicone_client.get_helicone_client")
    def test_make_helicone_request_stream_error(self, mock_get_client):
        """Test handling errors during streaming"""
        from src.services.helicone_client import make_helicone_request_openai_stream

        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_helicone_request_openai_stream(messages, "gpt-4o-mini")


class TestProcessHeliconeResponse:
    """Test process_helicone_response function"""

    def test_process_helicone_response(self):
        """Test processing Helicone response"""
        from src.services.helicone_client import process_helicone_response

        # Create mock response
        mock_message = Mock()
        mock_message.content = "Hello, world!"
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
        mock_response.model = "gpt-4o-mini"
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        result = process_helicone_response(mock_response)

        assert result["id"] == "chatcmpl-123"
        assert result["object"] == "chat.completion"
        assert result["created"] == 1234567890
        assert result["model"] == "gpt-4o-mini"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["index"] == 0
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 20
        assert result["usage"]["total_tokens"] == 30

    def test_process_helicone_response_no_usage(self):
        """Test processing response without usage data"""
        from src.services.helicone_client import process_helicone_response

        mock_message = Mock()
        mock_message.content = "Hello!"
        mock_message.role = "assistant"
        mock_message.tool_calls = None

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = Mock()
        mock_response.id = "chatcmpl-456"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "gpt-4o-mini"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        result = process_helicone_response(mock_response)

        assert result["id"] == "chatcmpl-456"
        assert result["usage"] == {}

    def test_process_helicone_response_multiple_choices(self):
        """Test processing response with multiple choices"""
        from src.services.helicone_client import process_helicone_response

        def create_mock_choice(index, content):
            mock_message = Mock()
            mock_message.content = content
            mock_message.role = "assistant"
            mock_message.tool_calls = None

            mock_choice = Mock()
            mock_choice.index = index
            mock_choice.message = mock_message
            mock_choice.finish_reason = "stop"
            return mock_choice

        mock_usage = Mock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 40
        mock_usage.total_tokens = 50

        mock_response = Mock()
        mock_response.id = "chatcmpl-789"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "gpt-4o-mini"
        mock_response.choices = [
            create_mock_choice(0, "Response 1"),
            create_mock_choice(1, "Response 2"),
        ]
        mock_response.usage = mock_usage

        result = process_helicone_response(mock_response)

        assert len(result["choices"]) == 2
        assert result["choices"][0]["index"] == 0
        assert result["choices"][1]["index"] == 1


class TestFetchModelPricing:
    """Test fetch_model_pricing_from_helicone function"""

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", None)
    def test_fetch_pricing_no_api_key(self):
        """Test fetching pricing without API key returns None"""
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "placeholder-key")
    def test_fetch_pricing_placeholder_key(self):
        """Test fetching pricing with placeholder key returns None"""
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_during_catalog_build(self, mock_is_building):
        """Test fetching pricing during catalog build returns None"""
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = True

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")
        assert result is None

    @patch("src.services.helicone_client.httpx.get")
    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_from_api(self, mock_is_building, mock_get):
        """Test fetching pricing from Helicone API"""
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = False

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"gpt-4o-mini": {"prompt": "0.15", "completion": "0.60"}}
        mock_get.return_value = mock_response

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")

        assert result == {"prompt": "0.15", "completion": "0.60"}

    @patch("src.services.helicone_client.httpx.get")
    @patch("src.services.helicone_client.get_provider_pricing_for_helicone_model")
    @patch("src.services.models._is_building_catalog")
    @patch("src.services.helicone_client.Config.HELICONE_API_KEY", "sk-helicone-test")
    def test_fetch_pricing_fallback_to_provider(
        self, mock_is_building, mock_get_provider_pricing, mock_get
    ):
        """Test fetching pricing falls back to provider lookup"""
        from src.services.helicone_client import fetch_model_pricing_from_helicone

        mock_is_building.return_value = False

        # Simulate Helicone API returning 404
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        mock_get_provider_pricing.return_value = {"prompt": "0.10", "completion": "0.30"}

        result = fetch_model_pricing_from_helicone("gpt-4o-mini")

        assert result == {"prompt": "0.10", "completion": "0.30"}
        mock_get_provider_pricing.assert_called_once_with("gpt-4o-mini")


class TestGetProviderPricing:
    """Test get_provider_pricing_for_helicone_model function"""

    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_during_catalog_build(self, mock_is_building):
        """Test getting provider pricing during catalog build returns None"""
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = True

        result = get_provider_pricing_for_helicone_model("gpt-4o-mini")
        assert result is None

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_found(self, mock_is_building, mock_get_pricing):
        """Test getting provider pricing when found"""
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False
        mock_get_pricing.return_value = {
            "found": True,
            "prompt": "0.15",
            "completion": "0.60",
        }

        result = get_provider_pricing_for_helicone_model("gpt-4o-mini")

        assert result == {"prompt": "0.15", "completion": "0.60"}

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_not_found(self, mock_is_building, mock_get_pricing):
        """Test getting provider pricing when not found"""
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False
        mock_get_pricing.return_value = {"found": False}

        result = get_provider_pricing_for_helicone_model("unknown-model")
        assert result is None

    @patch("src.services.pricing.get_model_pricing")
    @patch("src.services.models._is_building_catalog")
    def test_get_provider_pricing_with_prefix(self, mock_is_building, mock_get_pricing):
        """Test getting provider pricing with provider prefix"""
        from src.services.helicone_client import get_provider_pricing_for_helicone_model

        mock_is_building.return_value = False

        # First call with full name returns not found
        # Second call with model name only returns pricing
        mock_get_pricing.side_effect = [
            {"found": False},
            {"found": True, "prompt": "0.15", "completion": "0.60"},
        ]

        result = get_provider_pricing_for_helicone_model("openai/gpt-4o-mini")

        assert result == {"prompt": "0.15", "completion": "0.60"}
        assert mock_get_pricing.call_count == 2


class TestHeliconeTimeout:
    """Test Helicone timeout configuration"""

    def test_helicone_timeout_values(self):
        """Test that timeout values are correctly configured"""
        from src.services.helicone_client import HELICONE_TIMEOUT

        assert HELICONE_TIMEOUT.connect == 5.0
        assert HELICONE_TIMEOUT.read == 60.0
        assert HELICONE_TIMEOUT.write == 10.0
        assert HELICONE_TIMEOUT.pool == 5.0
