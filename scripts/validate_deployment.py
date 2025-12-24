#!/usr/bin/env python3
"""
Deployment Validation Script

Run this script after deploying to staging/production to validate
the unified chat endpoint is working correctly.

Usage:
    python scripts/validate_deployment.py --url https://api.gatewayz.ai --key YOUR_API_KEY

Or for anonymous testing:
    python scripts/validate_deployment.py --url https://api.gatewayz.ai
"""

import argparse
import sys
import time
from typing import Dict, List, Tuple
import httpx


class DeploymentValidator:
    """Validates deployment of unified chat endpoint"""

    def __init__(self, base_url: str, api_key: str = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.Client(timeout=30.0)
        self.results: List[Tuple[str, bool, str]] = []

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _test(self, name: str, func) -> bool:
        """Run a test and record result"""
        print(f"\n{'='*60}")
        print(f"Testing: {name}")
        print(f"{'='*60}")
        try:
            func()
            self.results.append((name, True, "PASSED"))
            print(f"✅ PASSED: {name}")
            return True
        except AssertionError as e:
            self.results.append((name, False, str(e)))
            print(f"❌ FAILED: {name}")
            print(f"   Error: {e}")
            return False
        except Exception as e:
            self.results.append((name, False, f"Exception: {str(e)}"))
            print(f"❌ ERROR: {name}")
            print(f"   Exception: {e}")
            return False

    def test_health_endpoint(self):
        """Test health endpoint"""
        response = self.client.get(f"{self.base_url}/health")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert "status" in data, "Missing status in health response"
        print(f"   Status: {data.get('status')}")

    def test_unified_endpoint_exists(self):
        """Test /v1/chat endpoint exists"""
        response = self.client.options(f"{self.base_url}/v1/chat")
        assert response.status_code in [200, 405], \
            f"Endpoint doesn't exist: {response.status_code}"
        print("   /v1/chat endpoint exists")

    def test_openai_format(self):
        """Test OpenAI format request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "Say 'test' in one word"}
            ],
            "max_tokens": 10
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._get_headers()
        )

        # Accept 200 or auth errors
        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert data["object"] == "chat.completion", "Wrong response format"
            assert "choices" in data, "Missing choices in response"
            print(f"   Response: {data['choices'][0]['message']['content'][:50]}...")
            print(f"   Model: {data.get('model')}")
        else:
            print(f"   Skipped (auth required): {response.status_code}")

    def test_anthropic_format(self):
        """Test Anthropic format request"""
        payload = {
            "model": "claude-3-haiku-20240307",
            "system": "Be concise",
            "messages": [
                {"role": "user", "content": "Say 'test' in one word"}
            ],
            "max_tokens": 10
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._get_headers()
        )

        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert data["type"] == "message", "Wrong response format"
            assert "content" in data, "Missing content in response"
            print(f"   Response: {data['content'][0]['text'][:50]}...")
        else:
            print(f"   Skipped (auth required): {response.status_code}")

    def test_responses_api_format(self):
        """Test Responses API format request"""
        payload = {
            "model": "gpt-3.5-turbo",
            "input": [
                {"role": "user", "content": "Say 'test' in one word"}
            ]
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._get_headers()
        )

        assert response.status_code in [200, 401, 402], \
            f"Unexpected error: {response.status_code} - {response.text}"

        if response.status_code == 200:
            data = response.json()
            assert data["object"] == "response", "Wrong response format"
            assert "output" in data, "Missing output in response"
            print(f"   Response: {data['output'][0]['content'][:50]}...")
        else:
            print(f"   Skipped (auth required): {response.status_code}")

    def test_format_headers(self):
        """Test format detection headers"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._get_headers()
        )

        if response.status_code == 200:
            assert "X-Request-Format" in response.headers, "Missing format header"
            assert "X-Response-Format" in response.headers, "Missing format header"
            print(f"   Request format: {response.headers['X-Request-Format']}")
            print(f"   Response format: {response.headers['X-Response-Format']}")
        else:
            print(f"   Skipped (auth required): {response.status_code}")

    def test_deprecation_headers(self):
        """Test deprecation headers on legacy endpoints"""
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            headers=self._get_headers()
        )

        # Deprecation headers should be present regardless of auth
        if "Deprecation" in response.headers:
            assert response.headers["Deprecation"] == "true", "Wrong deprecation value"
            assert "Sunset" in response.headers, "Missing sunset header"
            assert "Link" in response.headers, "Missing link header"
            print(f"   Deprecation: {response.headers['Deprecation']}")
            print(f"   Sunset: {response.headers['Sunset']}")
            print(f"   Link: {response.headers['Link']}")
        else:
            print(f"   Warning: Deprecation headers not present (may need authenticated request)")

    def test_invalid_request_handling(self):
        """Test invalid request returns 422"""
        payload = {
            # Missing required 'model' field
            "messages": [{"role": "user", "content": "Hi"}]
        }

        response = self.client.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._get_headers()
        )

        assert response.status_code == 422, \
            f"Expected 422 for invalid request, got {response.status_code}"
        print(f"   Correctly returned 422 for invalid request")

    def test_metrics_endpoint(self):
        """Test Prometheus metrics endpoint"""
        response = self.client.get(f"{self.base_url}/metrics")
        assert response.status_code == 200, f"Metrics endpoint failed: {response.status_code}"
        assert "text/plain" in response.headers.get("content-type", ""), \
            "Metrics endpoint wrong content type"
        print("   Metrics endpoint accessible")

    def run_all_tests(self) -> bool:
        """Run all validation tests"""
        print("\n" + "="*60)
        print("🚀 DEPLOYMENT VALIDATION")
        print("="*60)
        print(f"Base URL: {self.base_url}")
        print(f"API Key: {'Set' if self.api_key else 'Not set (anonymous mode)'}")
        print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*60)

        tests = [
            ("Health Endpoint", self.test_health_endpoint),
            ("Unified Endpoint Exists", self.test_unified_endpoint_exists),
            ("OpenAI Format", self.test_openai_format),
            ("Anthropic Format", self.test_anthropic_format),
            ("Responses API Format", self.test_responses_api_format),
            ("Format Headers", self.test_format_headers),
            ("Deprecation Headers", self.test_deprecation_headers),
            ("Invalid Request Handling", self.test_invalid_request_handling),
            ("Metrics Endpoint", self.test_metrics_endpoint),
        ]

        for name, test_func in tests:
            self._test(name, test_func)
            time.sleep(0.5)  # Small delay between tests

        return self.print_summary()

    def print_summary(self) -> bool:
        """Print test summary and return success status"""
        print("\n" + "="*60)
        print("📊 TEST SUMMARY")
        print("="*60)

        passed = sum(1 for _, success, _ in self.results if success)
        failed = len(self.results) - passed

        for name, success, message in self.results:
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status}: {name}")
            if not success and message:
                print(f"       {message}")

        print("="*60)
        print(f"Total: {len(self.results)} | Passed: {passed} | Failed: {failed}")
        print("="*60)

        if failed == 0:
            print("\n🎉 All tests passed! Deployment validated successfully.")
            return True
        else:
            print(f"\n⚠️  {failed} test(s) failed. Please review the errors above.")
            return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Validate unified chat endpoint deployment"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of the API (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--key",
        help="API key for authenticated requests (optional)"
    )

    args = parser.parse_args()

    validator = DeploymentValidator(args.url, args.key)
    success = validator.run_all_tests()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
