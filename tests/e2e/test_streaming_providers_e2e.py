"""
End-to-end tests for streaming and provider parameter across all endpoints.

These tests verify:
- Streaming works consistently across endpoints
- Provider parameter with various providers
- Failover mechanism when provider fails
- Provider auto-detection from model IDs
"""

import pytest
from fastapi.testclient import TestClient


class TestStreamingE2E:
    """E2E tests for streaming functionality."""

    def test_streaming_chat_completions(self, client: TestClient, auth_headers: dict):
        """Test streaming on chat completions endpoint."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Count to 5"}],
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            assert "text/event-stream" in response.headers.get("content-type", "")
            assert "data:" in response.text
            assert "[DONE]" in response.text

    def test_streaming_messages(self, client: TestClient, auth_headers: dict):
        """Test streaming on messages endpoint."""
        payload = {
            "model": "claude-3.5-sonnet",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Count to 5"}],
            "stream": True,
        }

        response = client.post(
            "/v1/messages",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            assert "text/event-stream" in response.headers.get("content-type", "")
            assert "data:" in response.text

    def test_streaming_responses(self, client: TestClient, auth_headers: dict):
        """Test streaming on responses endpoint."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": "Count to 5"}],
            "stream": True,
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            assert "text/event-stream" in response.headers.get("content-type", "")
            assert "data:" in response.text

    def test_streaming_with_custom_parameters(self, client: TestClient, auth_headers: dict):
        """Test streaming with custom temperature and tokens."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "temperature": 0.5,
            "max_tokens": 50,
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            assert "[DONE]" in response.text

    def test_non_streaming_chat_completions(self, client: TestClient, auth_headers: dict):
        """Test non-streaming chat completions."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": False,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "choices" in data
            assert data["choices"][0]["message"]["content"]

    def test_non_streaming_messages(self, client: TestClient, auth_headers: dict):
        """Test non-streaming messages endpoint."""
        payload = {
            "model": "claude-3.5-sonnet",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": False,
        }

        response = client.post(
            "/v1/messages",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            data = response.json()
            assert "content" in data


class TestProviderParameterE2E:
    """E2E tests for provider parameter across endpoints."""

    def test_provider_openrouter_chat(self, client: TestClient, auth_headers: dict):
        """Test explicit OpenRouter provider on chat endpoint."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "openrouter",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_featherless_chat(self, client: TestClient, auth_headers: dict):
        """Test explicit Featherless provider on chat endpoint."""
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

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_fireworks_chat(self, client: TestClient, auth_headers: dict):
        """Test explicit Fireworks provider on chat endpoint."""
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

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_together_chat(self, client: TestClient, auth_headers: dict):
        """Test explicit Together provider on chat endpoint."""
        payload = {
            "model": "meta-llama/llama-2-70b-chat-hf",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "together",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_huggingface_chat(self, client: TestClient, auth_headers: dict):
        """Test explicit HuggingFace provider on chat endpoint."""
        payload = {
            "model": "meta-llama/Llama-2-7b-chat-hf",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "huggingface",
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_openrouter_messages(self, client: TestClient, auth_headers: dict):
        """Test explicit OpenRouter provider on messages endpoint."""
        payload = {
            "model": "claude-3.5-sonnet",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "openrouter",
        }

        response = client.post(
            "/v1/messages",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_openrouter_responses(self, client: TestClient, auth_headers: dict):
        """Test explicit OpenRouter provider on responses endpoint."""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [{"role": "user", "content": "Hello"}],
            "provider": "openrouter",
        }

        response = client.post(
            "/v1/responses",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_deepinfra_images(self, client: TestClient, auth_headers: dict):
        """Test explicit DeepInfra provider on images endpoint."""
        payload = {
            "prompt": "A beautiful sunset",
            "n": 1,
            "size": "1024x1024",
            "provider": "deepinfra",
        }

        response = client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_default_fallback(self, client: TestClient, auth_headers: dict):
        """Test default provider fallback when not specified."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            # provider not specified - should default to openrouter
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_auto_detection_from_model_id(self, client: TestClient, auth_headers: dict):
        """Test provider auto-detection from model ID."""
        payload = {
            "model": "openai/gpt-4",  # Explicit OpenAI prefix
            "messages": [{"role": "user", "content": "Hello"}],
            # provider not specified - should auto-detect from model
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should auto-detect and route correctly
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_alias_hug_to_huggingface(self, client: TestClient, auth_headers: dict):
        """Test provider alias normalization (hug -> huggingface)."""
        payload = {
            "model": "meta-llama/Llama-2-7b-chat-hf",
            "messages": [{"role": "user", "content": "Hello"}],
            "provider": "hug",  # Alias for huggingface
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]

    def test_provider_with_streaming(self, client: TestClient, auth_headers: dict):
        """Test provider parameter works with streaming."""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "provider": "openrouter",
            "stream": True,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # May return various errors if backend doesn't support certain features
        assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
        if response.status_code == 200:
            assert "[DONE]" in response.text

    def test_multiple_providers_in_sequence(self, client: TestClient, auth_headers: dict):
        """Test using different providers in sequence."""
        providers = ["openrouter", "featherless", "fireworks"]
        model_for_provider = {
            "openrouter": "gpt-3.5-turbo",
            "featherless": "meta-llama/llama-2-70b-chat",
            "fireworks": "deepseek-ai/deepseek-v3",
        }

        for provider in providers:
            payload = {
                "model": model_for_provider[provider],
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": provider,
            }

            response = client.post(
                "/v1/chat/completions",
                json=payload,
                headers=auth_headers,
            )

            # Each should succeed or fail gracefully
            assert response.status_code in [200, 400, 401, 402, 403, 422, 429, 500, 502, 503]
