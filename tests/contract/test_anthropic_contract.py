"""
Contract tests for Anthropic API

Verifies that Anthropic API responses match our expected schema.
Helps catch breaking changes in their API early.

IMPORTANT: These tests make REAL API calls. They are:
- Marked as @pytest.mark.contract
- Skipped by default in CI (use -m contract to run)
- Require ANTHROPIC_API_KEY environment variable
"""

import os
import pytest
import httpx
from typing import Dict, List, Any


# Skip if no API key available
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
pytestmark = pytest.mark.skipif(
    not ANTHROPIC_API_KEY,
    reason="ANTHROPIC_API_KEY not set"
)


@pytest.fixture
def anthropic_headers():
    """Headers for Anthropic API requests"""
    return {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


# ============================================================================
# Messages API Contract Tests
# ============================================================================

class TestAnthropicMessagesContract:
    """Test Anthropic /messages API contract"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_messages_basic_request(self, anthropic_headers):
        """Verify basic messages request/response structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "Say 'test' and nothing else"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            assert response.status_code == 200, f"API returned {response.status_code}"

            data = response.json()

            # Required top-level fields
            assert "id" in data
            assert "type" in data
            assert data["type"] == "message"
            assert "role" in data
            assert data["role"] == "assistant"
            assert "content" in data
            assert "model" in data
            assert "stop_reason" in data
            assert "usage" in data

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_messages_content_structure(self, anthropic_headers):
        """Verify content array structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "Hello"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            data = response.json()

            # Content should be an array
            assert isinstance(data["content"], list)
            assert len(data["content"]) > 0

            # First content block
            content_block = data["content"][0]
            assert "type" in content_block
            assert content_block["type"] == "text"
            assert "text" in content_block
            assert isinstance(content_block["text"], str)

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_messages_usage_structure(self, anthropic_headers):
        """Verify usage object structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 50,
                "messages": [
                    {"role": "user", "content": "What is 2+2?"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            data = response.json()
            usage = data["usage"]

            # Required usage fields
            assert "input_tokens" in usage
            assert "output_tokens" in usage

            # Should be integers
            assert isinstance(usage["input_tokens"], int)
            assert isinstance(usage["output_tokens"], int)

            # Should be positive
            assert usage["input_tokens"] > 0
            assert usage["output_tokens"] > 0

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_messages_streaming(self, anthropic_headers):
        """Verify streaming response structure"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 50,
                "messages": [
                    {"role": "user", "content": "Count to 3"}
                ],
                "stream": True
            }

            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            ) as response:
                assert response.status_code == 200

                events_received = {}
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        import json
                        event_data = json.loads(line[6:])

                        event_type = event_data.get("type")
                        if event_type:
                            events_received[event_type] = events_received.get(event_type, 0) + 1

                        # Verify event structure based on type
                        if event_type == "message_start":
                            assert "message" in event_data
                            assert event_data["message"]["role"] == "assistant"

                        elif event_type == "content_block_start":
                            assert "index" in event_data
                            assert "content_block" in event_data

                        elif event_type == "content_block_delta":
                            assert "index" in event_data
                            assert "delta" in event_data

                        elif event_type == "message_delta":
                            assert "delta" in event_data

                # Should have received multiple event types
                assert "message_start" in events_received
                assert "content_block_start" in events_received
                assert "message_delta" in events_received


# ============================================================================
# System Messages Contract Tests
# ============================================================================

class TestAnthropicSystemMessagesContract:
    """Test Anthropic system message handling"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_system_parameter(self, anthropic_headers):
        """Verify system parameter is accepted"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 20,
                "system": "You are a helpful assistant.",
                "messages": [
                    {"role": "user", "content": "Hello"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            assert response.status_code == 200


# ============================================================================
# Error Handling Contract Tests
# ============================================================================

class TestAnthropicErrorContract:
    """Test Anthropic error response structure"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_invalid_api_key_error(self):
        """Verify error response for invalid API key"""
        async with httpx.AsyncClient() as client:
            headers = {
                "x-api-key": "invalid-key",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }

            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=30.0
            )

            # Should return 401
            assert response.status_code == 401

            data = response.json()
            assert "error" in data
            assert "type" in data["error"]
            assert "message" in data["error"]

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_invalid_model_error(self, anthropic_headers):
        """Verify error response for invalid model"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "nonexistent-model",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            # Should return 400 or 404
            assert response.status_code >= 400

            data = response.json()
            assert "error" in data

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_missing_required_field_error(self, anthropic_headers):
        """Verify error response when required field is missing"""
        async with httpx.AsyncClient() as client:
            # Missing max_tokens
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            # Should return 400
            assert response.status_code == 400

            data = response.json()
            assert "error" in data


# ============================================================================
# Tool Use Contract Tests
# ============================================================================

class TestAnthropicToolsContract:
    """Test Anthropic tool/function calling"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_tools_parameter_accepted(self, anthropic_headers):
        """Verify tools parameter is accepted"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Get weather for a location",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "location": {
                                    "type": "string",
                                    "description": "City name"
                                }
                            },
                            "required": ["location"]
                        }
                    }
                ],
                "messages": [
                    {"role": "user", "content": "What's the weather in SF?"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            # Should accept request with tools
            assert response.status_code == 200

            data = response.json()

            # May or may not use tool, but response should be valid
            assert "content" in data
            assert isinstance(data["content"], list)


# ============================================================================
# Model Variants Contract Tests
# ============================================================================

class TestAnthropicModelVariantsContract:
    """Test different Claude model variants"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_claude_3_5_sonnet(self, anthropic_headers):
        """Verify Claude 3.5 Sonnet works"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "Hi"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            assert response.status_code == 200
            data = response.json()
            assert "claude-3" in data["model"].lower() or "sonnet" in data["model"].lower()

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_claude_3_opus(self, anthropic_headers):
        """Verify Claude 3 Opus works"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-opus-20240229",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "Hi"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            assert response.status_code == 200
            data = response.json()
            assert "claude-3" in data["model"].lower()


# ============================================================================
# Rate Limit Headers Contract Tests
# ============================================================================

class TestAnthropicRateLimitsContract:
    """Test Anthropic rate limit headers"""

    @pytest.mark.contract
    @pytest.mark.asyncio
    async def test_rate_limit_headers_present(self, anthropic_headers):
        """Check for rate limit headers in response"""
        async with httpx.AsyncClient() as client:
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 10,
                "messages": [
                    {"role": "user", "content": "test"}
                ]
            }

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=anthropic_headers,
                json=payload,
                timeout=30.0
            )

            # Document rate limit headers if present
            headers = response.headers
            rate_limit_headers = {
                k: v for k, v in headers.items()
                if "rate" in k.lower() or "limit" in k.lower()
            }

            if rate_limit_headers:
                print(f"\nRate limit headers: {rate_limit_headers}")
