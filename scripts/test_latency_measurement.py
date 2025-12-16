#!/usr/bin/env python3
"""
Test script to demonstrate comprehensive latency measurement.

This script makes a request to the API and breaks down latency into
meaningful, actionable components.
"""

import asyncio
import json
import os
import time
from typing import Any

import httpx


class LatencyMeasurement:
    """Comprehensive latency measurement for AI chat requests"""

    def __init__(self, api_url: str, api_key: str | None = None):
        self.api_url = api_url
        self.api_key = api_key
        self.measurements: dict[str, Any] = {}

    async def measure_request(
        self,
        model: str,
        prompt: str,
        stream: bool = True,
        max_tokens: int = 100,
    ) -> dict[str, Any]:
        """
        Make a request and measure all latency components.

        Returns detailed breakdown of where time was spent.
        """
        # Reset measurements
        self.measurements = {
            "timestamps": {},
            "durations": {},
            "tokens": {},
            "metadata": {
                "model": model,
                "stream": stream,
                "prompt_length": len(prompt),
            },
        }

        # Mark: Client sent request
        self.measurements["timestamps"]["client_sent"] = time.time()

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if stream:
                    await self._measure_streaming_request(client, headers, payload)
                else:
                    await self._measure_non_streaming_request(client, headers, payload)

            # Calculate derived metrics
            self._calculate_metrics()

            return self.measurements

        except Exception as e:
            self.measurements["error"] = str(e)
            return self.measurements

    async def _measure_streaming_request(
        self, client: httpx.AsyncClient, headers: dict, payload: dict
    ):
        """Measure streaming request with TTFC tracking"""

        first_chunk_received = False
        chunks_received = 0
        response_text = ""

        async with client.stream(
            "POST", self.api_url, headers=headers, json=payload
        ) as response:
            # Mark: Response headers received (connection established)
            self.measurements["timestamps"]["headers_received"] = time.time()

            async for line in response.aiter_lines():
                if not line.strip() or line.startswith(":"):
                    continue

                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                        chunks_received += 1

                        # Mark: First chunk received (TTFC)
                        if not first_chunk_received:
                            self.measurements["timestamps"]["first_chunk"] = time.time()
                            first_chunk_received = True

                        # Collect content
                        if (
                            chunk.get("choices")
                            and chunk["choices"][0].get("delta", {}).get("content")
                        ):
                            response_text += chunk["choices"][0]["delta"]["content"]

                        # Mark: Last chunk (if it has finish_reason)
                        if chunk.get("choices") and chunk["choices"][0].get(
                            "finish_reason"
                        ):
                            self.measurements["timestamps"]["last_chunk"] = time.time()

                        # Extract usage from final chunk
                        if "usage" in chunk:
                            self.measurements["tokens"] = chunk["usage"]

                    except json.JSONDecodeError:
                        continue

        # Mark: Stream complete
        self.measurements["timestamps"]["stream_complete"] = time.time()
        self.measurements["metadata"]["chunks_received"] = chunks_received
        self.measurements["metadata"]["response_length"] = len(response_text)

    async def _measure_non_streaming_request(
        self, client: httpx.AsyncClient, headers: dict, payload: dict
    ):
        """Measure non-streaming request"""

        response = await client.post(self.api_url, headers=headers, json=payload)

        # Mark: Response received
        self.measurements["timestamps"]["response_received"] = time.time()

        # Parse response
        data = response.json()
        if "usage" in data:
            self.measurements["tokens"] = data["usage"]
        if "choices" in data and data["choices"]:
            self.measurements["metadata"]["response_length"] = len(
                data["choices"][0].get("message", {}).get("content", "")
            )

    def _calculate_metrics(self):
        """Calculate derived latency metrics"""
        ts = self.measurements["timestamps"]
        dur = self.measurements["durations"]

        client_sent = ts.get("client_sent")
        headers_received = ts.get("headers_received")
        first_chunk = ts.get("first_chunk")
        last_chunk = ts.get("last_chunk")

        # Time to First Chunk (TTFC) - Most important metric
        if client_sent and first_chunk:
            dur["ttfc"] = first_chunk - client_sent
            print(f"⭐ Time to First Chunk (TTFC): {dur['ttfc']:.3f}s")

        # Time to Last Chunk
        if client_sent and last_chunk:
            dur["ttlc"] = last_chunk - client_sent
            print(f"   Time to Last Chunk (TTLC): {dur['ttlc']:.3f}s")

        # Total request time
        if client_sent and last_chunk:
            dur["total"] = last_chunk - client_sent
            print(f"   Total Request Time: {dur['total']:.3f}s")

        # Network latency to establish connection
        if client_sent and headers_received:
            dur["connection"] = headers_received - client_sent
            print(f"   Connection Established: {dur['connection']:.3f}s")

        # Streaming duration (token generation time)
        if first_chunk and last_chunk:
            dur["streaming"] = last_chunk - first_chunk
            print(f"   Streaming Duration: {dur['streaming']:.3f}s")

        # Tokens per second
        tokens = self.measurements.get("tokens", {})
        completion_tokens = tokens.get("completion_tokens", 0)
        if completion_tokens > 0 and dur.get("streaming"):
            tps = completion_tokens / dur["streaming"]
            self.measurements["tokens"]["tokens_per_second"] = tps
            print(f"   Tokens/Second: {tps:.1f} tok/s")


async def main():
    """Run latency measurement test"""

    api_url = os.environ.get("API_URL", "http://localhost:8000/v1/chat/completions")
    api_key = os.environ.get("API_KEY")

    print("=" * 60)
    print("Latency Measurement Test")
    print("=" * 60)
    print(f"API URL: {api_url}")
    print(f"Authenticated: {bool(api_key)}\n")

    measurer = LatencyMeasurement(api_url, api_key)

    test_config = {
        "model": "openai/gpt-4o-mini",
        "prompt": "Write a haiku about programming",
        "stream": True,
        "max_tokens": 50,
    }

    print(f"Model: {test_config['model']}")
    print(f"Prompt: {test_config['prompt']}\n")
    print("Making request...\n")

    results = await measurer.measure_request(**test_config)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    if "error" in results:
        print(f"❌ Error: {results['error']}")
    else:
        tokens = results.get("tokens", {})
        print(f"Input tokens: {tokens.get('prompt_tokens', 'N/A')}")
        print(f"Output tokens: {tokens.get('completion_tokens', 'N/A')}")
        if "tokens_per_second" in tokens:
            print(f"Throughput: {tokens['tokens_per_second']:.1f} tokens/second")


if __name__ == "__main__":
    asyncio.run(main())
