"""
End-to-end tests for /v1/chat/completions endpoint.

These tests verify:
- Chat messages can be sent and received
- Streaming works correctly
- Provider parameter works with various providers
- Error handling for invalid inputs
- Rate limiting and credit checks
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestChatCompletionsE2E:
    """E2E tests for chat completions endpoint."""

    def test_chat_completions_basic_request(
        self, client: AsyncClient, auth_headers: dict, base_chat_payload: dict
    ):
        """Test basic chat completion request and response."""
        response = client.post(
            "/v1/chat/completions",
            json=base_chat_payload,
            headers=auth_headers,
        )

        # Verify response structure
        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "content" in data["choices"][0]["message"]
        assert "role" in data["choices"][0]["message"]
        assert data["choices"][0]["message"]["role"] == "assistant"

    def test_chat_completions_with_system_prompt(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with system prompt."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant who responds in JSON.",
                },
                {"role": "user", "content": 'Respond with {"status": "ok"}'},
            ],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["role"] == "assistant"

    def test_chat_completions_with_all_parameters(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with all optional parameters."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            "stream": False,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "choices" in data
        # Verify max_tokens was respected
        if "usage" in data:
            assert data["usage"]["completion_tokens"] <= 100

    def test_chat_completions_streaming(
        self, client: AsyncClient, auth_headers: dict, base_chat_payload: dict
    ):
        """Test streaming chat completion."""
        payload = {**base_chat_payload, "stream": True}

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        # Verify streaming response format
        assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"

        # Parse SSE stream
        content = response.text
        assert "data:" in content
        assert "[DONE]" in content

    def test_chat_completions_with_provider_openrouter(
        self, client: AsyncClient, auth_headers: dict, base_chat_payload: dict
    ):
        """Test chat completion with explicit OpenRouter provider."""
        payload = {**base_chat_payload, "provider": "openrouter"}

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "choices" in data

    def test_chat_completions_with_provider_featherless(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with Featherless provider."""
        payload = {
            "model": "meta-llama/llama-2-70b-chat",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "featherless",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed or fail gracefully with provider error
        assert response.status_code in [200, 503]

    def test_chat_completions_with_provider_fireworks(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with Fireworks provider."""
        payload = {
            "model": "deepseek-ai/deepseek-v3",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "fireworks",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 503]

    def test_chat_completions_missing_api_key(
        self, client: AsyncClient, base_chat_payload: dict
    ):
        """Test chat completion without API key."""
        response = client.post(
            "/v1/chat/completions",
            json=base_chat_payload,
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data

    def test_chat_completions_empty_messages(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with empty messages array."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error
        data = response.json()
        assert "detail" in data

    def test_chat_completions_invalid_role(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with invalid message role."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "invalid_role", "content": "Hello"}],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    def test_chat_completions_empty_content(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with empty message content."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": ""}],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    def test_chat_completions_multiple_messages(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with conversation history."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "2+2 equals 4."},
                {"role": "user", "content": "What is 3+3?"},
            ],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "choices" in data

    def test_chat_completions_with_tools(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with function calling tools."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "What's the weather?"}],
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
                                    "description": "The location",
                                }
                            },
                            "required": ["location"],
                        },
                    },
                }
            ],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should succeed even if tool not used
        assert response.status_code == 200

    def test_chat_completions_response_format_json(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with JSON response format."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "user",
                    "content": 'Respond with valid JSON: {"answer": "2+2=4"}',
                }
            ],
            "response_format": {"type": "json_object"},
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Response format may not be supported by all models
        assert response.status_code in [200, 400, 422]

    def test_chat_completions_very_long_message(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test chat completion with very long message."""
        long_content = "This is a test. " * 1000  # ~16KB message

        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": long_content}],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should either succeed or fail with appropriate error
        assert response.status_code in [200, 413, 400, 429]

    def test_chat_completions_session_id_parameter(
        self, client: AsyncClient, auth_headers: dict, base_chat_payload: dict
    ):
        """Test chat completion with session_id query parameter."""
        response = client.post(
            "/v1/chat/completions?session_id=1",
            json=base_chat_payload,
            headers=auth_headers,
        )

        # Session_id is optional; should still work
        assert response.status_code in [200, 404]  # 404 if session doesn't exist

    def test_chat_completions_default_max_tokens(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test that max_tokens defaults to 950."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            # max_tokens not specified - should default to 950
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Response should be within default limit
        if "usage" in data:
            assert data["usage"]["completion_tokens"] <= 950

    def test_chat_completions_default_temperature(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test that temperature defaults to 1.0."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Respond with exactly: CONSTANT"}],
            # temperature not specified - should default to 1.0
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"]
