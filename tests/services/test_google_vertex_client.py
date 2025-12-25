"""Tests for Google Vertex AI client"""

import json
import pytest
import sys
from unittest.mock import patch, MagicMock, Mock

# Mock Google Cloud dependencies before importing our module
# This allows tests to run even if google-cloud-aiplatform isn't installed
sys.modules['google'] = MagicMock()
sys.modules['google.auth'] = MagicMock()
sys.modules['google.auth.transport'] = MagicMock()
sys.modules['google.auth.transport.requests'] = MagicMock()
sys.modules['google.oauth2'] = MagicMock()
sys.modules['google.oauth2.service_account'] = MagicMock()

# Mock vertexai before importing our module (needed for lazy imports)
sys.modules['vertexai'] = MagicMock()
sys.modules['vertexai.generative_models'] = MagicMock()
sys.modules['google.protobuf'] = MagicMock()
sys.modules['google.protobuf.json_format'] = MagicMock()

# Now import our module (which will use the mocked dependencies)
try:
    from src.services.google_vertex_client import (
        make_google_vertex_request_openai,
        make_google_vertex_request_openai_stream,
        transform_google_vertex_model_id,
        _build_vertex_content,
        _process_google_vertex_rest_response,
        _ensure_vertex_imports,
        _ensure_protobuf_imports,
        _get_model_location,
    )
    GOOGLE_VERTEX_AVAILABLE = True
except ImportError:
    GOOGLE_VERTEX_AVAILABLE = False

from src.config import Config


@pytest.fixture
def force_sdk_transport(monkeypatch):
    """Force Vertex client to use SDK transport."""
    monkeypatch.setattr(Config, "GOOGLE_VERTEX_TRANSPORT", "sdk")
    yield


@pytest.fixture
def force_rest_transport(monkeypatch):
    """Force Vertex client to use REST transport."""
    monkeypatch.setattr(Config, "GOOGLE_VERTEX_TRANSPORT", "rest")
    yield


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestTransformGoogleVertexModelId:
    """Tests for model ID transformation"""

    def test_transform_simple_model_id(self):
        """Test transforming a simple model ID returns the model name"""
        result = transform_google_vertex_model_id("gemini-2.0-flash")
        assert result == "gemini-2.0-flash"

    def test_transform_full_resource_name(self):
        """Test that full resource names are extracted to simple model name"""
        model_id = "projects/my-project/locations/us-central1/publishers/google/models/gemini-2.0-flash"
        result = transform_google_vertex_model_id(model_id)
        assert result == "gemini-2.0-flash"

    def test_transform_various_models(self):
        """Test transforming various model IDs"""
        models = [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-1.0-pro",
            "gemini-2.5-flash-lite-preview-09-2025",
        ]
        for model in models:
            result = transform_google_vertex_model_id(model)
            assert result == model


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestBuildVertexContent:
    """Tests for content building"""

    def test_build_simple_text_content(self):
        """Test building content from simple text messages"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _build_vertex_content(messages)

        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["parts"][0]["text"] == "Hello"
        assert result[1]["role"] == "model"
        assert result[1]["parts"][0]["text"] == "Hi there!"

    def test_build_multimodal_content(self):
        """Test building multimodal content with images"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/image.jpg"}
                    },
                ],
            }
        ]
        result = _build_vertex_content(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["parts"]) >= 2

    def test_build_system_message(self):
        """Test that system messages are mapped to model role"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
        ]
        result = _build_vertex_content(messages)

        # System messages are not user messages, so they map to "model"
        # In Vertex AI, only "user" and "model" roles exist
        assert result[0]["role"] == "model"
        assert result[1]["role"] == "user"


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestProcessGoogleVertexResponse:
    """Tests for response processing"""

    def test_process_successful_rest_response(self):
        """Test processing a successful REST API response"""
        # Mock REST API response format (not protobuf)
        response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "This is a response"}
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
            }
        }

        result = _process_google_vertex_rest_response(response_data, "gemini-2.0-flash")

        assert result["model"] == "gemini-2.0-flash"
        assert result["choices"][0]["message"]["content"] == "This is a response"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5
        assert result["usage"]["total_tokens"] == 15

    def test_process_multiple_content_parts(self):
        """Test processing response with multiple content parts"""
        # Mock REST API response with multiple parts
        response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Part 1 "},
                            {"text": "Part 2"}
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
            }
        }

        result = _process_google_vertex_rest_response(response_data, "gemini-1.5-pro")

        assert result["choices"][0]["message"]["content"] == "Part 1 Part 2"

    def test_process_gemini_flash_lite_response(self):
        """Test processing response from gemini-2.5-flash-lite-preview-09-2025"""
        response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Flash Lite response"}
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 3,
            }
        }

        result = _process_google_vertex_rest_response(response_data, "gemini-2.5-flash-lite-preview-09-2025")

        assert result["model"] == "gemini-2.5-flash-lite-preview-09-2025"
        assert result["choices"][0]["message"]["content"] == "Flash Lite response"
        assert result["usage"]["total_tokens"] == 8


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
@pytest.mark.usefixtures("force_sdk_transport")
class TestMakeGoogleVertexRequest:
    """Tests for making requests to Google Vertex"""

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_make_request_with_parameters(self, mock_ensure_imports, mock_init_vertex):
        """Test making a request with various parameters"""
        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = Mock()
        mock_model_instance = Mock()
        mock_response = Mock()
        mock_response.text = "Response"
        mock_response.usage_metadata = Mock()
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 10
        mock_response.candidates = [Mock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance

        # Mock _ensure_vertex_imports to return our mocked GenerativeModel class
        mock_ensure_imports.return_value = (Mock(), mock_generative_model_class)

        messages = [
            {"role": "user", "content": "Hello"}
        ]

        result = make_google_vertex_request_openai(
            messages=messages,
            model="gemini-2.0-flash",
            max_tokens=100,
            temperature=0.7,
            top_p=0.9
        )

        assert "choices" in result
        assert result["model"] == "gemini-2.0-flash"
        assert "usage" in result
        assert result["choices"][0]["message"]["content"] == "Response"

        # Verify initialization and lazy import were called
        mock_init_vertex.assert_called_once()
        mock_ensure_imports.assert_called_once()

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_make_streaming_request(self, mock_ensure_imports, mock_init_vertex):
        """Test making a streaming request"""
        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = Mock()
        mock_model_instance = Mock()
        mock_response = Mock()
        mock_response.text = "Streaming response"
        mock_response.usage_metadata = Mock()
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 10
        mock_response.candidates = [Mock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance

        # Mock _ensure_vertex_imports to return our mocked GenerativeModel class
        mock_ensure_imports.return_value = (Mock(), mock_generative_model_class)

        messages = [
            {"role": "user", "content": "Hello"}
        ]

        # Get the generator
        gen = make_google_vertex_request_openai_stream(
            messages=messages,
            model="gemini-1.5-flash",
            max_tokens=100
        )

        # Collect all chunks
        chunks = list(gen)

        assert len(chunks) >= 2  # At least a content chunk and a DONE chunk
        assert any("Streaming response" in chunk for chunk in chunks)
        assert any("[DONE]" in chunk for chunk in chunks)

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_make_request_gemini_flash_lite(self, mock_ensure_imports, mock_init_vertex):
        """Test making a request to gemini-2.5-flash-lite (maps to preview version)"""
        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = Mock()
        mock_model_instance = Mock()
        mock_response = Mock()
        mock_response.text = "Flash Lite works!"
        mock_response.usage_metadata = Mock()
        mock_response.usage_metadata.prompt_token_count = 3
        mock_response.usage_metadata.candidates_token_count = 4
        mock_response.candidates = [Mock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance

        # Mock _ensure_vertex_imports to return our mocked GenerativeModel class
        mock_ensure_imports.return_value = (Mock(), mock_generative_model_class)

        messages = [
            {"role": "user", "content": "Test"}
        ]

        result = make_google_vertex_request_openai(
            messages=messages,
            model="gemini-2.5-flash-lite-preview-09-2025"
        )

        assert result["model"] == "gemini-2.5-flash-lite-preview-09-2025"
        assert result["choices"][0]["message"]["content"] == "Flash Lite works!"

        # Verify the lazy import was called
        mock_ensure_imports.assert_called_once()


class DummyHttpxResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class DummyHttpxClientFactory:
    """Factory that mimics httpx.Client context manager with predefined responses."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.payloads = []

    def __call__(self, *args, **kwargs):
        factory = self

        class _ClientCtx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

            def post(self_inner, url, headers=None, json=None):
                response = factory.responses[min(factory.calls, len(factory.responses) - 1)]
                factory.calls += 1
                factory.payloads.append({"url": url, "headers": headers, "json": json})
                return response

        return _ClientCtx()


class TestGoogleVertexRestTransport:
    """Tests for the REST fallback transport."""

    @pytest.mark.usefixtures("force_rest_transport")
    def test_rest_request_success(self, monkeypatch):
        """Ensure REST transport returns normalized response."""
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hello!"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "hi"}], model="gemini-2.0-flash"
        )

        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["usage"]["total_tokens"] == 5
        assert client_factory.calls == 1

    @pytest.mark.usefixtures("force_rest_transport")
    def test_rest_request_http_error(self, monkeypatch):
        """REST transport should raise ValueError on HTTP error."""
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        error_payload = {"error": {"message": "Something broke"}}
        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(500, error_payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        with pytest.raises(ValueError):
            make_google_vertex_request_openai(
                messages=[{"role": "user", "content": "hi"}], model="gemini-2.0-flash"
            )


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestGoogleVertexModelIntegration:
    """Integration tests for model detection and transformation"""

    @patch.dict('os.environ', {'GOOGLE_VERTEX_CREDENTIALS_JSON': '{"type":"service_account"}'})
    def test_gemini_model_detection(self):
        """Test that gemini models are properly detected when credentials are available"""
        from src.services.model_transformations import detect_provider_from_model_id

        models = [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "google/gemini-1.5-flash",
        ]

        for model in models:
            provider = detect_provider_from_model_id(model)
            assert provider == "google-vertex", f"Model {model} should detect as google-vertex, got {provider}"

    def test_model_id_transformation_consistency(self):
        """Test that model IDs are transformed consistently"""
        from src.services.model_transformations import transform_model_id

        result1 = transform_model_id("gemini-2.0-flash", "google-vertex")
        result2 = transform_model_id("gemini-2.0-flash", "google-vertex")

        assert result1 == result2
        assert "gemini-2.0-flash" in result1


class TestFetchModelsFromGoogleVertex:
    """Tests for fetch_models_from_google_vertex static model catalog"""

    def test_fetch_models_returns_list(self):
        """Test that fetch_models_from_google_vertex returns a list of models"""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        assert models is not None
        assert isinstance(models, list)
        assert len(models) > 0

    def test_fetch_models_includes_gemini_models(self):
        """Test that returned models include known Gemini models"""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()
        model_ids = [m["id"] for m in models]

        # Check for expected Gemini models
        expected_models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ]

        for expected in expected_models:
            assert expected in model_ids, f"Expected model {expected} not found in catalog"

    def test_fetch_models_normalized_format(self):
        """Test that returned models have the expected normalized format"""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Check first model has expected fields
        model = models[0]
        required_fields = [
            "id",
            "slug",
            "canonical_slug",
            "name",
            "description",
            "context_length",
            "architecture",
            "pricing",
            "source_gateway",
            "provider_slug",
        ]

        for field in required_fields:
            assert field in model, f"Missing required field: {field}"

        # Check source_gateway is set correctly
        assert model["source_gateway"] == "google-vertex"

    def test_fetch_models_updates_cache(self):
        """Test that fetch_models_from_google_vertex updates the cache"""
        from src.cache import _google_vertex_models_cache
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        # Clear cache first
        _google_vertex_models_cache["data"] = None
        _google_vertex_models_cache["timestamp"] = None

        models = fetch_models_from_google_vertex()

        assert _google_vertex_models_cache["data"] is not None
        assert _google_vertex_models_cache["timestamp"] is not None
        assert len(_google_vertex_models_cache["data"]) == len(models)


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestModelLocationRouting:
    """Tests for region-specific model routing"""

    def test_gemini_3_uses_global_endpoint(self, monkeypatch):
        """Test that Gemini 3 models use the global endpoint"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")

        # Test various Gemini 3 model names
        gemini_3_models = [
            "gemini-3-flash",
            "gemini-3-flash-preview",
            "gemini-3-pro",
            "GEMINI-3-FLASH",  # Test case insensitivity
        ]

        for model_name in gemini_3_models:
            location = _get_model_location(model_name)
            assert location == "global", f"Model {model_name} should use 'global' endpoint, got '{location}'"

    def test_gemini_2_uses_regional_endpoint(self, monkeypatch):
        """Test that Gemini 2.x models use the configured regional endpoint"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")

        # Test various Gemini 2.x model names
        gemini_2_models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-exp",
        ]

        for model_name in gemini_2_models:
            location = _get_model_location(model_name)
            assert location == "us-central1", f"Model {model_name} should use 'us-central1' endpoint, got '{location}'"

    def test_gemini_1_uses_regional_endpoint(self, monkeypatch):
        """Test that Gemini 1.x models use the configured regional endpoint"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "europe-west4")

        # Test various Gemini 1.x model names (though these are deprecated)
        gemini_1_models = [
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-1.0-pro",
        ]

        for model_name in gemini_1_models:
            location = _get_model_location(model_name)
            assert location == "europe-west4", f"Model {model_name} should use 'europe-west4' endpoint, got '{location}'"

    def test_gemma_models_use_regional_endpoint(self, monkeypatch):
        """Test that Gemma models use the configured regional endpoint"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-west1")

        gemma_models = [
            "gemma-2-9b-it",
            "gemma-2-27b-it",
        ]

        for model_name in gemma_models:
            location = _get_model_location(model_name)
            assert location == "us-west1", f"Model {model_name} should use 'us-west1' endpoint, got '{location}'"

    @pytest.mark.usefixtures("force_rest_transport")
    def test_rest_request_uses_global_for_gemini_3(self, monkeypatch):
        """Ensure REST transport constructs URL with global endpoint for Gemini 3"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Gemini 3 response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-3-flash-preview"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Gemini 3 response"

        # Verify the URL used the global endpoint
        # Global endpoint uses https://aiplatform.googleapis.com (no region prefix)
        assert client_factory.calls == 1
        request_url = client_factory.payloads[0]["url"]
        assert "https://aiplatform.googleapis.com/v1/" in request_url, f"URL should use global endpoint (no region prefix): {request_url}"
        assert "locations/global/" in request_url, f"URL should use global location: {request_url}"

    @pytest.mark.usefixtures("force_rest_transport")
    def test_rest_request_uses_regional_for_gemini_2(self, monkeypatch):
        """Ensure REST transport constructs URL with regional endpoint for Gemini 2"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Gemini 2.5 response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.5-flash"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Gemini 2.5 response"

        # Verify the URL used the regional endpoint
        assert client_factory.calls == 1
        request_url = client_factory.payloads[0]["url"]
        assert "us-central1-aiplatform.googleapis.com" in request_url, f"URL should use regional endpoint: {request_url}"
        assert "locations/us-central1/" in request_url, f"URL should use regional location: {request_url}"

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_sdk_request_uses_global_for_gemini_3(self, mock_ensure_imports, mock_init_vertex, monkeypatch):
        """Ensure SDK transport initializes with global location for Gemini 3"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_TRANSPORT", "sdk")

        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = Mock()
        mock_model_instance = Mock()
        mock_response = Mock()
        mock_response.text = "Gemini 3 SDK response"
        mock_response.usage_metadata = Mock()
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 10
        mock_response.candidates = [Mock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance
        mock_ensure_imports.return_value = (Mock(), mock_generative_model_class)

        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-3-flash-preview"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Gemini 3 SDK response"

        # Verify initialize_vertex_ai was called with location='global'
        mock_init_vertex.assert_called_once_with(location="global")


class TestMaxOutputTokensValidation:
    """Tests for maxOutputTokens validation and clamping."""

    @pytest.mark.usefixtures("force_rest_transport")
    def test_max_tokens_capped_at_65536(self, monkeypatch):
        """Test that max_tokens > 65536 is clamped to 65536 for Vertex AI."""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        # Request with max_tokens=100000 (greater than max 65536)
        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.5-flash",
            max_tokens=100000,  # Should be clamped to 65536
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Response"

        # Verify the maxOutputTokens was clamped to 65536
        request_body = client_factory.payloads[0]["json"]
        assert "generationConfig" in request_body
        assert request_body["generationConfig"]["maxOutputTokens"] == 65536

    @pytest.mark.usefixtures("force_rest_transport")
    def test_max_tokens_minimum_is_16(self, monkeypatch):
        """Test that max_tokens < 16 is raised to 16 for Vertex AI."""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        # Request with max_tokens=5 (less than min 16)
        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.5-flash",
            max_tokens=5,  # Should be raised to 16
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Response"

        # Verify the maxOutputTokens was raised to 16
        request_body = client_factory.payloads[0]["json"]
        assert "generationConfig" in request_body
        assert request_body["generationConfig"]["maxOutputTokens"] == 16

    @pytest.mark.usefixtures("force_rest_transport")
    def test_valid_max_tokens_unchanged(self, monkeypatch):
        """Test that valid max_tokens values are not modified."""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        # Request with valid max_tokens=4096
        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.5-flash",
            max_tokens=4096,  # Should remain unchanged
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Response"

        # Verify the maxOutputTokens was not modified
        request_body = client_factory.payloads[0]["json"]
        assert "generationConfig" in request_body
        assert request_body["generationConfig"]["maxOutputTokens"] == 4096


@pytest.mark.skipif(not GOOGLE_VERTEX_AVAILABLE, reason="Google Vertex AI SDK not available")
class TestVertexTimeoutRetry:
    """Tests for timeout retry logic"""

    @pytest.mark.usefixtures("force_rest_transport")
    def test_timeout_retry_success_on_second_attempt(self, monkeypatch):
        """Test that timeout triggers retry and succeeds on second attempt."""
        import httpx
        from src.services.google_vertex_client import _make_google_vertex_request_rest

        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        # First call times out, second succeeds
        call_count = 0

        class TimeoutThenSuccessClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, headers, json):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise httpx.ReadTimeout("Connection timed out")
                # Second call succeeds
                response = Mock()
                response.status_code = 200
                response.json.return_value = {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Success after retry"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
                }
                return response

        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", TimeoutThenSuccessClient)
        monkeypatch.setattr("src.services.google_vertex_client.time.sleep", lambda x: None)

        result = _make_google_vertex_request_rest(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.5-flash",
            max_tokens=100,
        )

        # Should succeed after retry
        assert result["choices"][0]["message"]["content"] == "Success after retry"
        assert call_count == 2  # One timeout, one success

    @pytest.mark.usefixtures("force_rest_transport")
    def test_timeout_exhausts_retries(self, monkeypatch):
        """Test that timeout raises error after exhausting retries."""
        import httpx
        from src.services.google_vertex_client import _make_google_vertex_request_rest

        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        call_count = 0

        class AlwaysTimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, headers, json):
                nonlocal call_count
                call_count += 1
                raise httpx.ReadTimeout("Connection timed out")

        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", AlwaysTimeoutClient)
        monkeypatch.setattr("src.services.google_vertex_client.time.sleep", lambda x: None)

        # Should fail after max retries
        with pytest.raises(ValueError, match="timed out after .* retries"):
            _make_google_vertex_request_rest(
                messages=[{"role": "user", "content": "test"}],
                model="gemini-2.5-flash",
                max_tokens=100,
                vertex_max_retries=2,
            )

        # Should have attempted 3 times (initial + 2 retries)
        assert call_count == 3

    @pytest.mark.usefixtures("force_rest_transport")
    def test_timeout_custom_retry_count(self, monkeypatch):
        """Test that custom retry count is respected."""
        import httpx
        from src.services.google_vertex_client import _make_google_vertex_request_rest

        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        call_count = 0

        class AlwaysTimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, headers, json):
                nonlocal call_count
                call_count += 1
                raise httpx.ReadTimeout("Connection timed out")

        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", AlwaysTimeoutClient)
        monkeypatch.setattr("src.services.google_vertex_client.time.sleep", lambda x: None)

        # Test with 0 retries (should fail immediately)
        with pytest.raises(ValueError, match="timed out after .* retries"):
            _make_google_vertex_request_rest(
                messages=[{"role": "user", "content": "test"}],
                model="gemini-2.5-flash",
                max_tokens=100,
                vertex_max_retries=0,
            )

        # Should have attempted only once
        assert call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
