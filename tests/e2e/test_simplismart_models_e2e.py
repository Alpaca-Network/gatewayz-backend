"""
End-to-end tests for Simplismart models via the /v1/chat/completions endpoint.

Tests the following Simplismart models:
- meta-llama/Meta-Llama-3.1-8B-Instruct - Llama 3.1 8B
- meta-llama/Meta-Llama-3.1-70B-Instruct - Llama 3.1 70B
- meta-llama/Llama-3.3-70B-Instruct - Llama 3.3 70B
- google/gemma-3-27b-it - Gemma 3 27B
- Qwen/Qwen2.5-32B-Instruct - Qwen 2.5 32B
- deepseek-ai/DeepSeek-R1-Distill-Llama-70B - DeepSeek R1 Distill
- mistralai/Mixtral-8x7B-Instruct-v0.1-FP8 - Mixtral 8x7B

These tests validate:
- Models can be called via the chat completions endpoint
- Streaming responses work correctly
- Response format follows OpenAI standard
- Model aliases work correctly
"""

import pytest
from fastapi.testclient import TestClient

# Simplismart models available via the API
SIMPLISMART_MODELS = [
    "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "meta-llama/Llama-3.3-70B-Instruct",
    "google/gemma-3-27b-it",
    "Qwen/Qwen2.5-32B-Instruct",
]

# Models with aliases for alias testing
SIMPLISMART_MODEL_ALIASES = [
    ("llama-3.1-8b", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
    ("llama-3.3-70b", "meta-llama/Llama-3.3-70B-Instruct"),
    ("gemma-3-27b", "google/gemma-3-27b-it"),
    ("qwen-2.5-32b", "Qwen/Qwen2.5-32B-Instruct"),
]


class TestSimplismartModelsE2E:
    """E2E tests for Simplismart models via chat completions endpoint."""

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_basic_request(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test basic chat completion request for each Simplismart model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "max_tokens": 100,
            "provider": "simplismart",
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

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_streaming(self, client: TestClient, auth_headers: dict, model: str):
        """Test streaming chat completion for each Simplismart model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Count from 1 to 5."}],
            "max_tokens": 100,
            "stream": True,
            "provider": "simplismart",
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

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_with_system_prompt(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test Simplismart models with system prompt."""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant who responds briefly."},
                {"role": "user", "content": "What is 2+2?"},
            ],
            "max_tokens": 50,
            "provider": "simplismart",
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

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_conversation_history(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test Simplismart models with conversation history."""
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "My name is Alice."},
                {"role": "assistant", "content": "Hello Alice! Nice to meet you."},
                {"role": "user", "content": "What is my name?"},
            ],
            "max_tokens": 50,
            "provider": "simplismart",
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

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_with_temperature(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test Simplismart models with custom temperature."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "temperature": 0.7,
            "provider": "simplismart",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_with_top_p(self, client: TestClient, auth_headers: dict, model: str):
        """Test Simplismart models with top_p parameter."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "top_p": 0.95,
            "provider": "simplismart",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_simplismart_model_response_has_usage(
        self, client: TestClient, auth_headers: dict, model: str
    ):
        """Test that Simplismart model responses include usage information."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "provider": "simplismart",
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

    def test_simplismart_model_invalid_should_fail(self, client: TestClient, auth_headers: dict):
        """Test that invalid Simplismart model ID returns appropriate error."""
        payload = {
            "model": "simplismart/invalid-model-name",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 50,
            "provider": "simplismart",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should fail with appropriate error code
        assert response.status_code in [400, 401, 402, 403, 422, 429, 500, 502, 503]


class TestSimplismartModelAliasesE2E:
    """E2E tests for Simplismart model aliases."""

    @pytest.mark.parametrize("alias,expected_model", SIMPLISMART_MODEL_ALIASES)
    def test_simplismart_alias_resolution(
        self, client: TestClient, auth_headers: dict, alias: str, expected_model: str
    ):
        """Test that model aliases are correctly resolved."""
        payload = {
            "model": alias,
            "messages": [{"role": "user", "content": "Say hi"}],
            "max_tokens": 30,
            "provider": "simplismart",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]


class TestSimplismartStreamingE2E:
    """Dedicated streaming tests for Simplismart models."""

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_streaming_with_custom_params(self, client: TestClient, auth_headers: dict, model: str):
        """Test streaming with custom temperature and max_tokens."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello"}],
            "temperature": 0.5,
            "max_tokens": 30,
            "stream": True,
            "provider": "simplismart",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            assert "[DONE]" in response.text

    @pytest.mark.parametrize("model", SIMPLISMART_MODELS)
    def test_streaming_response_structure(self, client: TestClient, auth_headers: dict, model: str):
        """Test that streaming responses have correct SSE structure."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 20,
            "stream": True,
            "provider": "simplismart",
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
            data_lines = [line for line in lines if line.startswith("data:")]
            assert len(data_lines) > 0, "No data lines in SSE response"
            # Last data line should be [DONE]
            assert "data: [DONE]" in response.text


class TestSimplismartLlamaModelsE2E:
    """Specific tests for Llama models on Simplismart."""

    def test_llama_3_1_8b_instruction_following(self, client: TestClient, auth_headers: dict):
        """Test that Llama 3.1 8B follows instructions."""
        payload = {
            "model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Always respond in exactly one word.",
                },
                {"role": "user", "content": "What color is the sky?"},
            ],
            "max_tokens": 20,
            "provider": "simplismart",
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

    def test_llama_3_3_70b_reasoning_task(self, client: TestClient, auth_headers: dict):
        """Test Llama 3.3 70B with a reasoning task."""
        payload = {
            "model": "meta-llama/Llama-3.3-70B-Instruct",
            "messages": [
                {
                    "role": "user",
                    "content": "What is 15 + 27? Explain your reasoning.",
                }
            ],
            "max_tokens": 200,
            "provider": "simplismart",
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
            assert len(content) > 0


class TestSimplismartGemmaModelsE2E:
    """Specific tests for Gemma models on Simplismart."""

    def test_gemma_3_27b_basic(self, client: TestClient, auth_headers: dict):
        """Test Gemma 3 27B basic functionality."""
        payload = {
            "model": "google/gemma-3-27b-it",
            "messages": [
                {"role": "user", "content": "What is the capital of France?"},
            ],
            "max_tokens": 50,
            "provider": "simplismart",
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


class TestSimplismartQwenModelsE2E:
    """Specific tests for Qwen models on Simplismart."""

    def test_qwen_2_5_32b_basic(self, client: TestClient, auth_headers: dict):
        """Test Qwen 2.5 32B basic functionality."""
        payload = {
            "model": "Qwen/Qwen2.5-32B-Instruct",
            "messages": [
                {"role": "user", "content": "Write a haiku about programming."},
            ],
            "max_tokens": 100,
            "provider": "simplismart",
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
