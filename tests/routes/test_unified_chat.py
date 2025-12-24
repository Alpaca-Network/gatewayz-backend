"""
Integration tests for the unified chat endpoint.

Tests the /v1/chat endpoint with:
- Different request formats (OpenAI, Anthropic, Responses API)
- Format auto-detection
- Response format matching
- Error handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_unified_handler():
    """Mock the unified chat handler"""
    with patch("src.routes.unified_chat.chat_handler") as mock:
        # Mock successful response
        mock.process_chat = AsyncMock(return_value={
            "id": "test-response-123",
            "created": 1234567890,
            "model": "gpt-4",
            "content": "Hello! How can I help you today?",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "total_tokens": 18
            },
            "gateway_usage": {
                "cost_usd": 0.001,
                "provider": "openrouter"
            }
        })
        yield mock


@pytest.fixture
def mock_auth():
    """Mock authentication dependencies"""
    with patch("src.routes.unified_chat.validate_user_and_auth") as mock_validate, \
         patch("src.routes.unified_chat.validate_trial") as mock_trial, \
         patch("src.routes.unified_chat.check_plan_limits") as mock_limits:

        # Mock user validation
        mock_validate.return_value = (
            {"id": 1, "credits": 100.0, "environment_tag": "live"},
            False  # is_anonymous
        )

        # Mock trial validation
        mock_trial.return_value = {"is_valid": True, "is_trial": False}

        # Mock plan limits check
        mock_limits.return_value = None

        yield {
            "validate": mock_validate,
            "trial": mock_trial,
            "limits": mock_limits
        }


class TestUnifiedChatEndpoint:
    """Test the unified chat endpoint"""

    def test_openai_format_request(self, mock_unified_handler, mock_auth):
        """Test request in OpenAI format"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        request_data = {
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "Hello!"}
            ],
            "temperature": 0.7
        }

        response = client.post(
            "/v1/chat",
            json=request_data,
            headers={"Authorization": "Bearer test-key"}
        )

        assert response.status_code == 200
        data = response.json()

        # Should return OpenAI format
        assert data["object"] == "chat.completion"
        assert "choices" in data
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert data["choices"][0]["message"]["role"] == "assistant"

        # Check response format header
        assert response.headers.get("X-Request-Format") == "openai"
        assert response.headers.get("X-Response-Format") == "openai"

    def test_anthropic_format_request(self, mock_unified_handler, mock_auth):
        """Test request in Anthropic format (with system prompt)"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        request_data = {
            "model": "claude-3-opus",
            "system": "You are a helpful assistant",
            "messages": [
                {"role": "user", "content": "Hello!"}
            ],
            "max_tokens": 1024
        }

        response = client.post(
            "/v1/chat",
            json=request_data,
            headers={"Authorization": "Bearer test-key"}
        )

        assert response.status_code == 200
        data = response.json()

        # Should return Anthropic format
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert "content" in data
        assert len(data["content"]) == 1
        assert data["content"][0]["type"] == "text"
        assert data["content"][0]["text"] == "Hello! How can I help you today?"

        # Check response format header
        assert response.headers.get("X-Request-Format") == "anthropic"
        assert response.headers.get("X-Response-Format") == "anthropic"

    def test_responses_api_format_request(self, mock_unified_handler, mock_auth):
        """Test request in Responses API format (with input field)"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        request_data = {
            "model": "gpt-4",
            "input": [
                {"role": "user", "content": "Hello!"}
            ]
        }

        response = client.post(
            "/v1/chat",
            json=request_data,
            headers={"Authorization": "Bearer test-key"}
        )

        assert response.status_code == 200
        data = response.json()

        # Should return Responses API format
        assert data["object"] == "response"
        assert "output" in data
        assert len(data["output"]) == 1
        assert data["output"][0]["role"] == "assistant"
        assert data["output"][0]["content"] == "Hello! How can I help you today?"

        # Check response format header
        assert response.headers.get("X-Request-Format") == "responses"
        assert response.headers.get("X-Response-Format") == "responses"

    def test_explicit_format_override(self, mock_unified_handler, mock_auth):
        """Test explicit format field overrides auto-detection"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        # Send OpenAI-style request but explicitly request Anthropic format
        request_data = {
            "format": "anthropic",
            "model": "gpt-4",
            "messages": [
                {"role": "user", "content": "Hello!"}
            ]
        }

        response = client.post(
            "/v1/chat",
            json=request_data,
            headers={"Authorization": "Bearer test-key"}
        )

        assert response.status_code == 200
        data = response.json()

        # Should return Anthropic format due to explicit override
        assert data["type"] == "message"
        assert response.headers.get("X-Response-Format") == "anthropic"

    def test_missing_api_key_anonymous_request(self, mock_unified_handler):
        """Test anonymous request without API key"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        with patch("src.routes.unified_chat.validate_user_and_auth") as mock_validate:
            # Mock anonymous user
            mock_validate.return_value = (
                {"id": None},
                True  # is_anonymous
            )

            request_data = {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ]
            }

            response = client.post("/v1/chat", json=request_data)

            assert response.status_code == 200
            # Anonymous requests should work
            mock_validate.assert_called_once()

    def test_invalid_request_format(self):
        """Test request with invalid format"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        # Missing required 'model' field
        request_data = {
            "messages": [
                {"role": "user", "content": "Hello!"}
            ]
        }

        response = client.post(
            "/v1/chat",
            json=request_data,
            headers={"Authorization": "Bearer test-key"}
        )

        assert response.status_code == 422
        assert "Invalid request format" in response.json()["detail"]

    def test_insufficient_credits(self, mock_unified_handler):
        """Test request with insufficient credits"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        with patch("src.routes.unified_chat.validate_user_and_auth") as mock_validate, \
             patch("src.routes.unified_chat.validate_trial") as mock_trial:

            # Mock user with zero credits
            mock_validate.return_value = (
                {"id": 1, "credits": 0.0, "environment_tag": "live"},
                False  # is_anonymous
            )

            # Not a trial account
            mock_trial.return_value = {"is_valid": True, "is_trial": False}

            request_data = {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ]
            }

            response = client.post(
                "/v1/chat",
                json=request_data,
                headers={"Authorization": "Bearer test-key"}
            )

            assert response.status_code == 402
            assert "Insufficient credits" in response.json()["detail"]

    def test_streaming_request(self, mock_auth):
        """Test streaming request"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        async def mock_streaming_generator():
            """Mock streaming response"""
            yield b"data: {\"content\": \"Hello\"}\n\n"
            yield b"data: {\"content\": \" there\"}\n\n"
            yield b"data: [DONE]\n\n"

        with patch("src.routes.unified_chat.chat_handler") as mock_handler:
            mock_handler.process_chat = AsyncMock(return_value=mock_streaming_generator())

            request_data = {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ],
                "stream": True
            }

            response = client.post(
                "/v1/chat",
                json=request_data,
                headers={"Authorization": "Bearer test-key"}
            )

            assert response.status_code == 200
            assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"
            assert response.headers.get("X-Accel-Buffering") == "no"
            assert response.headers.get("Cache-Control") == "no-cache, no-transform"

    def test_tool_calls_in_response(self, mock_auth):
        """Test response with tool calls"""
        from src.main import create_app
        app = create_app()
        client = TestClient(app)

        with patch("src.routes.unified_chat.chat_handler") as mock_handler:
            # Mock response with tool calls
            mock_handler.process_chat = AsyncMock(return_value={
                "id": "test-123",
                "created": 1234567890,
                "model": "gpt-4",
                "content": "",
                "finish_reason": "tool_calls",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "San Francisco"}'
                    }
                }],
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70
                }
            })

            request_data = {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "What's the weather in SF?"}
                ]
            }

            response = client.post(
                "/v1/chat",
                json=request_data,
                headers={"Authorization": "Bearer test-key"}
            )

            assert response.status_code == 200
            data = response.json()

            # Check tool calls in OpenAI format
            assert "tool_calls" in data["choices"][0]["message"]
            assert len(data["choices"][0]["message"]["tool_calls"]) == 1
            assert data["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"


class TestFormatDetection:
    """Test format auto-detection logic"""

    def test_detect_openai_format(self):
        """Test detection of OpenAI format (default)"""
        from src.services.format_detection import detect_request_format

        data = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}]
        }

        assert detect_request_format(data) == "openai"

    def test_detect_anthropic_format(self):
        """Test detection of Anthropic format (system field)"""
        from src.services.format_detection import detect_request_format

        data = {
            "model": "claude-3-opus",
            "system": "You are helpful",
            "messages": [{"role": "user", "content": "Hello"}]
        }

        assert detect_request_format(data) == "anthropic"

    def test_detect_responses_format(self):
        """Test detection of Responses API format (input field)"""
        from src.services.format_detection import detect_request_format

        data = {
            "model": "gpt-4",
            "input": [{"role": "user", "content": "Hello"}]
        }

        assert detect_request_format(data) == "responses"

    def test_explicit_format_priority(self):
        """Test explicit format field has priority"""
        from src.services.format_detection import detect_request_format

        data = {
            "format": "anthropic",  # Explicit
            "model": "gpt-4",
            "input": [{"role": "user", "content": "Hello"}]  # Would be detected as responses
        }

        assert detect_request_format(data) == "anthropic"
