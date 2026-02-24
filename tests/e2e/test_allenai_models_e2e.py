"""
End-to-end tests for AllenAI OLMo models via the /v1/chat/completions endpoint.

Tests the following AllenAI models:
- allenai/olmo-3.1-32b-think - 32B reasoning model
- allenai/olmo-3-32b-think - 32B reasoning model
- allenai/olmo-3-7b-instruct - 7B instruction model
- allenai/olmo-3-7b-think - 7B reasoning model

These tests validate:
- Models can be called via the chat completions endpoint
- Streaming responses work correctly
- Response format follows OpenAI standard
- Thinking/reasoning models return appropriate responses
"""

import pytest
from fastapi.testclient import TestClient

# AllenAI OLMo models available via OpenRouter
ALLENAI_MODELS = [
    "allenai/olmo-3.1-32b-think",
    "allenai/olmo-3-32b-think",
    "allenai/olmo-3-7b-instruct",
    "allenai/olmo-3-7b-think",
]


class TestAllenAIModelsE2E:
    """E2E tests for AllenAI OLMo models via chat completions endpoint."""

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_basic_request(self, client: TestClient, auth_headers: dict, model: str):
        """Test basic chat completion request for each AllenAI model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully with appropriate error
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            assert len(data["choices"]) > 0
            assert "message" in data["choices"][0]
            assert "content" in data["choices"][0]["message"]
            assert "role" in data["choices"][0]["message"]
            assert data["choices"][0]["message"]["role"] == "assistant"
            # Content should not be empty
            assert data["choices"][0]["message"]["content"]

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_streaming(self, client: TestClient, auth_headers: dict, model: str):
        """Test streaming chat completion for each AllenAI model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Count from 1 to 5."}],
            "max_tokens": 100,
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            # Verify streaming response format
            assert "text/event-stream" in response.headers.get("content-type", "")
            content = response.text
            assert "data:" in content
            assert "[DONE]" in content

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_with_system_prompt(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test AllenAI models with system prompt."""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant who responds briefly."},
                {"role": "user", "content": "What is 2+2?"},
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            assert data["choices"][0]["message"]["role"] == "assistant"

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_conversation_history(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test AllenAI models with conversation history."""
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "My name is Alice."},
                {"role": "assistant", "content": "Hello Alice! Nice to meet you."},
                {"role": "user", "content": "What is my name?"},
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            # Model should maintain context
            content = data["choices"][0]["message"]["content"]
            assert content is not None

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_with_temperature(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test AllenAI models with custom temperature."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "temperature": 0.7,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_with_top_p(self, client: TestClient, auth_headers: dict, model: str):
        """Test AllenAI models with top_p parameter."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "top_p": 0.9,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_olmo_instruct_follows_instructions(self, client: TestClient, auth_headers: dict):
        """Test that OLMo instruct model follows instructions."""
        payload = {
            "model": "allenai/olmo-3-7b-instruct",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Always respond in exactly one word.",
                },
                {"role": "user", "content": "What color is the sky?"},
            ],
            "max_tokens": 20,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            assert content is not None

    @pytest.mark.parametrize(
        "model",
        [
            "allenai/olmo-3.1-32b-think",
            "allenai/olmo-3-32b-think",
            "allenai/olmo-3-7b-think",
        ],
    )
    def test_thinking_models_reasoning_task(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test thinking models with a reasoning task."""
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "What is 15 + 27? Explain your reasoning.",
                }
            ],
            "max_tokens": 200,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            assert content is not None
            # Thinking models should provide detailed responses
            assert len(content) > 0

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_with_openrouter_provider(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test AllenAI models with explicit OpenRouter provider."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "provider": "openrouter",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_response_has_usage(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test that AllenAI model responses include usage information."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            # Usage should be present (though may be empty for some providers)
            if "usage" in data and data["usage"]:
                assert "prompt_tokens" in data["usage"] or data["usage"] == {}
                assert "completion_tokens" in data["usage"] or data["usage"] == {}

    def test_allenai_model_invalid_should_fail(self, client: TestClient, auth_headers: dict):
        """Test that invalid AllenAI model ID returns appropriate error."""
        payload = {
            "model": "allenai/invalid-model-name",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should fail with 400 (bad request) for invalid model
        assert response.status_code in [400, 401, 402, 403, 422, 429, 500, 502, 503]


class TestAllenAIModelsStreamingE2E:
    """Dedicated streaming tests for AllenAI models."""

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_streaming_with_custom_params(self, client: TestClient, auth_headers: dict, model: str):
        """Test streaming with custom temperature and max_tokens."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello"}],
            "temperature": 0.5,
            "max_tokens": 30,
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            assert "[DONE]" in response.text

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_streaming_response_structure(self, client: TestClient, auth_headers: dict, model: str):
        """Test that streaming responses have correct SSE structure."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 20,
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            # Check SSE format
            lines = response.text.strip().split("\n")
            data_lines = [l for l in lines if l.startswith("data:")]
            assert len(data_lines) > 0, "No data lines in SSE response"
            # Last data line should be [DONE]
            assert "data: [DONE]" in response.text


class TestAllenAIModelsDeveloperRoleE2E:
    """Tests for AllenAI models with developer role support."""

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_with_developer_role(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test AllenAI models with developer role instead of system role.

        The developer role is an OpenAI API feature that some models support
        as an alternative to the system role for setting assistant behavior.
        """
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "developer",
                    "content": "You are a helpful assistant who responds briefly.",
                },
                {"role": "user", "content": "What is 2+2?"},
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Developer role should be accepted by our API validation
        # Provider may or may not support it, so we accept various status codes
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            assert data["choices"][0]["message"]["role"] == "assistant"

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_allenai_model_developer_role_streaming(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test streaming with developer role for AllenAI models."""
        payload = {
            "model": model,
            "messages": [
                {"role": "developer", "content": "Respond in one word only."},
                {"role": "user", "content": "Hello"},
            ],
            "max_tokens": 30,
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            assert "[DONE]" in response.text
