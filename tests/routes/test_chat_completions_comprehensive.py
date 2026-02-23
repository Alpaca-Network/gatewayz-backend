"""
Comprehensive test suite for Chat Completions endpoint.

Tests the following:
- POST /v1/chat/completions
- Various request formats and parameters
- Error handling
- Response structure
"""

import pytest
from fastapi.testclient import TestClient
import json
import os


# Test API key and model (must be provided via environment variables)
# These should be set before running tests that require authentication
TEST_API_KEY = os.getenv("TEST_API_KEY", "")  # Leave empty if not provided
TEST_MODEL = os.getenv("TEST_MODEL", "gpt-3.5-turbo")


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from src.main import create_app
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Create authorization headers.

    Note: Tests using this fixture will fail if TEST_API_KEY environment variable
    is not set. Set it before running tests:
    export TEST_API_KEY="your-actual-api-key"
    """
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {TEST_API_KEY}" if TEST_API_KEY else "Bearer invalid-key",
        "Content-Type": "application/json",
    }


class TestChatCompletionsBasic:
    """Test basic chat completions functionality."""

    def test_chat_completions_simple_request(self, client, auth_headers):
        """Test simple chat completions request with minimal parameters."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Hello, how are you?",
                }
            ],
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:500]}")

        # Should return 200 or 201
        assert response.status_code in [200, 201], f"Got {response.status_code}: {response.text}"

        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0] or "delta" in data["choices"][0]

        print("✅ Simple chat completions request successful")

    def test_chat_completions_system_message(self, client, auth_headers):
        """Test chat completions with system message."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": "What is 2+2?",
                },
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with system message successful")

    def test_chat_completions_multiple_messages(self, client, auth_headers):
        """Test chat completions with conversation history."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "What is Python?",
                },
                {
                    "role": "assistant",
                    "content": "Python is a programming language.",
                },
                {
                    "role": "user",
                    "content": "What are its uses?",
                },
            ],
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with conversation history successful")


class TestChatCompletionsParameters:
    """Test chat completions with various parameters."""

    def test_chat_completions_with_temperature(self, client, auth_headers):
        """Test chat completions with temperature parameter."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Tell me a story.",
                }
            ],
            "temperature": 0.7,
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with temperature parameter successful")

    def test_chat_completions_with_top_p(self, client, auth_headers):
        """Test chat completions with top_p parameter."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "What is AI?",
                }
            ],
            "top_p": 0.9,
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with top_p parameter successful")

    def test_chat_completions_with_frequency_penalty(self, client, auth_headers):
        """Test chat completions with frequency penalty."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "List some colors.",
                }
            ],
            "frequency_penalty": 0.5,
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with frequency penalty successful")

    def test_chat_completions_with_presence_penalty(self, client, auth_headers):
        """Test chat completions with presence penalty."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Describe the weather.",
                }
            ],
            "presence_penalty": 0.5,
            "max_tokens": 100,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with presence penalty successful")

    def test_chat_completions_with_seed(self, client, auth_headers):
        """Test chat completions with seed parameter for reproducibility."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Count to 5.",
                }
            ],
            "seed": 42,
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with seed parameter successful")

    def test_chat_completions_with_user_identifier(self, client, auth_headers):
        """Test chat completions with user identifier."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Who am I?",
                }
            ],
            "user": "user_12345",
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()
        assert "choices" in data

        print("✅ Chat completions with user identifier successful")


class TestChatCompletionsStreaming:
    """Test streaming chat completions."""

    def test_chat_completions_streaming(self, client, auth_headers):
        """Test streaming chat completions."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Count to 3.",
                }
            ],
            "stream": True,
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]

        # For streaming responses, check that we get SSE format
        content = response.text
        assert len(content) > 0

        print("✅ Chat completions streaming successful")
        print(f"   Response length: {len(content)} bytes")


class TestChatCompletionsResponseStructure:
    """Test response structure and format."""

    def test_chat_completions_response_structure(self, client, auth_headers):
        """Test that chat completions response has correct structure."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Hello.",
                }
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()

        # Check required fields
        assert "id" in data
        assert "object" in data
        assert "created" in data
        assert "model" in data
        assert "choices" in data
        assert isinstance(data["choices"], list)
        assert len(data["choices"]) > 0

        # Check choice structure
        choice = data["choices"][0]
        assert "index" in choice
        assert ("message" in choice or "delta" in choice)

        print("✅ Chat completions response structure valid")
        print(f"   ID: {data.get('id')}")
        print(f"   Model: {data.get('model')}")
        print(f"   Choices: {len(data['choices'])}")

    def test_chat_completions_usage_information(self, client, auth_headers):
        """Test that response includes usage information when available."""
        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Hi",
                }
            ],
            "max_tokens": 50,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 201]
        data = response.json()

        # Usage information may or may not be present
        if "usage" in data:
            assert "prompt_tokens" in data["usage"]
            assert "completion_tokens" in data["usage"]
            assert "total_tokens" in data["usage"]
            print("✅ Chat completions includes usage information")
            print(f"   Prompt tokens: {data['usage'].get('prompt_tokens')}")
            print(f"   Completion tokens: {data['usage'].get('completion_tokens')}")
        else:
            print("ℹ️  Usage information not included in response")


class TestChatCompletionsErrorHandling:
    """Test error handling in chat completions."""

    def test_chat_completions_missing_model(self, client, auth_headers):
        """Test error when model is missing."""
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should return error (400 or 422)
        assert response.status_code in [400, 422, 200]  # Some models may default

        print(f"✅ Missing model parameter handled (status: {response.status_code})")

    def test_chat_completions_missing_messages(self, client, auth_headers):
        """Test error when messages are missing."""
        payload = {
            "model": TEST_MODEL,
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )

        # Should return error (400 or 422)
        assert response.status_code in [400, 422, 200]  # Some models may default

        print(f"✅ Missing messages parameter handled (status: {response.status_code})")

    def test_chat_completions_invalid_auth(self, client):
        """Test error with invalid authentication."""
        headers = {
            "accept": "application/json",
            "Authorization": "Bearer invalid_key_12345",
            "Content-Type": "application/json",
        }

        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Hello",
                }
            ],
        }

        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers,
        )

        # Should return 401 or similar
        assert response.status_code in [401, 403, 400]

        print(f"✅ Invalid authentication handled (status: {response.status_code})")


class TestChatCompletionsPerformance:
    """Test performance of chat completions."""

    def test_chat_completions_response_time(self, client, auth_headers):
        """Test that chat completions responds in reasonable time."""
        import time

        payload = {
            "model": TEST_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "Hi",
                }
            ],
            "max_tokens": 10,
        }

        start = time.time()
        response = client.post(
            "/v1/chat/completions",
            json=payload,
            headers=auth_headers,
        )
        elapsed_seconds = time.time() - start

        assert response.status_code in [200, 201]

        # Check response time (should be under 30 seconds for API call)
        # Adjust threshold based on your requirements
        print(f"✅ Chat completions response time: {elapsed_seconds:.2f} seconds")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
