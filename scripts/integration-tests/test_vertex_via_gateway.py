#!/usr/bin/env python3
"""
Test Vertex AI models via the Gatewayz API Gateway

This tests the actual production API to verify:
1. Regional endpoint works for Gemini 2.x models
2. Global endpoint works for Gemini 3 models
3. Streaming works correctly

Usage:
    GATEWAYZ_API_KEY=gw_xxx python scripts/integration-tests/test_vertex_via_gateway.py

Requires:
    - GATEWAYZ_API_KEY environment variable
"""

import json
import os
import sys
from datetime import datetime

try:
    import httpx
except ImportError:
    print("❌ Missing httpx. Run: pip install httpx")
    sys.exit(1)

API_BASE_URL = os.getenv("GATEWAYZ_API_URL", "https://api.gatewayz.ai")
API_KEY = os.getenv("GATEWAYZ_API_KEY")

if not API_KEY:
    print("❌ GATEWAYZ_API_KEY not found")
    print("   Set this environment variable with your Gatewayz API key")
    sys.exit(1)

print("=" * 60)
print("Vertex AI Test via Gatewayz Gateway")
print("=" * 60)
print(f"🌐 API URL: {API_BASE_URL}")
print(f"🔑 API Key: {API_KEY[:10]}...{API_KEY[-4:]}")
print(f"⏰ Time: {datetime.now().isoformat()}")
print()


def test_model(model_name: str, stream: bool = False) -> dict:
    """Test a single model via the gateway."""

    url = f"{API_BASE_URL}/v1/chat/completions"

    print(f"🧪 Testing: {model_name}")
    print(f"   Streaming: {stream}")

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "Say 'Hello from Vertex AI' and tell me your model name. Keep it brief (1-2 sentences max)."
            }
        ],
        "max_tokens": 100,
        "temperature": 0.1,
        "stream": stream
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    result = {
        "model": model_name,
        "streaming": stream,
        "status": None,
        "response_text": None,
        "error": None,
        "tokens": None,
        "latency_ms": None,
        "provider": None
    }

    try:
        with httpx.Client(timeout=90.0) as client:
            start_time = datetime.now()

            if stream:
                # Streaming request
                full_text = ""
                chunk_count = 0
                finish_reason = None

                with client.stream("POST", url, headers=headers, json=payload) as response:
                    result["status"] = response.status_code

                    if response.status_code == 200:
                        for line in response.iter_lines():
                            if line:
                                if line.startswith("data: "):
                                    line = line[6:]
                                if line == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(line)
                                    chunk_count += 1

                                    if "choices" in chunk and len(chunk["choices"]) > 0:
                                        delta = chunk["choices"][0].get("delta", {})
                                        if "content" in delta:
                                            full_text += delta["content"]
                                        if chunk["choices"][0].get("finish_reason"):
                                            finish_reason = chunk["choices"][0]["finish_reason"]
                                except json.JSONDecodeError:
                                    pass

                        elapsed = (datetime.now() - start_time).total_seconds() * 1000
                        result["latency_ms"] = elapsed
                        result["response_text"] = full_text
                        result["chunk_count"] = chunk_count
                        result["finish_reason"] = finish_reason
                        print(f"   ✅ SUCCESS ({elapsed:.0f}ms, {chunk_count} chunks)")
                    else:
                        result["error"] = response.text[:500]
                        print(f"   ❌ FAILED: HTTP {response.status_code}")
            else:
                # Non-streaming request
                response = client.post(url, headers=headers, json=payload)
                elapsed = (datetime.now() - start_time).total_seconds() * 1000
                result["status"] = response.status_code
                result["latency_ms"] = elapsed

                if response.status_code == 200:
                    data = response.json()

                    # Extract response text
                    if "choices" in data and len(data["choices"]) > 0:
                        message = data["choices"][0].get("message", {})
                        result["response_text"] = message.get("content", "")
                        result["finish_reason"] = data["choices"][0].get("finish_reason")

                    # Extract token usage
                    if "usage" in data:
                        usage = data["usage"]
                        result["tokens"] = {
                            "prompt": usage.get("prompt_tokens", 0),
                            "completion": usage.get("completion_tokens", 0),
                            "total": usage.get("total_tokens", 0)
                        }

                    # Check for provider info in headers
                    result["provider"] = response.headers.get("x-provider", "unknown")

                    print(f"   ✅ SUCCESS ({elapsed:.0f}ms)")
                    if result["tokens"]:
                        print(f"   📊 Tokens: {result['tokens']['prompt']} in, {result['tokens']['completion']} out")
                    if result["provider"] and result["provider"] != "unknown":
                        print(f"   🏢 Provider: {result['provider']}")
                else:
                    result["error"] = response.text[:500]
                    print(f"   ❌ FAILED: HTTP {response.status_code}")

                    # Parse error for more details
                    try:
                        error_data = response.json()
                        if "error" in error_data:
                            err = error_data["error"]
                            msg = err.get("message", str(err))
                            print(f"   Error: {msg[:150]}")
                    except:
                        print(f"   Error: {response.text[:150]}")

    except httpx.TimeoutException:
        result["error"] = "Request timed out after 90s"
        print(f"   ❌ TIMEOUT")
    except Exception as e:
        result["error"] = str(e)
        print(f"   ❌ EXCEPTION: {e}")

    if result["response_text"]:
        text = result["response_text"].replace("\n", " ")[:100]
        print(f"   💬 Response: {text}...")
    print()

    return result


def main():
    results = []

    # Test Gemini 2.x models (Regional endpoint)
    print("=" * 60)
    print("TEST 1: Gemini 2.x Models (Regional Endpoint)")
    print("=" * 60)
    print()

    regional_models = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]

    for model in regional_models:
        result = test_model(model, stream=False)
        results.append(result)

    # Test Gemini 3 models (Global endpoint)
    print("=" * 60)
    print("TEST 2: Gemini 3 Models (Global Endpoint)")
    print("=" * 60)
    print()

    global_models = [
        "gemini-3-flash",
        "gemini-3-pro",
    ]

    for model in global_models:
        result = test_model(model, stream=False)
        results.append(result)

    # Test streaming on Gemini 2.x (Regional)
    print("=" * 60)
    print("TEST 3: Streaming - Gemini 2.x (Regional)")
    print("=" * 60)
    print()

    result = test_model("gemini-2.5-flash", stream=True)
    results.append(result)

    # Test streaming on Gemini 3 (Global)
    print("=" * 60)
    print("TEST 4: Streaming - Gemini 3 (Global)")
    print("=" * 60)
    print()

    result = test_model("gemini-3-flash", stream=True)
    results.append(result)

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()

    success_count = sum(1 for r in results if r["status"] == 200)
    total_count = len(results)

    print(f"Total tests: {total_count}")
    print(f"Successful:  {success_count}")
    print(f"Failed:      {total_count - success_count}")
    print()

    # Show failures
    failures = [r for r in results if r["status"] != 200]
    if failures:
        print("❌ Failed tests:")
        for f in failures:
            streaming_tag = " [streaming]" if f["streaming"] else ""
            print(f"   • {f['model']}{streaming_tag}")
            print(f"     Status: {f['status']}")
            if f["error"]:
                error_preview = f["error"][:200].replace("\n", " ")
                print(f"     Error: {error_preview}")
        print()

    # Show successes
    successes = [r for r in results if r["status"] == 200]
    if successes:
        print("✅ Successful tests:")
        for s in successes:
            streaming_tag = " [streaming]" if s["streaming"] else ""
            latency = f" ({s['latency_ms']:.0f}ms)" if s.get("latency_ms") else ""
            print(f"   • {s['model']}{streaming_tag}{latency}")

    print()

    if success_count == total_count:
        print("🎉 All tests passed!")
        return 0
    else:
        print(f"⚠️ {total_count - success_count} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
