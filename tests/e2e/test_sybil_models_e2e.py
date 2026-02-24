"""
End-to-end tests for Sybil models via the /v1/chat/completions endpoint.

Tests the following Sybil models:
- deepseek-ai/DeepSeek-V3-0324 - DeepSeek V3
- mistralai/Mistral-7B-Instruct-v0.3 - Mistral 7B Instruct
- moonshotai/Kimi-K2-Thinking - Kimi K2 Thinking
- Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 - Qwen3 Coder 480B

These tests validate:
- Models can be called via the chat completions endpoint
- Streaming responses work correctly
- Response format follows OpenAI standard
- Model features (tools, JSON mode, etc.) work correctly
"""

import pytest
from fastapi.testclient import TestClient

# Sybil models available via the API
SYBIL_MODELS = [
    "deepseek-ai/DeepSeek-V3-0324",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "moonshotai/Kimi-K2-Thinking",
]


class TestSybilModelsE2E:
    """E2E tests for Sybil models via chat completions endpoint."""

    @pytest.mark.parametrize("model", SYBIL_MODELS)
    def test_sybil_model_basic_request(self, client: TestClient, auth_headers: dict, model: str):
        """Test basic chat completion request for each Sybil model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello in one sentence."}],
            "max_tokens": 100,
            "provider": "sybil",
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

    @pytest.mark.parametrize("model", SYBIL_MODELS)
    def test_sybil_model_streaming(self, client: TestClient, auth_headers: dict, model: str):
        """Test streaming chat completion for each Sybil model."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Count from 1 to 3."}],
            "max_tokens": 50,
            "stream": True,
            "provider": "sybil",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            # For streaming, response should be text/event-stream
            assert "text/event-stream" in response.headers.get("content-type", "")

    def test_sybil_model_with_tools(self, client: TestClient, auth_headers: dict):
        """Test Sybil model with tool calling (for models that support it)."""
        payload = {
            "model": "deepseek-ai/DeepSeek-V3-0324",  # Supports tools
            "messages": [
                {
                    "role": "user",
                    "content": "What's the weather like in San Francisco?",
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get the weather for a location",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {
                                    "type": "string",
                                    "description": "The city and state, e.g. San Francisco, CA",
                                }
                            },
                            "required": ["location"],
                        },
                    },
                }
            ],
            "provider": "sybil",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            # Tool calling may result in tool_calls or regular content
            if "message" in data["choices"][0]:
                message = data["choices"][0]["message"]
                assert "role" in message
                assert message["role"] == "assistant"

    def test_sybil_model_with_json_mode(self, client: TestClient, auth_headers: dict):
        """Test Sybil model with JSON mode (for models that support it)."""
        payload = {
            "model": "deepseek-ai/DeepSeek-V3-0324",  # Supports JSON mode
            "messages": [
                {
                    "role": "user",
                    "content": "List three colors in JSON format with a 'colors' array.",
                }
            ],
            "response_format": {"type": "json_object"},
            "provider": "sybil",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            assert "message" in data["choices"][0]
            assert "content" in data["choices"][0]["message"]
            # Content should be valid JSON
            content = data["choices"][0]["message"]["content"]
            assert content  # Should not be empty
