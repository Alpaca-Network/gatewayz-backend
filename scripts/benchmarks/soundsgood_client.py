"""
Soundsgood provider client for GLM-4.5-Air benchmarking.

Handles the reasoning model response format with separate 'reasoning' and 'content' fields.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from benchmark_config import ModelConfig

logger = logging.getLogger(__name__)


@dataclass
class CompletionResponse:
    """Parsed completion response from the API."""

    id: str
    model: str
    content: str
    reasoning: str | None
    finish_reason: str

    # Token usage
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int

    # Cost (from API response)
    cost_usd_total: float
    cost_usd_input: float
    cost_usd_output: float

    # Timing
    ttfb_seconds: float
    ttfc_seconds: float | None  # Time to first content (streaming only)
    total_duration_seconds: float

    # Raw response for debugging
    raw_response: dict[str, Any]


@dataclass
class StreamingMetrics:
    """Metrics collected during streaming."""

    ttfb_seconds: float  # Time to first byte
    ttfc_seconds: float  # Time to first content chunk
    total_duration_seconds: float
    chunks_received: int
    content_accumulated: str
    reasoning_accumulated: str


class SoundsgoodClient:
    """Async client for Soundsgood GLM-4.5-Air API."""

    def __init__(self, model_config: ModelConfig, timeout: float = 120.0):
        self.config = model_config
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SoundsgoodClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure client is initialized and return it."""
        if self._client is None:
            raise RuntimeError(
                "Client not initialized. Use 'async with SoundsgoodClient(...) as client:'"
            )
        return self._client

    @property
    def headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2000,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> CompletionResponse:
        """
        Make a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            stream: Whether to use streaming (affects TTFC measurement)

        Returns:
            CompletionResponse with content, reasoning, timing, and cost data
        """
        payload = {
            "model": self.config.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }

        if stream:
            payload["stream_options"] = {"include_usage": True}

        start_time = time.perf_counter()
        ttfb_time: float | None = None
        ttfc_time: float | None = None

        try:
            if stream:
                return await self._complete_streaming(payload, start_time)
            else:
                return await self._complete_non_streaming(payload, start_time)

        except httpx.TimeoutException as e:
            logger.error(f"Request timeout: {e}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def _complete_non_streaming(
        self, payload: dict[str, Any], start_time: float
    ) -> CompletionResponse:
        """Handle non-streaming completion."""
        client = self._ensure_client()
        response = await client.post(
            f"{self.config.api_base_url}/chat/completions",
            headers=self.headers,
            json=payload,
        )
        ttfb_time = time.perf_counter()
        response.raise_for_status()

        data = response.json()
        end_time = time.perf_counter()

        return self._parse_response(
            data,
            ttfb_seconds=ttfb_time - start_time,
            total_duration_seconds=end_time - start_time,
        )

    async def _complete_streaming(
        self, payload: dict[str, Any], start_time: float
    ) -> CompletionResponse:
        """Handle streaming completion with TTFC measurement."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        ttfb_time: float | None = None
        ttfc_time: float | None = None
        usage_data: dict[str, Any] = {}
        response_id = ""
        model = ""
        finish_reason = ""

        client = self._ensure_client()
        async with client.stream(
            "POST",
            f"{self.config.api_base_url}/chat/completions",
            headers=self.headers,
            json=payload,
        ) as response:
            ttfb_time = time.perf_counter()
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line.strip():
                    continue

                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        response_id = chunk.get("id", response_id)
                        model = chunk.get("model", model)

                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})

                            # Track content
                            if "content" in delta and delta["content"]:
                                if ttfc_time is None:
                                    ttfc_time = time.perf_counter()
                                content_parts.append(delta["content"])

                            # Track reasoning
                            if "reasoning" in delta and delta["reasoning"]:
                                reasoning_parts.append(delta["reasoning"])

                            # Track finish reason
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]

                        # Capture usage from final chunk
                        if "usage" in chunk:
                            usage_data = chunk["usage"]

                    except json.JSONDecodeError:
                        continue

        end_time = time.perf_counter()

        # Build response object
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts) if reasoning_parts else None

        return CompletionResponse(
            id=response_id,
            model=model,
            content=content,
            reasoning=reasoning,
            finish_reason=finish_reason or "stop",
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=usage_data.get("reasoning_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            cost_usd_total=usage_data.get("cost_usd_total", 0.0),
            cost_usd_input=usage_data.get("cost_usd_input", 0.0),
            cost_usd_output=usage_data.get("cost_usd_output", 0.0),
            ttfb_seconds=ttfb_time - start_time if ttfb_time else end_time - start_time,
            ttfc_seconds=ttfc_time - start_time if ttfc_time else None,
            total_duration_seconds=end_time - start_time,
            raw_response={
                "id": response_id,
                "model": model,
                "content": content,
                "reasoning": reasoning,
                "usage": usage_data,
            },
        )

    def _parse_response(
        self,
        data: dict[str, Any],
        ttfb_seconds: float,
        total_duration_seconds: float,
    ) -> CompletionResponse:
        """Parse non-streaming API response."""
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("No choices in response")

        message = choices[0].get("message", {})
        usage = data.get("usage", {})

        return CompletionResponse(
            id=data.get("id", ""),
            model=data.get("model", ""),
            content=message.get("content", ""),
            reasoning=message.get("reasoning"),
            finish_reason=choices[0].get("finish_reason", "stop"),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            cost_usd_total=usage.get("cost_usd_total", 0.0),
            cost_usd_input=usage.get("cost_usd_input", 0.0),
            cost_usd_output=usage.get("cost_usd_output", 0.0),
            ttfb_seconds=ttfb_seconds,
            ttfc_seconds=None,  # Not available for non-streaming
            total_duration_seconds=total_duration_seconds,
            raw_response=data,
        )

    async def health_check(self) -> bool:
        """Check if the API is reachable and responding."""
        try:
            response = await self.complete(
                messages=[{"role": "user", "content": "Say 'OK'"}],
                max_tokens=10,
                temperature=0.0,
            )
            return bool(response.content)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


async def test_client():
    """Test the client with a simple request."""
    from benchmark_config import SOUNDSGOOD_GLM_45_AIR

    config = SOUNDSGOOD_GLM_45_AIR

    print(f"Testing {config.model_id} at {config.api_base_url}")
    print(f"API Key: {'configured' if config.api_key else 'NOT SET'}")

    async with SoundsgoodClient(config) as client:
        # Test non-streaming
        print("\n--- Non-streaming test ---")
        response = await client.complete(
            messages=[{"role": "user", "content": "Write a hello world in Python"}],
            max_tokens=200,
            stream=False,
        )
        print(f"Content: {response.content[:200]}...")
        print(f"Reasoning: {response.reasoning[:200] if response.reasoning else 'None'}...")
        print(f"TTFB: {response.ttfb_seconds:.3f}s")
        print(f"Total: {response.total_duration_seconds:.3f}s")
        print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out, {response.reasoning_tokens} reasoning")
        print(f"Cost: ${response.cost_usd_total:.6f}")

        # Test streaming
        print("\n--- Streaming test ---")
        response = await client.complete(
            messages=[{"role": "user", "content": "Write a hello world in Python"}],
            max_tokens=200,
            stream=True,
        )
        print(f"Content: {response.content[:200]}...")
        print(f"TTFB: {response.ttfb_seconds:.3f}s")
        print(f"Total: {response.total_duration_seconds:.3f}s")


if __name__ == "__main__":
    asyncio.run(test_client())
