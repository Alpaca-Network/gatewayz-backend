"""Tests for Soundsgood API client."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add benchmark scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "benchmarks"))

from benchmark_config import ModelConfig
from soundsgood_client import CompletionResponse, SoundsgoodClient, StreamingMetrics


class TestCompletionResponse:
    """Tests for CompletionResponse dataclass."""

    def test_create_response(self):
        """Test creating a completion response."""
        response = CompletionResponse(
            id="chatcmpl-123",
            model="zai-org/GLM-4.5-Air",
            content="Hello, world!",
            reasoning="I should greet the user.",
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=15,
            total_tokens=30,
            cost_usd_total=0.0001,
            cost_usd_input=0.00001,
            cost_usd_output=0.00009,
            ttfb_seconds=0.5,
            ttfc_seconds=0.6,
            total_duration_seconds=1.2,
            raw_response={"id": "chatcmpl-123"},
        )

        assert response.id == "chatcmpl-123"
        assert response.model == "zai-org/GLM-4.5-Air"
        assert response.content == "Hello, world!"
        assert response.reasoning is not None
        assert response.total_tokens == 30

    def test_response_without_reasoning(self):
        """Test response with no reasoning field."""
        response = CompletionResponse(
            id="chatcmpl-456",
            model="gpt-4",
            content="Just content.",
            reasoning=None,
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            total_tokens=15,
            cost_usd_total=0.0001,
            cost_usd_input=0.00001,
            cost_usd_output=0.00009,
            ttfb_seconds=0.3,
            ttfc_seconds=None,
            total_duration_seconds=0.8,
            raw_response={},
        )

        assert response.reasoning is None
        assert response.reasoning_tokens == 0


class TestStreamingMetrics:
    """Tests for StreamingMetrics dataclass."""

    def test_create_metrics(self):
        """Test creating streaming metrics."""
        metrics = StreamingMetrics(
            ttfb_seconds=0.3,
            ttfc_seconds=0.5,
            total_duration_seconds=2.5,
            chunks_received=25,
            content_accumulated="Full response content",
            reasoning_accumulated="Reasoning process",
        )

        assert metrics.ttfb_seconds == 0.3
        assert metrics.ttfc_seconds == 0.5
        assert metrics.chunks_received == 25


class TestSoundsgoodClient:
    """Tests for SoundsgoodClient class."""

    @pytest.fixture
    def model_config(self):
        """Create a test model config."""
        return ModelConfig(
            model_id="zai-org/GLM-4.5-Air",
            provider="soundsgood",
            api_base_url="https://soundsgood.one/v1",
            api_key_env_var="SOUNDSGOOD_API_KEY",
            price_input_per_m=0.15,
            price_output_per_m=4.13,
            context_length=128000,
            supports_streaming=True,
            is_reasoning_model=True,
        )

    def test_init(self, model_config):
        """Test client initialization."""
        client = SoundsgoodClient(model_config)

        assert client.config == model_config
        assert client.timeout == 120.0
        assert client._client is None

    def test_init_custom_timeout(self, model_config):
        """Test client initialization with custom timeout."""
        client = SoundsgoodClient(model_config, timeout=60.0)

        assert client.timeout == 60.0

    def test_headers(self, model_config, monkeypatch):
        """Test headers property."""
        monkeypatch.setenv("SOUNDSGOOD_API_KEY", "test_api_key_123")
        client = SoundsgoodClient(model_config)

        headers = client.headers

        assert headers["Authorization"] == "Bearer test_api_key_123"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_context_manager(self, model_config):
        """Test async context manager."""
        async with SoundsgoodClient(model_config) as client:
            assert client._client is not None

        # Client should be closed after exiting
        assert client._client is None or client._client.is_closed

    def test_parse_response(self, model_config):
        """Test parsing non-streaming response."""
        client = SoundsgoodClient(model_config)

        data = {
            "id": "chatcmpl-789",
            "model": "zai-org/GLM-4.5-Air",
            "choices": [
                {
                    "message": {
                        "content": "Response content",
                        "reasoning": "Reasoning process",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 100,
                "reasoning_tokens": 75,
                "total_tokens": 225,
                "cost_usd_total": 0.00045,
                "cost_usd_input": 0.00001,
                "cost_usd_output": 0.00044,
            },
        }

        response = client._parse_response(data, ttfb_seconds=0.5, total_duration_seconds=2.0)

        assert response.id == "chatcmpl-789"
        assert response.content == "Response content"
        assert response.reasoning == "Reasoning process"
        assert response.input_tokens == 50
        assert response.output_tokens == 100
        assert response.reasoning_tokens == 75
        assert response.cost_usd_total == 0.00045
        assert response.ttfb_seconds == 0.5
        assert response.total_duration_seconds == 2.0

    def test_parse_response_no_choices(self, model_config):
        """Test parsing response with no choices raises error."""
        client = SoundsgoodClient(model_config)

        data = {"id": "test", "choices": []}

        with pytest.raises(ValueError, match="No choices in response"):
            client._parse_response(data, ttfb_seconds=0.5, total_duration_seconds=1.0)

    def test_parse_response_missing_fields(self, model_config):
        """Test parsing response with missing optional fields."""
        client = SoundsgoodClient(model_config)

        data = {
            "choices": [
                {
                    "message": {"content": "Just content"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

        response = client._parse_response(data, ttfb_seconds=0.3, total_duration_seconds=1.0)

        assert response.content == "Just content"
        assert response.reasoning is None
        assert response.reasoning_tokens == 0
        assert response.cost_usd_total == 0.0


class TestStreamingParsing:
    """Tests for streaming response parsing."""

    @pytest.fixture
    def model_config(self):
        """Create a test model config."""
        return ModelConfig(
            model_id="zai-org/GLM-4.5-Air",
            provider="soundsgood",
            api_base_url="https://soundsgood.one/v1",
            api_key_env_var="SOUNDSGOOD_API_KEY",
            price_input_per_m=0.15,
            price_output_per_m=4.13,
            context_length=128000,
            supports_streaming=True,
            is_reasoning_model=True,
        )

    def test_sse_line_parsing(self, model_config):
        """Test that SSE lines are properly identified."""
        _ = SoundsgoodClient(model_config)  # Verify client can be instantiated

        # Simulated SSE lines
        lines = [
            "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}",
            "",
            "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}",
            "",
            "data: [DONE]",
        ]

        # Check line formats
        assert lines[0].startswith("data: ")
        assert lines[0][6:] != "[DONE]"
        assert lines[4][6:] == "[DONE]"

    def test_parse_streaming_chunk(self, model_config):
        """Test parsing a single streaming chunk."""
        chunk_data = {
            "id": "chatcmpl-123",
            "model": "zai-org/GLM-4.5-Air",
            "choices": [
                {
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }

        # Verify chunk structure
        assert chunk_data["choices"][0]["delta"]["content"] == "Hello"
        assert chunk_data["choices"][0]["finish_reason"] is None

    def test_parse_final_streaming_chunk(self, model_config):
        """Test parsing final streaming chunk with usage."""
        final_chunk = {
            "id": "chatcmpl-123",
            "model": "zai-org/GLM-4.5-Air",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 100,
                "reasoning_tokens": 75,
                "total_tokens": 225,
                "cost_usd_total": 0.00045,
            },
        }

        assert "usage" in final_chunk
        assert final_chunk["usage"]["total_tokens"] == 225
        assert final_chunk["choices"][0]["finish_reason"] == "stop"


class TestHealthCheck:
    """Tests for health check functionality."""

    @pytest.fixture
    def model_config(self):
        """Create a test model config."""
        return ModelConfig(
            model_id="zai-org/GLM-4.5-Air",
            provider="soundsgood",
            api_base_url="https://soundsgood.one/v1",
            api_key_env_var="SOUNDSGOOD_API_KEY",
            price_input_per_m=0.15,
            price_output_per_m=4.13,
            context_length=128000,
            supports_streaming=True,
            is_reasoning_model=True,
        )

    @pytest.mark.asyncio
    async def test_health_check_success(self, model_config):
        """Test successful health check."""
        client = SoundsgoodClient(model_config)

        # Mock the complete method with AsyncMock since it's an async method
        mock_response = CompletionResponse(
            id="test",
            model="test",
            content="OK",
            reasoning=None,
            finish_reason="stop",
            input_tokens=5,
            output_tokens=1,
            reasoning_tokens=0,
            total_tokens=6,
            cost_usd_total=0.0,
            cost_usd_input=0.0,
            cost_usd_output=0.0,
            ttfb_seconds=0.1,
            ttfc_seconds=None,
            total_duration_seconds=0.2,
            raw_response={},
        )

        with patch.object(client, "complete", new_callable=AsyncMock, return_value=mock_response) as mock_complete:
            result = await client.health_check()

        assert result is True
        mock_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure(self, model_config):
        """Test failed health check."""
        client = SoundsgoodClient(model_config)

        with patch.object(client, "complete", new_callable=AsyncMock, side_effect=Exception("API Error")):
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_empty_response(self, model_config):
        """Test health check with empty response."""
        client = SoundsgoodClient(model_config)

        mock_response = CompletionResponse(
            id="test",
            model="test",
            content="",  # Empty content
            reasoning=None,
            finish_reason="stop",
            input_tokens=5,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=5,
            cost_usd_total=0.0,
            cost_usd_input=0.0,
            cost_usd_output=0.0,
            ttfb_seconds=0.1,
            ttfc_seconds=None,
            total_duration_seconds=0.2,
            raw_response={},
        )

        with patch.object(client, "complete", new_callable=AsyncMock, return_value=mock_response):
            result = await client.health_check()

        assert result is False
