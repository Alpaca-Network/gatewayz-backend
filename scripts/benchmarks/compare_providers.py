#!/usr/bin/env python3
"""
Compare GLM-4.5-Air between Soundsgood and Z.AI providers.

This script runs the same prompts against both providers and compares:
- Response quality and content
- Latency metrics (TTFB, total time)
- Token usage and pricing
- Streaming behavior
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx


@dataclass
class ComparisonResult:
    """Result from a single provider test."""

    provider: str
    model: str
    content: str
    reasoning: str | None

    # Timing
    ttfb_seconds: float
    ttfc_seconds: float | None
    total_duration_seconds: float

    # Tokens
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int

    # Cost
    cost_usd: float

    # Meta
    success: bool
    error: str | None = None
    raw_response: dict | None = None


async def call_soundsgood(
    messages: list[dict],
    api_key: str,
    stream: bool = True,
    max_tokens: int = 1000,
) -> ComparisonResult:
    """Call Soundsgood GLM-4.5-Air API."""
    url = "https://soundsgood.one/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "zai-org/GLM-4.5-Air",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": stream,
    }

    if stream:
        payload["stream_options"] = {"include_usage": True}

    start_time = time.perf_counter()
    ttfb_time = None
    ttfc_time = None

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                content_parts = []
                reasoning_parts = []
                usage_data = {}
                response_id = ""
                model = ""

                async with client.stream("POST", url, headers=headers, json=payload) as response:
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
                                    if "content" in delta and delta["content"]:
                                        if ttfc_time is None:
                                            ttfc_time = time.perf_counter()
                                        content_parts.append(delta["content"])
                                    if "reasoning" in delta and delta["reasoning"]:
                                        reasoning_parts.append(delta["reasoning"])

                                if "usage" in chunk:
                                    usage_data = chunk["usage"]
                            except json.JSONDecodeError:
                                continue

                end_time = time.perf_counter()
                content = "".join(content_parts)
                reasoning = "".join(reasoning_parts) if reasoning_parts else None

                return ComparisonResult(
                    provider="soundsgood",
                    model=model or "zai-org/GLM-4.5-Air",
                    content=content,
                    reasoning=reasoning,
                    ttfb_seconds=ttfb_time - start_time if ttfb_time else end_time - start_time,
                    ttfc_seconds=ttfc_time - start_time if ttfc_time else None,
                    total_duration_seconds=end_time - start_time,
                    input_tokens=usage_data.get("prompt_tokens", 0),
                    output_tokens=usage_data.get("completion_tokens", 0),
                    reasoning_tokens=usage_data.get("reasoning_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                    cost_usd=usage_data.get("cost_usd_total", 0.0),
                    success=True,
                    raw_response={"usage": usage_data},
                )
            else:
                response = await client.post(url, headers=headers, json=payload)
                ttfb_time = time.perf_counter()
                response.raise_for_status()
                data = response.json()
                end_time = time.perf_counter()

                message = data.get("choices", [{}])[0].get("message", {})
                usage = data.get("usage", {})

                return ComparisonResult(
                    provider="soundsgood",
                    model=data.get("model", "zai-org/GLM-4.5-Air"),
                    content=message.get("content", ""),
                    reasoning=message.get("reasoning"),
                    ttfb_seconds=ttfb_time - start_time,
                    ttfc_seconds=None,
                    total_duration_seconds=end_time - start_time,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    reasoning_tokens=usage.get("reasoning_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    cost_usd=usage.get("cost_usd_total", 0.0),
                    success=True,
                    raw_response=data,
                )

    except Exception as e:
        end_time = time.perf_counter()
        return ComparisonResult(
            provider="soundsgood",
            model="zai-org/GLM-4.5-Air",
            content="",
            reasoning=None,
            ttfb_seconds=end_time - start_time,
            ttfc_seconds=None,
            total_duration_seconds=end_time - start_time,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            success=False,
            error=str(e),
        )


async def call_zai(
    messages: list[dict],
    api_key: str,
    stream: bool = True,
    max_tokens: int = 1000,
) -> ComparisonResult:
    """Call Z.AI GLM-4.5-Air API directly.

    According to https://docs.z.ai/guides/llm/glm-4.5:
    - Endpoint: https://api.z.ai/api/paas/v4/chat/completions
    - Model: glm-4.5-air (lowercase)
    """
    url = "https://api.z.ai/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "glm-4.5-air",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": stream,
    }

    start_time = time.perf_counter()
    ttfb_time = None
    ttfc_time = None

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                content_parts = []
                reasoning_parts = []
                usage_data = {}
                response_id = ""
                model = ""

                async with client.stream("POST", url, headers=headers, json=payload) as response:
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
                                    if "content" in delta and delta["content"]:
                                        if ttfc_time is None:
                                            ttfc_time = time.perf_counter()
                                        content_parts.append(delta["content"])
                                    # Z.AI may use different field for reasoning
                                    if "reasoning_content" in delta and delta["reasoning_content"]:
                                        reasoning_parts.append(delta["reasoning_content"])
                                    elif "reasoning" in delta and delta["reasoning"]:
                                        reasoning_parts.append(delta["reasoning"])

                                if "usage" in chunk:
                                    usage_data = chunk["usage"]
                            except json.JSONDecodeError:
                                continue

                end_time = time.perf_counter()
                content = "".join(content_parts)
                reasoning = "".join(reasoning_parts) if reasoning_parts else None

                return ComparisonResult(
                    provider="z.ai",
                    model=model or "glm-4.5-air",
                    content=content,
                    reasoning=reasoning,
                    ttfb_seconds=ttfb_time - start_time if ttfb_time else end_time - start_time,
                    ttfc_seconds=ttfc_time - start_time if ttfc_time else None,
                    total_duration_seconds=end_time - start_time,
                    input_tokens=usage_data.get("prompt_tokens", 0),
                    output_tokens=usage_data.get("completion_tokens", 0),
                    reasoning_tokens=usage_data.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
                    total_tokens=usage_data.get("total_tokens", 0),
                    cost_usd=0.0,  # Z.AI may not return cost
                    success=True,
                    raw_response={"usage": usage_data},
                )
            else:
                response = await client.post(url, headers=headers, json=payload)
                ttfb_time = time.perf_counter()
                response.raise_for_status()
                data = response.json()
                end_time = time.perf_counter()

                message = data.get("choices", [{}])[0].get("message", {})
                usage = data.get("usage", {})

                return ComparisonResult(
                    provider="z.ai",
                    model=data.get("model", "glm-4.5-air"),
                    content=message.get("content", ""),
                    reasoning=message.get("reasoning_content") or message.get("reasoning"),
                    ttfb_seconds=ttfb_time - start_time,
                    ttfc_seconds=None,
                    total_duration_seconds=end_time - start_time,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    reasoning_tokens=usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    cost_usd=0.0,
                    success=True,
                    raw_response=data,
                )

    except httpx.HTTPStatusError as e:
        end_time = time.perf_counter()
        error_body = ""
        try:
            error_body = e.response.text
        except Exception:
            pass
        return ComparisonResult(
            provider="z.ai",
            model="glm-4.5-air",
            content="",
            reasoning=None,
            ttfb_seconds=end_time - start_time,
            ttfc_seconds=None,
            total_duration_seconds=end_time - start_time,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            success=False,
            error=f"{str(e)} | Response: {error_body[:500]}",
        )
    except Exception as e:
        end_time = time.perf_counter()
        return ComparisonResult(
            provider="z.ai",
            model="glm-4.5-air",
            content="",
            reasoning=None,
            ttfb_seconds=end_time - start_time,
            ttfc_seconds=None,
            total_duration_seconds=end_time - start_time,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            success=False,
            error=str(e),
        )


def print_result(result: ComparisonResult, verbose: bool = False):
    """Print a comparison result."""
    status = "SUCCESS" if result.success else f"FAILED: {result.error}"
    print(f"\n{'='*60}")
    print(f"Provider: {result.provider.upper()}")
    print(f"Model: {result.model}")
    print(f"Status: {status}")
    print(f"{'-'*60}")
    print(f"Latency:")
    print(f"  TTFB: {result.ttfb_seconds:.3f}s")
    if result.ttfc_seconds:
        print(f"  TTFC: {result.ttfc_seconds:.3f}s")
    print(f"  Total: {result.total_duration_seconds:.3f}s")
    print(f"{'-'*60}")
    print(f"Tokens:")
    print(f"  Input: {result.input_tokens}")
    print(f"  Output: {result.output_tokens}")
    if result.reasoning_tokens:
        print(f"  Reasoning: {result.reasoning_tokens}")
    print(f"  Total: {result.total_tokens}")
    if result.cost_usd:
        print(f"  Cost: ${result.cost_usd:.6f}")
    print(f"{'-'*60}")

    if result.success:
        content_preview = result.content[:500] if len(result.content) > 500 else result.content
        print(f"Content Preview:\n{content_preview}")
        if result.reasoning:
            reasoning_preview = result.reasoning[:300] if len(result.reasoning) > 300 else result.reasoning
            print(f"\nReasoning Preview:\n{reasoning_preview}")

    if verbose and result.raw_response:
        print(f"\nRaw Response:\n{json.dumps(result.raw_response, indent=2)}")


async def run_comparison(
    soundsgood_key: str,
    zai_key: str,
    prompts: list[dict],
    stream: bool = True,
    sequential: bool = False,
):
    """Run comparison tests."""
    print(f"\n{'#'*60}")
    print(f"# GLM-4.5-Air Provider Comparison")
    print(f"# Streaming: {stream}")
    print(f"# Sequential: {sequential}")
    print(f"# Time: {datetime.now().isoformat()}")
    print(f"{'#'*60}")

    all_results = []

    for i, prompt_data in enumerate(prompts, 1):
        prompt = prompt_data["prompt"]
        name = prompt_data.get("name", f"Test {i}")

        print(f"\n\n{'*'*60}")
        print(f"* TEST {i}: {name}")
        print(f"* Prompt: {prompt[:100]}...")
        print(f"{'*'*60}")

        messages = [{"role": "user", "content": prompt}]

        if sequential:
            # Run sequentially with delay
            soundsgood_result = await call_soundsgood(messages, soundsgood_key, stream=stream)
            await asyncio.sleep(2)  # Delay before Z.AI call
            zai_result = await call_zai(messages, zai_key, stream=stream)
            await asyncio.sleep(2)  # Delay between tests
        else:
            # Run both providers concurrently
            soundsgood_result, zai_result = await asyncio.gather(
                call_soundsgood(messages, soundsgood_key, stream=stream),
                call_zai(messages, zai_key, stream=stream),
            )

        print_result(soundsgood_result)
        print_result(zai_result)

        # Comparison summary
        print(f"\n{'~'*60}")
        print("COMPARISON SUMMARY:")
        if soundsgood_result.success and zai_result.success:
            ttfb_diff = soundsgood_result.ttfb_seconds - zai_result.ttfb_seconds
            total_diff = soundsgood_result.total_duration_seconds - zai_result.total_duration_seconds

            faster_ttfb = "Z.AI" if ttfb_diff > 0 else "Soundsgood"
            faster_total = "Z.AI" if total_diff > 0 else "Soundsgood"

            print(f"  TTFB: {faster_ttfb} faster by {abs(ttfb_diff):.3f}s")
            print(f"  Total: {faster_total} faster by {abs(total_diff):.3f}s")

            # Content length comparison
            sg_len = len(soundsgood_result.content)
            zai_len = len(zai_result.content)
            print(f"  Content Length: Soundsgood={sg_len} chars, Z.AI={zai_len} chars")

            # Token comparison
            print(f"  Output Tokens: Soundsgood={soundsgood_result.output_tokens}, Z.AI={zai_result.output_tokens}")
        else:
            if not soundsgood_result.success:
                print(f"  Soundsgood FAILED: {soundsgood_result.error}")
            if not zai_result.success:
                print(f"  Z.AI FAILED: {zai_result.error}")

        all_results.append({
            "test_name": name,
            "prompt": prompt,
            "soundsgood": asdict(soundsgood_result),
            "zai": asdict(zai_result),
        })

    # Save results
    output_file = f"comparison_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n\nResults saved to: {output_file}")

    return all_results


# Test prompts for comparison
TEST_PROMPTS = [
    {
        "name": "Simple Hello",
        "prompt": "Hello, tell me about yourself in 2-3 sentences.",
    },
    {
        "name": "Python Function",
        "prompt": "Write a Python function to check if a string is a palindrome. Include docstring and type hints.",
    },
    {
        "name": "Debugging",
        "prompt": """Debug this Python code:
```python
def factorial(n):
    if n == 0:
        return 1
    return n * factorial(n)
```
Explain the bug and provide the fix.""",
    },
    {
        "name": "Algorithm Explanation",
        "prompt": "Explain how quicksort works with a simple example. Be concise.",
    },
    {
        "name": "Code Refactoring",
        "prompt": """Refactor this code to be more Pythonic:
```python
result = []
for i in range(len(items)):
    if items[i] > 0:
        result.append(items[i] * 2)
```""",
    },
]


async def main():
    """Main entry point."""
    # API Keys
    SOUNDSGOOD_KEY = "sg_2e430d39e6d2f1ecbc898e4d136dc31d133e7e577b68c9d4e82f5e314c852639"
    ZAI_KEY = "7ae4ef31036f4a22a29a479fb8a0493a.5hwGmpbuAqqfe3iW"

    print("Running GLM-4.5-Air comparison tests...")
    print(f"Soundsgood API Key: {SOUNDSGOOD_KEY[:20]}...")
    print(f"Z.AI API Key: {ZAI_KEY[:20]}...")

    # Run streaming comparison (sequential to avoid rate limits)
    await run_comparison(
        SOUNDSGOOD_KEY,
        ZAI_KEY,
        TEST_PROMPTS,
        stream=True,
        sequential=True,  # Run sequentially to avoid Z.AI rate limits
    )


if __name__ == "__main__":
    asyncio.run(main())
