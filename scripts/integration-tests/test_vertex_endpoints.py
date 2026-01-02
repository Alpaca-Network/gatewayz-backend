#!/usr/bin/env python3
"""
Test script for Vertex AI endpoints (both regional and global)

Tests:
1. Regional endpoint with Gemini 2.x models
2. Global endpoint with Gemini 3 models
3. Streaming responses

Usage:
    python scripts/integration-tests/test_vertex_endpoints.py

Requires:
    - GOOGLE_VERTEX_CREDENTIALS_JSON env var
    - GOOGLE_PROJECT_ID env var (defaults to 'gatewayz-468519')
    - GOOGLE_VERTEX_LOCATION env var (defaults to 'us-central1')
"""

import json
import os
import sys
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Check for credentials
credentials_json = os.getenv('GOOGLE_VERTEX_CREDENTIALS_JSON')
if not credentials_json:
    print("❌ GOOGLE_VERTEX_CREDENTIALS_JSON not found")
    print("   Set this environment variable with your service account JSON")
    sys.exit(1)

project_id = os.getenv('GOOGLE_PROJECT_ID', 'gatewayz-468519')
location = os.getenv('GOOGLE_VERTEX_LOCATION', 'us-central1')

print("=" * 60)
print("Vertex AI Endpoint Test")
print("=" * 60)
print(f"📍 Project: {project_id}")
print(f"📍 Default Location: {location}")
print(f"⏰ Time: {datetime.now().isoformat()}")
print()

# Import after env vars are loaded
try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    import httpx
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Run: pip install google-auth httpx")
    sys.exit(1)

# Parse credentials
try:
    credentials_dict = json.loads(credentials_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict,
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    credentials.refresh(Request())
    access_token = credentials.token
    print(f"✅ Got access token: {access_token[:20]}...")
    print()
except Exception as e:
    print(f"❌ Failed to get access token: {e}")
    sys.exit(1)


def test_model(model_name: str, use_global: bool = False, stream: bool = False) -> dict:
    """Test a single model with the appropriate endpoint."""

    # Determine endpoint
    if use_global:
        base_url = "https://aiplatform.googleapis.com/v1"
        endpoint_location = "global"
    else:
        base_url = f"https://{location}-aiplatform.googleapis.com/v1"
        endpoint_location = location

    endpoint_type = "GLOBAL" if use_global else "REGIONAL"

    # Build URL
    if stream:
        url = (
            f"{base_url}/projects/{project_id}/locations/{endpoint_location}/"
            f"publishers/google/models/{model_name}:streamGenerateContent"
        )
    else:
        url = (
            f"{base_url}/projects/{project_id}/locations/{endpoint_location}/"
            f"publishers/google/models/{model_name}:generateContent"
        )

    print(f"🧪 Testing: {model_name}")
    print(f"   Endpoint: {endpoint_type} ({endpoint_location})")
    print(f"   Streaming: {stream}")
    print(f"   URL: {url[:80]}...")

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": "Say 'Hello from Vertex AI' and tell me your model name. Keep it brief."}]
        }],
        "generationConfig": {
            "maxOutputTokens": 100,
            "temperature": 0.1
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    result = {
        "model": model_name,
        "endpoint": endpoint_type,
        "location": endpoint_location,
        "streaming": stream,
        "status": None,
        "response_text": None,
        "error": None,
        "tokens": None
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            start_time = datetime.now()

            if stream:
                # Streaming request
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    result["status"] = response.status_code

                    if response.status_code == 200:
                        full_text = ""
                        chunk_count = 0

                        for line in response.iter_lines():
                            if line:
                                chunk_count += 1
                                try:
                                    # Parse the chunk (may have "data: " prefix)
                                    if line.startswith("data: "):
                                        line = line[6:]
                                    chunk_data = json.loads(line)

                                    if "candidates" in chunk_data:
                                        for candidate in chunk_data["candidates"]:
                                            if "content" in candidate and "parts" in candidate["content"]:
                                                for part in candidate["content"]["parts"]:
                                                    if "text" in part:
                                                        full_text += part["text"]
                                except json.JSONDecodeError:
                                    pass

                        result["response_text"] = full_text
                        result["chunk_count"] = chunk_count
                        print(f"   ✅ SUCCESS (streaming, {chunk_count} chunks)")
                    else:
                        result["error"] = response.text[:500]
                        print(f"   ❌ FAILED: HTTP {response.status_code}")
            else:
                # Non-streaming request
                response = client.post(url, headers=headers, json=payload)
                result["status"] = response.status_code

                elapsed = (datetime.now() - start_time).total_seconds()

                if response.status_code == 200:
                    data = response.json()

                    # Extract response text
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            parts = candidate["content"]["parts"]
                            text = "".join(p.get("text", "") for p in parts)
                            result["response_text"] = text

                    # Extract token usage
                    if "usageMetadata" in data:
                        usage = data["usageMetadata"]
                        result["tokens"] = {
                            "prompt": usage.get("promptTokenCount", 0),
                            "completion": usage.get("candidatesTokenCount", 0),
                            "total": usage.get("totalTokenCount", 0)
                        }

                    print(f"   ✅ SUCCESS ({elapsed:.2f}s)")
                    if result["tokens"]:
                        print(f"   📊 Tokens: {result['tokens']['prompt']} in, {result['tokens']['completion']} out")
                else:
                    result["error"] = response.text[:500]
                    print(f"   ❌ FAILED: HTTP {response.status_code}")

                    # Parse error for more details
                    try:
                        error_data = response.json()
                        if "error" in error_data:
                            err = error_data["error"]
                            print(f"   Error: {err.get('message', 'Unknown')[:100]}")
                    except:
                        print(f"   Error: {response.text[:100]}")

    except httpx.TimeoutException:
        result["error"] = "Request timed out after 60s"
        print(f"   ❌ TIMEOUT")
    except Exception as e:
        result["error"] = str(e)
        print(f"   ❌ EXCEPTION: {e}")

    if result["response_text"]:
        print(f"   💬 Response: {result['response_text'][:80]}...")
    print()

    return result


def main():
    results = []

    # Test 1: Regional endpoint with Gemini 2.x models
    print("=" * 60)
    print("TEST 1: Regional Endpoint (Gemini 2.x)")
    print("=" * 60)
    print()

    regional_models = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]

    for model in regional_models:
        result = test_model(model, use_global=False, stream=False)
        results.append(result)

    # Test 2: Global endpoint with Gemini 3 models
    print("=" * 60)
    print("TEST 2: Global Endpoint (Gemini 3)")
    print("=" * 60)
    print()

    global_models = [
        "gemini-3-flash",
        "gemini-3-pro",
    ]

    for model in global_models:
        result = test_model(model, use_global=True, stream=False)
        results.append(result)

    # Test 3: Streaming on regional endpoint
    print("=" * 60)
    print("TEST 3: Streaming (Regional)")
    print("=" * 60)
    print()

    result = test_model("gemini-2.0-flash", use_global=False, stream=True)
    results.append(result)

    # Test 4: Streaming on global endpoint
    print("=" * 60)
    print("TEST 4: Streaming (Global)")
    print("=" * 60)
    print()

    result = test_model("gemini-3-flash", use_global=True, stream=True)
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
        print("Failed tests:")
        for f in failures:
            print(f"  ❌ {f['model']} ({f['endpoint']}, streaming={f['streaming']})")
            print(f"     Status: {f['status']}")
            if f["error"]:
                print(f"     Error: {f['error'][:100]}")
        print()

    # Show successes
    successes = [r for r in results if r["status"] == 200]
    if successes:
        print("Successful tests:")
        for s in successes:
            streaming_tag = " [streaming]" if s["streaming"] else ""
            print(f"  ✅ {s['model']} ({s['endpoint']}){streaming_tag}")

    return 0 if success_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())
