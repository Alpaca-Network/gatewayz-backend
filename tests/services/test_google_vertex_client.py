"""Tests for Google Vertex AI client"""

import json
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock Google Cloud dependencies before importing our module
# This allows tests to run even if google-cloud-aiplatform isn't installed
sys.modules["google"] = MagicMock()
sys.modules["google.auth"] = MagicMock()
sys.modules["google.auth.transport"] = MagicMock()
sys.modules["google.auth.transport.requests"] = MagicMock()
sys.modules["google.oauth2"] = MagicMock()
sys.modules["google.oauth2.service_account"] = MagicMock()

# Mock vertexai before importing our module (needed for lazy imports)
sys.modules["vertexai"] = MagicMock()
sys.modules["vertexai.generative_models"] = MagicMock()
sys.modules["google.protobuf"] = MagicMock()
sys.modules["google.protobuf.json_format"] = MagicMock()

# Now import our module (which will use the mocked dependencies)
try:
    from src.services.google_vertex_client import (
        _build_vertex_content,
        _ensure_protobuf_imports,
        _ensure_vertex_imports,
        _get_model_location,
        _process_google_vertex_rest_response,
        make_google_vertex_request_openai,
        make_google_vertex_request_openai_stream,
        transform_google_vertex_model_id,
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
        model_id = (
            "projects/my-project/locations/us-central1/publishers/google/models/gemini-2.0-flash"
        )
        result = transform_google_vertex_model_id(model_id)
        assert result == "gemini-2.0-flash"

    def test_transform_google_prefix(self):
        """Test that google/ prefix is stripped from model IDs"""
        # This is the case that caused the 404 error - model IDs coming from
        # the routing layer with the provider prefix need to be stripped
        test_cases = [
            ("google/gemini-2.0-flash", "gemini-2.0-flash"),
            ("google/gemini-3-pro-preview", "gemini-3-pro-preview"),
            ("google/gemini-1.5-pro", "gemini-1.5-pro"),
            ("google/gemini-2.5-flash-lite", "gemini-2.5-flash-lite"),
        ]
        for model_id, expected in test_cases:
            result = transform_google_vertex_model_id(model_id)
            assert result == expected, f"Expected {expected} for {model_id}, got {result}"

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
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}},
                ],
            }
        ]
        result = _build_vertex_content(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["parts"]) >= 2

    def test_build_base64_image_content_strips_data_url_prefix(self):
        """Test that base64 image data URLs are properly parsed to extract raw base64 data.

        This is a critical fix for the 400 Bad Request error from Vertex AI.
        The Gemini API expects only the raw base64-encoded data, NOT the data URI prefix.

        Incorrect: data:image/png;base64,iVBORw0KGgo...
        Correct:   iVBORw0KGgo...

        See: https://github.com/google-gemini/generative-ai-js/issues/307
        """
        # Sample base64 data (just a small test string, not an actual image)
        raw_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        data_url = f"data:image/png;base64,{raw_base64}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = _build_vertex_content(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert len(result[0]["parts"]) == 2

        # Check the image part has the correct format
        image_part = result[0]["parts"][1]
        assert "inline_data" in image_part
        assert image_part["inline_data"]["mime_type"] == "image/png"
        # Critical assertion: the data should be raw base64, not the full data URL
        assert image_part["inline_data"]["data"] == raw_base64
        assert not image_part["inline_data"]["data"].startswith("data:")

    def test_build_base64_image_content_jpeg(self):
        """Test that JPEG base64 images are correctly parsed with proper MIME type."""
        raw_base64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////"
        data_url = f"data:image/jpeg;base64,{raw_base64}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = _build_vertex_content(messages)

        image_part = result[0]["parts"][1]
        assert image_part["inline_data"]["mime_type"] == "image/jpeg"
        assert image_part["inline_data"]["data"] == raw_base64

    def test_build_base64_image_content_webp(self):
        """Test that WebP base64 images are correctly parsed with proper MIME type."""
        raw_base64 = "UklGRlYAAABXRUJQVlA4IEoAAADwAQCdASoB"
        data_url = f"data:image/webp;base64,{raw_base64}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = _build_vertex_content(messages)

        image_part = result[0]["parts"][1]
        assert image_part["inline_data"]["mime_type"] == "image/webp"
        assert image_part["inline_data"]["data"] == raw_base64

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
                    "content": {"parts": [{"text": "This is a response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
            },
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
                    "content": {"parts": [{"text": "Part 1 "}, {"text": "Part 2"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
            },
        }

        result = _process_google_vertex_rest_response(response_data, "gemini-1.5-pro")

        assert result["choices"][0]["message"]["content"] == "Part 1 Part 2"

    def test_process_gemini_flash_lite_response(self):
        """Test processing response from gemini-2.5-flash-lite-preview-09-2025"""
        response_data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Flash Lite response"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 3,
            },
        }

        result = _process_google_vertex_rest_response(
            response_data, "gemini-2.5-flash-lite-preview-09-2025"
        )

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

        messages = [{"role": "user", "content": "Hello"}]

        result = make_google_vertex_request_openai(
            messages=messages, model="gemini-2.0-flash", max_tokens=100, temperature=0.7, top_p=0.9
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

        messages = [{"role": "user", "content": "Hello"}]

        # Get the generator
        gen = make_google_vertex_request_openai_stream(
            messages=messages, model="gemini-1.5-flash", max_tokens=100
        )

        # Collect all chunks
        chunks = list(gen)

        # Streaming now yields dict objects, not SSE strings
        assert len(chunks) >= 2  # At least a content chunk and a finish chunk

        # Check that we have content in the chunks
        content_found = False
        finish_found = False
        for chunk in chunks:
            if isinstance(chunk, dict):
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        content_found = True
                    if choices[0].get("finish_reason"):
                        finish_found = True
        assert content_found, "Should find content in streaming chunks"
        assert finish_found, "Should find finish_reason in streaming chunks"

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

        messages = [{"role": "user", "content": "Test"}]

        result = make_google_vertex_request_openai(
            messages=messages, model="gemini-2.5-flash-lite-preview-09-2025"
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

    @patch.dict("os.environ", {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"type":"service_account"}'})
    def test_gemini_model_detection(self):
        """Test that gemini models are properly detected when credentials are available"""
        from src.services.model_transformations import detect_provider_from_model_id

        # Note: Gemini 1.5 models were retired by Google in April-September 2025
        # They now default to OpenRouter as Vertex AI returns 404 for them
        models = [
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "google/gemini-3-flash",
        ]

        for model in models:
            provider = detect_provider_from_model_id(model)
            assert (
                provider == "google-vertex"
            ), f"Model {model} should detect as google-vertex, got {provider}"

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
        # Note: Gemini 1.5 models were retired by Google in April-September 2025
        # and are no longer included in the static config
        expected_models = [
            "gemini-3-pro",
            "gemini-3-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
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

        # Check that slug and canonical_slug have google-vertex/ prefix
        # This ensures Google Vertex models don't get deduplicated with other gateways
        assert model["slug"].startswith(
            "google-vertex/"
        ), f"Expected slug to start with 'google-vertex/', got: {model['slug']}"
        assert model["canonical_slug"].startswith(
            "google-vertex/"
        ), f"Expected canonical_slug to start with 'google-vertex/', got: {model['canonical_slug']}"
        assert model["slug"] == f"google-vertex/{model['id']}"
        assert model["canonical_slug"] == f"google-vertex/{model['id']}"

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

    def test_fetch_models_includes_provider_model_id(self):
        """Test that returned models include provider_model_id for database sync.

        This is critical for models where the canonical ID differs from the
        actual provider model ID (e.g., gemini-3-flash vs gemini-3-flash-preview).
        The provider_model_id is used when saving chat completion requests to
        ensure proper model lookup in the database.
        """
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Check that all models have provider_model_id
        for model in models:
            assert "provider_model_id" in model, f"Model {model['id']} missing provider_model_id"
            assert (
                model["provider_model_id"] is not None
            ), f"Model {model['id']} has None provider_model_id"

        # Specifically check Gemini 3 models which have different provider model IDs
        gemini_3_flash = next((m for m in models if m["id"] == "gemini-3-flash"), None)
        assert gemini_3_flash is not None, "gemini-3-flash model not found"
        assert (
            gemini_3_flash["provider_model_id"] == "gemini-3-flash-preview"
        ), f"Expected gemini-3-flash to have provider_model_id 'gemini-3-flash-preview', got '{gemini_3_flash['provider_model_id']}'"

        gemini_3_pro = next((m for m in models if m["id"] == "gemini-3-pro"), None)
        assert gemini_3_pro is not None, "gemini-3-pro model not found"
        assert (
            gemini_3_pro["provider_model_id"] == "gemini-3-pro-preview"
        ), f"Expected gemini-3-pro to have provider_model_id 'gemini-3-pro-preview', got '{gemini_3_pro['provider_model_id']}'"

    def test_fetch_models_raw_google_vertex_includes_provider_model_id(self):
        """Test that raw_google_vertex metadata includes provider_model_id."""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Check Gemini 3 flash specifically
        gemini_3_flash = next((m for m in models if m["id"] == "gemini-3-flash"), None)
        assert gemini_3_flash is not None, "gemini-3-flash model not found"
        assert "raw_google_vertex" in gemini_3_flash, "Missing raw_google_vertex metadata"
        assert (
            gemini_3_flash["raw_google_vertex"]["provider_model_id"] == "gemini-3-flash-preview"
        ), "raw_google_vertex should include the provider_model_id for database sync"


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
            assert (
                location == "global"
            ), f"Model {model_name} should use 'global' endpoint, got '{location}'"

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
            assert (
                location == "us-central1"
            ), f"Model {model_name} should use 'us-central1' endpoint, got '{location}'"

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
            assert (
                location == "europe-west4"
            ), f"Model {model_name} should use 'europe-west4' endpoint, got '{location}'"

    def test_gemma_models_use_regional_endpoint(self, monkeypatch):
        """Test that Gemma models use the configured regional endpoint"""
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-west1")

        gemma_models = [
            "gemma-2-9b-it",
            "gemma-2-27b-it",
        ]

        for model_name in gemma_models:
            location = _get_model_location(model_name)
            assert (
                location == "us-west1"
            ), f"Model {model_name} should use 'us-west1' endpoint, got '{location}'"

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
            messages=[{"role": "user", "content": "test"}], model="gemini-3-flash-preview"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Gemini 3 response"

        # Verify the URL used the global endpoint
        # Global endpoint uses https://aiplatform.googleapis.com (no region prefix)
        assert client_factory.calls == 1
        request_url = client_factory.payloads[0]["url"]
        assert (
            "https://aiplatform.googleapis.com/v1/" in request_url
        ), f"URL should use global endpoint (no region prefix): {request_url}"
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
            messages=[{"role": "user", "content": "test"}], model="gemini-2.5-flash"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Gemini 2.5 response"

        # Verify the URL used the regional endpoint
        assert client_factory.calls == 1
        request_url = client_factory.payloads[0]["url"]
        assert (
            "us-central1-aiplatform.googleapis.com" in request_url
        ), f"URL should use regional endpoint: {request_url}"
        assert (
            "locations/us-central1/" in request_url
        ), f"URL should use regional location: {request_url}"

    @pytest.mark.usefixtures("force_rest_transport")
    def test_rest_request_strips_google_prefix_from_model_id(self, monkeypatch):
        """Ensure google/ prefix is stripped from model ID to prevent 404 errors.

        This tests the fix for the issue where model IDs like 'google/gemini-3-pro-preview'
        were not having their prefix stripped, resulting in malformed URLs like:
        publishers/google/models/google/gemini-3-pro-preview
        """
        monkeypatch.setattr(Config, "GOOGLE_VERTEX_LOCATION", "us-central1")
        monkeypatch.setattr(Config, "GOOGLE_PROJECT_ID", "test-project")
        monkeypatch.setattr(
            "src.services.google_vertex_client._get_google_vertex_access_token",
            lambda force_refresh=False: "token-123",
        )

        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Response from prefixed model"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10},
        }

        client_factory = DummyHttpxClientFactory([DummyHttpxResponse(200, payload)])
        monkeypatch.setattr("src.services.google_vertex_client.httpx.Client", client_factory)

        # Use model ID with google/ prefix (as it comes from the routing layer)
        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}], model="google/gemini-3-pro-preview"
        )

        # Verify the request was successful
        assert result["choices"][0]["message"]["content"] == "Response from prefixed model"

        # Verify the URL does NOT contain a duplicated google/ prefix
        assert client_factory.calls == 1
        request_url = client_factory.payloads[0]["url"]

        # The URL should have publishers/google/models/gemini-3-pro-preview
        # NOT publishers/google/models/google/gemini-3-pro-preview
        assert (
            "models/gemini-3-pro-preview" in request_url
        ), f"URL should have stripped model name: {request_url}"
        assert (
            "models/google/gemini-3-pro-preview" not in request_url
        ), f"URL should NOT have duplicated google/ prefix: {request_url}"

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_sdk_request_uses_global_for_gemini_3(
        self, mock_ensure_imports, mock_init_vertex, monkeypatch
    ):
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
            messages=[{"role": "user", "content": "test"}], model="gemini-3-flash-preview"
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
class TestNoCandidatesErrorHandling:
    """Tests for handling 'no candidates' responses from Vertex AI"""

    def test_no_candidates_without_prompt_feedback(self):
        """Test that 'no candidates' error includes model version when no promptFeedback is present"""
        # This response mimics what Gemini 3 preview returns when it fails silently
        response_data = {
            "usageMetadata": {
                "promptTokenCount": 12456,
                "totalTokenCount": 12456,
                "cachedContentTokenCount": 12251,
                "trafficType": "ON_DEMAND",
            },
            "modelVersion": "gemini-3-flash-preview",
            "createTime": "2025-12-31T08:01:02.219993Z",
            "responseId": "test-response-id",
        }

        with pytest.raises(ValueError) as exc_info:
            _process_google_vertex_rest_response(response_data, "gemini-3-flash-preview")

        error_msg = str(exc_info.value).lower()
        assert "no candidates" in error_msg
        assert "gemini-3-flash-preview" in error_msg
        assert "transient" in error_msg or "preview" in error_msg

    def test_no_candidates_with_block_reason(self):
        """Test that 'no candidates' error includes block reason when promptFeedback is present"""
        response_data = {
            "promptFeedback": {
                "blockReason": "SAFETY",
                "safetyRatings": [
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "probability": "HIGH",
                        "blocked": True,
                    }
                ],
            },
            "usageMetadata": {"promptTokenCount": 100},
        }

        with pytest.raises(ValueError) as exc_info:
            _process_google_vertex_rest_response(response_data, "gemini-2.5-flash")

        error_msg = str(exc_info.value)
        assert "no candidates" in error_msg.lower()
        assert "SAFETY" in error_msg or "Block reason" in error_msg

    def test_no_candidates_with_safety_ratings(self):
        """Test that safety rating details are included in error when blocked"""
        response_data = {
            "promptFeedback": {
                "safetyRatings": [
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "probability": "HIGH",
                        "blocked": True,
                    },
                    {"category": "HARM_CATEGORY_VIOLENCE", "probability": "LOW", "blocked": False},
                ]
            },
            "usageMetadata": {"promptTokenCount": 50},
        }

        with pytest.raises(ValueError) as exc_info:
            _process_google_vertex_rest_response(response_data, "gemini-2.5-flash")

        error_msg = str(exc_info.value)
        assert "HARM_CATEGORY_HATE_SPEECH" in error_msg
        # LOW probability items should not be included
        assert "HARM_CATEGORY_VIOLENCE" not in error_msg or "LOW" not in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
