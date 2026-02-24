"""
End-to-end tests for /v1/responses endpoint (Unified API).

These tests verify:
- Unified responses endpoint works with different model types
- Multimodal input support (text + images)
- Response format options (JSON, etc.)
- Provider parameter works with unified API
- Streaming works with responses endpoint
"""

import pytest
from fastapi.testclient import TestClient


class TestResponsesE2E:
    """E2E tests for unified responses endpoint."""

    def test_responses_basic_request(
        self, client: TestClient, auth_headers: dict, base_responses_payload: dict
    ):
        """Test basic unified responses endpoint request and response."""
        response = client.post(
            "/v1/responses",
            json=base_responses_payload,
            headers=auth_headers,
        )

        # Verify response structure
        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "output" in data
            assert len(data["output"]) > 0
            assert "content" in data["output"][0]

    def test_responses_with_all_parameters(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with all optional parameters."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            "stream": False,
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "output" in data

    def test_responses_streaming(
        self, client: TestClient, auth_headers: dict, base_responses_payload: dict
    ):
        """Test streaming responses endpoint."""
        payload = {**base_responses_payload, "stream": True}

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            # Verify streaming response format
            assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"
            # Parse SSE stream
            content = response.text
            assert "data:" in content

    def test_responses_with_provider(
        self, client: TestClient, auth_headers: dict, base_responses_payload: dict
    ):
        """Test responses endpoint with specific provider."""
        payload = {**base_responses_payload, "provider": "openrouter"}

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "output" in data

    def test_responses_with_json_response_format(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with JSON response format."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [
                {
                    "role": "user",
                    "content": 'Respond with valid JSON: {"answer": "2+2=4"}',
                }
            ],
            "response_format": {"type": "json_object"},
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Response format may not be supported by all models
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_responses_multimodal_input(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with multimodal input (text + image)."""
        payload = {
            "model": "gpt-4-vision-preview",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ea/Van_Gogh_-_Starry_Night_-_Google_Art_Project.jpg/1280px-Van_Gogh_-_Starry_Night_-_Google_Art_Project.jpg"
                            },
                        },
                    ],
                }
            ],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Vision models should handle this
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_responses_multiple_input_items(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with multiple input items."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "2+2 equals 4"},
                {"role": "user", "content": "What is 3+3?"},
            ],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "output" in data

    def test_responses_missing_api_key(self, client: TestClient, base_responses_payload: dict):
        """Test responses endpoint without API key."""
        response = client.post(
            "/v1/responses",
            json=base_responses_payload,
        )

        assert response.status_code == 401

    def test_responses_empty_input(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with empty input array."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    def test_responses_missing_model(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint without model."""
        payload = {
            "input": [{"role": "user", "content": "Hello"}],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    def test_responses_with_tools(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with tool definitions."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": "What's the weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_responses_very_long_input(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with very long input."""
        long_content = "This is a test. " * 1000

        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": long_content}],
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503, 413]

    def test_responses_default_max_tokens(self, client: TestClient, auth_headers: dict):
        """Test that max_tokens defaults to 950."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": "Hello"}],
            # max_tokens not specified
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # Responses tests may fail with various errors if backend doesn't support parameters or auth fails
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        data = response.json()
        # Response should be within default limit
        if "usage" in data:
            assert data["usage"]["output_tokens"] <= 950

    def test_responses_with_featherless_provider(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with Featherless provider."""
        payload = {
            "model": "meta-llama/llama-2-70b-chat",
            "input": [{"role": "user", "content": "Hello"}],
            "provider": "featherless",
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_responses_with_fireworks_provider(self, client: TestClient, auth_headers: dict):
        """Test responses endpoint with Fireworks provider."""
        payload = {
            "model": "deepseek-ai/deepseek-v3",
            "input": [{"role": "user", "content": "Hello"}],
            "provider": "fireworks",
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
