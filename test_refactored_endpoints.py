#!/usr/bin/env python3
"""
Comprehensive API test for refactored chat endpoints.

This script tests all refactored endpoints to ensure the unified handler
integration works correctly:
- /v1/chat/completions (non-streaming and streaming)
- /v1/messages (Anthropic API)
- /v1/responses (non-streaming)
- /api/chat/ai-sdk (non-streaming and streaming)

Usage:
    python test_refactored_endpoints.py

Environment variables:
    GATEWAYZ_API_KEY: Your Gatewayz API key (required)
    GATEWAYZ_API_URL: API base URL (default: https://gatewayz-staging.up.railway.app)
"""

import os
import sys
import json
import time
import requests
from typing import Dict, Any, Optional

# ANSI color codes for pretty output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'


class APITester:
    def __init__(self, api_key: str, base_url: str = "https://gatewayz-staging.up.railway.app"):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.passed = 0
        self.failed = 0
        self.total = 0

    def print_header(self, text: str):
        """Print a section header."""
        print(f"\n{BOLD}{BLUE}{'=' * 80}{RESET}")
        print(f"{BOLD}{BLUE}{text}{RESET}")
        print(f"{BOLD}{BLUE}{'=' * 80}{RESET}\n")

    def print_test(self, test_name: str):
        """Print test name."""
        self.total += 1
        print(f"{BOLD}[{self.total}] {test_name}...{RESET}", end=' ')

    def print_success(self, message: str = ""):
        """Print success message."""
        self.passed += 1
        print(f"{GREEN}✓ PASSED{RESET}", end='')
        if message:
            print(f" ({message})")
        else:
            print()

    def print_failure(self, message: str):
        """Print failure message."""
        self.failed += 1
        print(f"{RED}✗ FAILED{RESET}")
        print(f"{RED}   Error: {message}{RESET}")

    def print_warning(self, message: str):
        """Print warning message."""
        print(f"{YELLOW}   Warning: {message}{RESET}")

    def print_summary(self):
        """Print test summary."""
        print(f"\n{BOLD}{BLUE}{'=' * 80}{RESET}")
        print(f"{BOLD}Test Summary{RESET}")
        print(f"{BOLD}{BLUE}{'=' * 80}{RESET}")
        print(f"Total tests: {self.total}")
        print(f"{GREEN}Passed: {self.passed}{RESET}")
        print(f"{RED}Failed: {self.failed}{RESET}")

        if self.failed == 0:
            print(f"\n{GREEN}{BOLD}🎉 All tests passed!{RESET}")
        else:
            print(f"\n{RED}{BOLD}❌ Some tests failed{RESET}")

        print(f"{BOLD}{BLUE}{'=' * 80}{RESET}\n")

        return self.failed == 0

    def test_chat_completions_non_streaming(self):
        """Test /v1/chat/completions endpoint (non-streaming)."""
        self.print_test("Chat Completions (non-streaming)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "Say 'test passed' and nothing else."}
                ],
                "max_tokens": 10,
                "stream": False
            }

            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            data = response.json()

            # Validate response structure
            assert "choices" in data, "Missing 'choices' in response"
            assert len(data["choices"]) > 0, "No choices in response"
            assert "message" in data["choices"][0], "Missing 'message' in choice"
            assert "content" in data["choices"][0]["message"], "Missing 'content' in message"
            assert "usage" in data, "Missing 'usage' in response"

            content = data["choices"][0]["message"]["content"]
            self.print_success(f"content={content[:50]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_chat_completions_streaming(self):
        """Test /v1/chat/completions endpoint (streaming)."""
        self.print_test("Chat Completions (streaming)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "Count to 3."}
                ],
                "max_tokens": 20,
                "stream": True
            }

            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=30,
                stream=True
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            chunks_received = 0
            content_parts = []

            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith('data: '):
                        data_str = line_str[6:]
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            chunk = json.loads(data_str)
                            chunks_received += 1

                            # Extract content delta
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta:
                                    content_parts.append(delta["content"])
                        except json.JSONDecodeError:
                            pass

            assert chunks_received > 0, f"No chunks received"

            full_content = "".join(content_parts)
            self.print_success(f"{chunks_received} chunks, content={full_content[:30]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_messages_endpoint(self):
        """Test /v1/messages endpoint (Anthropic API)."""
        self.print_test("Messages API (Anthropic format)")

        try:
            payload = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 20,
                "messages": [
                    {"role": "user", "content": "Say 'anthropic test passed' briefly."}
                ]
            }

            response = requests.post(
                f"{self.base_url}/v1/messages",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            data = response.json()

            # Validate Anthropic response structure
            assert "content" in data, "Missing 'content' in response"
            assert len(data["content"]) > 0, "No content in response"
            assert "type" in data["content"][0], "Missing 'type' in content block"
            assert data["content"][0]["type"] == "text", "Expected text content type"
            assert "text" in data["content"][0], "Missing 'text' in content block"
            assert "usage" in data, "Missing 'usage' in response"

            text = data["content"][0]["text"]
            self.print_success(f"text={text[:50]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_responses_endpoint(self):
        """Test /v1/responses endpoint (OpenAI Responses API)."""
        self.print_test("Responses API (non-streaming)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "input": [
                    {"role": "user", "content": "Say 'responses test passed'."}
                ],
                "max_tokens": 15
            }

            response = requests.post(
                f"{self.base_url}/v1/responses",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            data = response.json()

            # Validate Responses API structure
            assert "output" in data, "Missing 'output' in response"
            assert len(data["output"]) > 0, "No output in response"
            assert "content" in data["output"][0], "Missing 'content' in output item"

            # Get text content
            content_items = data["output"][0]["content"]
            if isinstance(content_items, list) and len(content_items) > 0:
                text = content_items[0].get("text", "")
            elif isinstance(content_items, str):
                text = content_items
            else:
                text = str(content_items)

            self.print_success(f"output={text[:50]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_ai_sdk_endpoint(self):
        """Test /api/chat/ai-sdk endpoint (AI SDK format)."""
        self.print_test("AI SDK endpoint (non-streaming)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "Say 'AI SDK test passed'."}
                ],
                "max_tokens": 15,
                "stream": False
            }

            response = requests.post(
                f"{self.base_url}/api/chat/ai-sdk",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            data = response.json()

            # Validate AI SDK response structure (similar to OpenAI)
            assert "choices" in data, "Missing 'choices' in response"
            assert len(data["choices"]) > 0, "No choices in response"
            assert "message" in data["choices"][0], "Missing 'message' in choice"
            assert "content" in data["choices"][0]["message"], "Missing 'content' in message"

            content = data["choices"][0]["message"]["content"]
            self.print_success(f"content={content[:50]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_ai_sdk_streaming(self):
        """Test /api/chat/ai-sdk endpoint (streaming)."""
        self.print_test("AI SDK endpoint (streaming)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "Count to 2."}
                ],
                "max_tokens": 15,
                "stream": True
            }

            response = requests.post(
                f"{self.base_url}/api/chat/ai-sdk",
                headers=self.headers,
                json=payload,
                timeout=30,
                stream=True
            )

            if response.status_code != 200:
                self.print_failure(f"Status {response.status_code}: {response.text[:200]}")
                return False

            chunks_received = 0
            content_parts = []

            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith('data: '):
                        data_str = line_str[6:]
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            chunk = json.loads(data_str)
                            chunks_received += 1

                            # Extract content delta
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta:
                                    content_parts.append(delta["content"])
                        except json.JSONDecodeError:
                            pass

            assert chunks_received > 0, f"No chunks received"

            full_content = "".join(content_parts)
            self.print_success(f"{chunks_received} chunks, content={full_content[:30]}")
            return True

        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_error_handling(self):
        """Test error handling with invalid requests."""
        self.print_test("Error handling (invalid model)")

        try:
            payload = {
                "model": "nonexistent-model-12345",
                "messages": [
                    {"role": "user", "content": "Test"}
                ]
            }

            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            # Should return error status code (400s or 500s)
            if response.status_code < 400:
                self.print_failure(f"Expected error status, got {response.status_code}")
                return False

            # Validate detailed error structure
            data = response.json()
            assert "error" in data, "Missing 'error' key in error response"
            error = data["error"]

            # Required fields
            assert "type" in error, "Missing 'type' in error"
            assert "message" in error, "Missing 'message' in error"
            assert "code" in error, "Missing 'code' in error"
            assert "status" in error, "Missing 'status' in error"
            assert "request_id" in error, "Missing 'request_id' in error"
            assert "timestamp" in error, "Missing 'timestamp' in error"

            # Should have suggestions
            assert "suggestions" in error, "Missing 'suggestions' in error"
            if error["suggestions"]:
                assert isinstance(error["suggestions"], list), "Suggestions should be a list"
                assert len(error["suggestions"]) > 0, "Suggestions should not be empty"

            # Check X-Request-ID header
            assert "X-Request-ID" in response.headers, "Missing X-Request-ID header"

            self.print_success(f"Status {response.status_code}, detailed error with request_id={error['request_id'][:12]}...")
            return True

        except AssertionError as e:
            self.print_failure(str(e))
            return False
        except Exception as e:
            self.print_failure(str(e))
            return False

    def test_error_invalid_api_key(self):
        """Test error handling with invalid API key."""
        self.print_test("Error handling (invalid API key)")

        try:
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": "Test"}
                ]
            }

            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": "Bearer invalid_key_12345", "Content-Type": "application/json"},
                json=payload,
                timeout=30
            )

            # Should return 401
            assert response.status_code == 401, f"Expected 401, got {response.status_code}"

            # Validate detailed error structure
            data = response.json()
            assert "error" in data, "Missing 'error' key"
            error = data["error"]

            assert error["type"] == "invalid_api_key", f"Expected type 'invalid_api_key', got '{error['type']}'"
            assert error["code"] == "INVALID_API_KEY", f"Expected code 'INVALID_API_KEY', got '{error['code']}'"
            assert error["status"] == 401, f"Expected status 401, got {error['status']}"
            assert error["request_id"] is not None, "Missing request_id"
            assert error["suggestions"] is not None, "Missing suggestions"

            self.print_success(f"Detailed error with type={error['type']}")
            return True

        except AssertionError as e:
            self.print_failure(str(e))
            return False
        except Exception as e:
            self.print_failure(str(e))
            return False

    def run_all_tests(self):
        """Run all API tests."""
        self.print_header("🚀 Testing Refactored Chat Endpoints")

        print(f"API Base URL: {BOLD}{self.base_url}{RESET}")
        print(f"API Key: {BOLD}{self.api_key[:8]}...{RESET}\n")

        # Test each endpoint
        self.print_header("1️⃣  Chat Completions Endpoint Tests")
        self.test_chat_completions_non_streaming()
        self.test_chat_completions_streaming()

        self.print_header("2️⃣  Messages API (Anthropic) Tests")
        self.test_messages_endpoint()

        self.print_header("3️⃣  Responses API Tests")
        self.test_responses_endpoint()

        self.print_header("4️⃣  AI SDK Endpoint Tests")
        self.test_ai_sdk_endpoint()
        self.test_ai_sdk_streaming()

        self.print_header("5️⃣  Error Handling Tests")
        self.test_error_handling()
        self.test_error_invalid_api_key()

        # Print summary
        return self.print_summary()


def main():
    """Main test runner."""
    # Get API key from environment
    api_key = os.getenv("GATEWAYZ_API_KEY")
    if not api_key:
        print(f"{RED}Error: GATEWAYZ_API_KEY environment variable not set{RESET}")
        print(f"\nUsage: GATEWAYZ_API_KEY=your-key python {sys.argv[0]}")
        sys.exit(1)

    # Get API URL (default to staging)
    base_url = os.getenv("GATEWAYZ_API_URL", "https://gatewayz-staging.up.railway.app")

    # Run tests
    tester = APITester(api_key, base_url)
    success = tester.run_all_tests()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
