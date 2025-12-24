"""
Smoke tests for unified chat endpoint.

These tests validate basic functionality in a deployed environment.
They should be run after deployment to staging/production.
"""

import os
import pytest
import httpx
from typing import Optional


# Test configuration
BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("TEST_API_KEY", None)  # Optional for anonymous tests


class TestUnifiedEndpointSmoke:
    """Smoke tests for /v1/chat endpoint"""

    @pytest.fixture
    def client(self):
        """HTTP client for testing"""
        return httpx.Client(base_url=BASE_URL, timeout=30.0)

    @pytest.fixture
    def headers(self):
        """Request headers with optional API key"""
        if API_KEY:
            return {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
        return {"Content-Type": "application/json"}

    def test_endpoint_exists(self, client):
        """Test that the unified endpoint exists and responds"""
        response = client.options(f"{BASE_URL}/v1/chat")
        # Should get 405 or 200 (endpoint exists but wrong method)
        assert response.status_code in [200, 405], \
            f"Endpoint doesn't exist or is unreachable: {response.status_code}"

    def test_openai_format_basic(self, client, headers):
        """Test basic OpenAI format request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "Say 'hello' in one word"}
            ],
            "max_tokens": 10
        }

        response = client.post("/v1/chat", json=payload, headers=headers)

        # Accept 200 (success) or 401/402 (auth/credits) for smoke test
        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert "object" in data
            assert data["object"] == "chat.completion"
            assert "choices" in data
            print(f"✅ OpenAI format works: {data['choices'][0]['message']['content']}")

    def test_anthropic_format_basic(self, client, headers):
        """Test basic Anthropic format request"""
        payload = {
            "model": "claude-3-haiku-20240307",
            "system": "You are concise",
            "messages": [
                {"role": "user", "content": "Say 'hello' in one word"}
            ],
            "max_tokens": 10
        }

        response = client.post("/v1/chat", json=payload, headers=headers)

        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert "type" in data
            assert data["type"] == "message"
            assert "content" in data
            print(f"✅ Anthropic format works: {data['content'][0]['text']}")

    def test_responses_api_format_basic(self, client, headers):
        """Test basic Responses API format request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [
                {"role": "user", "content": "Say 'hello' in one word"}
            ]
        }

        response = client.post("/v1/chat", json=payload, headers=headers)

        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert "object" in data
            assert data["object"] == "response"
            assert "output" in data
            print(f"✅ Responses API format works: {data['output'][0]['content']}")

    def test_format_headers_present(self, client, headers):
        """Test that format detection headers are present"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "Hi"}
            ],
            "max_tokens": 5
        }

        response = client.post("/v1/chat", json=payload, headers=headers)

        if response.status_code == 200:
            assert "X-Request-Format" in response.headers, \
                "Missing X-Request-Format header"
            assert "X-Response-Format" in response.headers, \
                "Missing X-Response-Format header"

            assert response.headers["X-Request-Format"] == "openai"
            assert response.headers["X-Response-Format"] == "openai"
            print(f"✅ Format headers present: {response.headers['X-Request-Format']}")

    def test_invalid_request_returns_422(self, client, headers):
        """Test that invalid requests return 422"""
        payload = {
            # Missing required 'model' field
            "messages": [
                {"role": "user", "content": "Hi"}
            ]
        }

        response = client.post("/v1/chat", json=payload, headers=headers)
        assert response.status_code == 422, \
            f"Expected 422 for invalid request, got {response.status_code}"
        print("✅ Invalid request handling works")

    def test_explicit_format_override(self, client, headers):
        """Test explicit format field override"""
        payload = {
            "format": "anthropic",  # Explicit Anthropic
            "model": "gpt-3.5-turbo",
            "messages": [  # OpenAI-style messages
                {"role": "user", "content": "Hi"}
            ],
            "max_tokens": 5
        }

        response = client.post("/v1/chat", json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            # Should return Anthropic format due to override
            assert data["type"] == "message"
            assert response.headers["X-Response-Format"] == "anthropic"
            print("✅ Explicit format override works")


class TestLegacyEndpointsDeprecation:
    """Test that legacy endpoints show deprecation headers"""

    @pytest.fixture
    def client(self):
        """HTTP client for testing"""
        return httpx.Client(base_url=BASE_URL, timeout=30.0)

    @pytest.fixture
    def headers(self):
        """Request headers"""
        if API_KEY:
            return {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
        return {"Content-Type": "application/json"}

    def test_chat_completions_deprecation_headers(self, client, headers):
        """Test /v1/chat/completions shows deprecation headers"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5
        }

        response = client.post("/v1/chat/completions", json=payload, headers=headers)

        # Check deprecation headers are present (regardless of status code)
        assert "Deprecation" in response.headers or response.status_code in [401, 402], \
            "Missing deprecation header on legacy endpoint"

        if "Deprecation" in response.headers:
            assert response.headers["Deprecation"] == "true"
            assert "Sunset" in response.headers
            assert "Link" in response.headers
            assert "/v1/chat" in response.headers["Link"]
            print(f"✅ Deprecation headers present on /v1/chat/completions")
            print(f"   Sunset: {response.headers.get('Sunset')}")
            print(f"   Link: {response.headers.get('Link')}")

    def test_messages_deprecation_headers(self, client, headers):
        """Test /v1/messages shows deprecation headers"""
        payload = {
            "model": "claude-3-haiku-20240307",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5
        }

        response = client.post("/v1/messages", json=payload, headers=headers)

        if "Deprecation" in response.headers:
            assert response.headers["Deprecation"] == "true"
            assert "X-API-Warn" in response.headers
            print(f"✅ Deprecation headers present on /v1/messages")


class TestHealthAndMonitoring:
    """Test health and monitoring endpoints"""

    @pytest.fixture
    def client(self):
        """HTTP client for testing"""
        return httpx.Client(base_url=BASE_URL, timeout=10.0)

    def test_health_endpoint(self, client):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        print(f"✅ Health check: {data.get('status')}")

    def test_metrics_endpoint_accessible(self, client):
        """Test Prometheus metrics endpoint is accessible"""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        print("✅ Metrics endpoint accessible")


if __name__ == "__main__":
    """Run smoke tests directly"""
    print("=" * 60)
    print("🔥 Running Unified Endpoint Smoke Tests")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"API Key: {'Set' if API_KEY else 'Not set (anonymous mode)'}")
    print("=" * 60)

    pytest.main([__file__, "-v", "--tb=short", "-s"])
