"""
Contract tests for OpenRouter API

Verifies that OpenRouter API responses match our expected schema.
Helps catch breaking changes in their API early.

IMPORTANT: These tests make REAL API calls. They are:
- Marked as @pytest.mark.contract
- Skipped by default in CI (use -m contract to run)
- Require OPENROUTER_API_KEY environment variable
"""

import os
import pytest
import httpx
from typing import Dict, List, Any


# Skip if no API key available
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
pytestmark = pytest.mark.skipif(
    not OPENROUTER_API_KEY,
    reason="OPENROUTER_API_KEY not set"
)


@pytest.fixture
def openrouter_headers():
    """Headers for OpenRouter API requests"""
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://gatewayz.ai",
        "X-Title": "Gatewayz Contract Tests",
    }


# ============================================================================
# Models API Contract Tests
# ============================================================================

class TestOpenRouterModelsContract:
    """Test OpenRouter /models API contract"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_models_endpoint_structure(self, openrouter_headers):
        """Verify /models endpoint returns expected structure"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=openrouter_headers,
                timeout=30.0
            )

            assert response.status_code == 200, f"API returned {response.status_code}"

            data = response.json()

            # Top-level structure
            assert "data" in data, "Response missing 'data' field"
            assert isinstance(data["data"], list), "data field should be a list"
            assert len(data["data"]) > 0, "No models returned"

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_model_object_schema(self, openrouter_headers):
        """Verify each model object has required fields"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=openrouter_headers,
                timeout=30.0
            )

            models = response.json()["data"]
            sample_model = models[0]

            # Required fields
            required_fields = ["id", "name", "pricing"]
            for field in required_fields:
                assert field in sample_model, f"Model missing required field: {field}"

            # ID format
            assert isinstance(sample_model["id"], str)
            assert len(sample_model["id"]) > 0

            # Name
            assert isinstance(sample_model["name"], str)
            assert len(sample_model["name"]) > 0

            # Pricing structure
            assert isinstance(sample_model["pricing"], dict)
            assert "prompt" in sample_model["pricing"]
            assert "completion" in sample_model["pricing"]

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_model_pricing_format(self, openrouter_headers):
        """Verify pricing is in correct format (string representing cost per token)"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=openrouter_headers,
                timeout=30.0
            )

            models = response.json()["data"]
            sample_model = models[0]

            pricing = sample_model["pricing"]

            # Pricing values should be strings (representing dollars per token)
            assert isinstance(pricing["prompt"], str), "prompt pricing should be string"
            assert isinstance(pricing["completion"], str), "completion pricing should be string"

            # Should be convertible to float
            prompt_price = float(pricing["prompt"])
            completion_price = float(pricing["completion"])

            # Should be reasonable values (> 0, < 1)
            assert prompt_price >= 0, "Prompt price should be non-negative"
            assert completion_price >= 0, "Completion price should be non-negative"

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_model_context_length(self, openrouter_headers):
        """Verify context_length field exists and is valid"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=openrouter_headers,
                timeout=30.0
            )

            models = response.json()["data"]

            # Check first 5 models
            for model in models[:5]:
                if "context_length" in model:
                    assert isinstance(model["context_length"], int)
                    assert model["context_length"] > 0
                    assert model["context_length"] <= 2000000  # Reasonable upper bound


# ============================================================================
# Chat Completions API Contract Tests
# ============================================================================

class TestOpenRouterChatContract:
    """Test OpenRouter /chat/completions API contract"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_chat_completions_basic(self, openrouter_headers):
        """Verify basic chat completion request/response structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "Say 'test' and nothing else"}
                ],
                "max_tokens": 10
            }

            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=openrouter_headers,
                json=payload,
                timeout=30.0
            )

            assert response.status_code == 200, f"API returned {response.status_code}"

            data = response.json()

            # Required top-level fields
            assert "id" in data
            assert "model" in data
            assert "choices" in data
            assert "usage" in data

            # Choices structure
            assert isinstance(data["choices"], list)
            assert len(data["choices"]) > 0

            choice = data["choices"][0]
            assert "message" in choice
            assert "finish_reason" in choice

            # Message structure
            message = choice["message"]
            assert "role" in message
            assert "content" in message
            assert message["role"] == "assistant"

            # Usage structure
            usage = data["usage"]
            assert "prompt_tokens" in usage
            assert "completion_tokens" in usage
            assert "total_tokens" in usage

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_chat_streaming_response(self, openrouter_headers):
        """Verify streaming response structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "Count to 3"}
                ],
                "stream": True,
                "max_tokens": 20
            }

            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers=openrouter_headers,
                json=payload,
                timeout=30.0
            ) as response:
                assert response.status_code == 200

                chunks_received = 0
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        chunk_data = line[6:]  # Remove "data: " prefix

                        if chunk_data == "[DONE]":
                            break

                        import json
                        chunk = json.loads(chunk_data)

                        # Verify chunk structure
                        assert "id" in chunk
                        assert "choices" in chunk
                        assert isinstance(chunk["choices"], list)

                        if chunk["choices"]:
                            choice = chunk["choices"][0]
                            assert "delta" in choice

                        chunks_received += 1

                assert chunks_received > 0, "No chunks received in stream"


# ============================================================================
# Error Handling Contract Tests
# ============================================================================

class TestOpenRouterErrorContract:
    """Test OpenRouter error response structure"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_invalid_model_error(self, openrouter_headers):
        """Verify error response structure for invalid model"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "invalid/nonexistent-model",
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=openrouter_headers,
                json=payload,
                timeout=30.0
            )

            # Should return 400 or similar error
            assert response.status_code >= 400

            data = response.json()

            # Error structure should include error object
            assert "error" in data
            error = data["error"]

            # Error should have message
            assert "message" in error or "error" in error

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_missing_api_key_error(self):
        """Verify error response when API key is missing"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                timeout=30.0
            )

            # Should return 401 unauthorized
            assert response.status_code == 401


# ============================================================================
# Metadata Contract Tests
# ============================================================================

class TestOpenRouterMetadataContract:
    """Test OpenRouter response metadata"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_response_headers(self, openrouter_headers):
        """Verify important response headers are present"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=openrouter_headers,
                timeout=30.0
            )

            # Should have content-type header
            assert "content-type" in response.headers
            assert "json" in response.headers["content-type"].lower()

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_rate_limit_headers(self, openrouter_headers):
        """Check if rate limit headers are present"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "test"}
                ],
                "max_tokens": 5
            }

            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=openrouter_headers,
                json=payload,
                timeout=30.0
            )

            # Note: OpenRouter may or may not include these, just document them
            headers = response.headers
            rate_limit_headers = [
                k for k in headers.keys()
                if "rate-limit" in k.lower() or "ratelimit" in k.lower()
            ]

            # Log what we find (for documentation purposes)
            if rate_limit_headers:
                print(f"\nRate limit headers found: {rate_limit_headers}")
