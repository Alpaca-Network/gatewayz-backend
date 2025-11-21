#!/usr/bin/env python3
"""
Performance test for Gatewayz API
Tests response time for 10 different models with a basic math problem
"""

import asyncio
import time
import httpx
from typing import Dict, List
import statistics

# Configuration
API_BASE_URL = "https://api.gatewayz.ai"
API_KEY = "gw_live_keYT21TicJZzxObd8-6LJukxOg5p0CLo_3Yki83w3pU"

# Test models - selecting a diverse set of providers (mix of paid and free)
TEST_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-3.5-turbo",
    "gpt-4-turbo",
    "google/gemini-flash-1.5",
    "google/gemini-pro-1.5",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]

# Simple math problem
MATH_PROBLEM = "What is 127 + 389? Please provide only the numerical answer."


async def test_model(client: httpx.AsyncClient, model: str) -> Dict:
    """Test a single model and measure response time"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": MATH_PROBLEM}
        ],
        "max_tokens": 50,
        "temperature": 0.1,
    }

    start_time = time.time()

    try:
        response = await client.post(
            f"{API_BASE_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
        )

        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # Convert to milliseconds

        if response.status_code == 200:
            data = response.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "N/A")
            tokens = data.get("usage", {})

            return {
                "model": model,
                "status": "success",
                "response_time_ms": round(response_time, 2),
                "answer": answer.strip(),
                "tokens_used": tokens.get("total_tokens", "N/A"),
                "prompt_tokens": tokens.get("prompt_tokens", "N/A"),
                "completion_tokens": tokens.get("completion_tokens", "N/A"),
            }
        else:
            end_time = time.time()
            response_time = (end_time - start_time) * 1000
            return {
                "model": model,
                "status": "error",
                "response_time_ms": round(response_time, 2),
                "error": f"HTTP {response.status_code}: {response.text[:100]}",
            }

    except asyncio.TimeoutError:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000
        return {
            "model": model,
            "status": "timeout",
            "response_time_ms": round(response_time, 2),
            "error": "Request timed out after 60s",
        }
    except Exception as e:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000
        return {
            "model": model,
            "status": "error",
            "response_time_ms": round(response_time, 2),
            "error": str(e),
        }


async def run_performance_test():
    """Run performance test on all models"""
    print("=" * 80)
    print("GATEWAYZ API PERFORMANCE TEST")
    print("=" * 80)
    print(f"Testing {len(TEST_MODELS)} models with math problem: '{MATH_PROBLEM}'")
    print(f"API Endpoint: {API_BASE_URL}")
    print("=" * 80)
    print()

    async with httpx.AsyncClient() as client:
        # Test all models concurrently
        tasks = [test_model(client, model) for model in TEST_MODELS]
        results = await asyncio.gather(*tasks)

    # Display results
    print("\n📊 PERFORMANCE RESULTS")
    print("=" * 80)
    print(f"{'Model':<40} {'Time (ms)':<12} {'Status':<10} {'Answer':<10}")
    print("-" * 80)

    successful_results = []
    failed_results = []

    for result in results:
        model = result["model"]
        response_time = result["response_time_ms"]
        status = result["status"]

        if status == "success":
            answer = result.get("answer", "N/A")[:10]
            successful_results.append(result)
            print(f"{model:<40} {response_time:<12} ✓ {status:<10} {answer}")
        else:
            error = result.get("error", "Unknown error")[:30]
            failed_results.append(result)
            print(f"{model:<40} {response_time:<12} ✗ {status:<10} {error}")

    # Statistics
    print("\n" + "=" * 80)
    print("📈 STATISTICS")
    print("=" * 80)

    if successful_results:
        response_times = [r["response_time_ms"] for r in successful_results]

        print(f"Successful requests: {len(successful_results)}/{len(TEST_MODELS)}")
        print(f"Failed requests: {len(failed_results)}/{len(TEST_MODELS)}")
        print()
        print(f"Fastest response: {min(response_times):.2f}ms ({min(successful_results, key=lambda x: x['response_time_ms'])['model']})")
        print(f"Slowest response: {max(response_times):.2f}ms ({max(successful_results, key=lambda x: x['response_time_ms'])['model']})")
        print(f"Average response time: {statistics.mean(response_times):.2f}ms")
        print(f"Median response time: {statistics.median(response_times):.2f}ms")

        if len(response_times) > 1:
            print(f"Std deviation: {statistics.stdev(response_times):.2f}ms")
    else:
        print("❌ All requests failed!")

    # Detailed token usage
    if successful_results:
        print("\n" + "=" * 80)
        print("🎫 TOKEN USAGE DETAILS")
        print("=" * 80)
        print(f"{'Model':<40} {'Prompt':<10} {'Completion':<12} {'Total':<10}")
        print("-" * 80)

        for result in successful_results:
            model = result["model"]
            prompt = result.get("prompt_tokens", "N/A")
            completion = result.get("completion_tokens", "N/A")
            total = result.get("tokens_used", "N/A")
            print(f"{model:<40} {str(prompt):<10} {str(completion):<12} {str(total):<10}")

    # Failed requests details
    if failed_results:
        print("\n" + "=" * 80)
        print("❌ FAILED REQUESTS")
        print("=" * 80)
        for result in failed_results:
            print(f"\n{result['model']}:")
            print(f"  Status: {result['status']}")
            print(f"  Error: {result.get('error', 'Unknown')}")
            print(f"  Time taken: {result['response_time_ms']}ms")

    print("\n" + "=" * 80)
    print("Test completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_performance_test())
