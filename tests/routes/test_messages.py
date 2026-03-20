#!/usr/bin/env python3
"""
Comprehensive tests for Anthropic Messages API endpoint (Claude API)

Tests cover:
- Basic Claude API message completion
- Authentication and authorization
- Credit validation and deduction
- Request transformation (Anthropic ↔ OpenAI format)
- Response transformation
- Provider failover
- Rate limiting
- Trial validation
- Plan enforcement
- Chat history integration
- Error handling
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.schemas import AnthropicMessage, MessagesRequest
from src.services.anthropic_transformer import (
    extract_text_from_content,
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def mock_user():
    """Sample user with sufficient credits"""
    return {
        "id": 1,
        "email": "test@example.com",
        "credits": 100.0,
        "api_key": "test_api_key_12345",
        "environment_tag": "live",
    }


@pytest.fixture
def mock_user_no_credits():
    """Sample user with zero credits"""
    return {
        "id": 2,
        "email": "broke@example.com",
        "credits": 0.0,
        "api_key": "broke_api_key_12345",
        "environment_tag": "live",
    }


@pytest.fixture
def mock_openai_response():
    """Sample OpenAI-style response"""
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "claude-sonnet-4-5-20250929",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
    }


@pytest.fixture
def valid_messages_request():
    """Valid Anthropic messages request"""
    return {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello, Claude!"}],
    }


# ============================================================
# TEST CLASS: Anthropic Transformer
# ============================================================


class TestAnthropicTransformer:
    """Test transformation between Anthropic and OpenAI formats"""

    def test_transform_anthropic_to_openai_basic(self):
        """Test basic message transformation"""
        messages = [{"role": "user", "content": "Hello"}]

        openai_messages, params = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        assert len(openai_messages) == 1
        assert openai_messages[0]["role"] == "user"
        assert openai_messages[0]["content"] == "Hello"
        assert params["max_tokens"] == 100

    def test_transform_anthropic_to_openai_with_system(self):
        """Test transformation with system message"""
        messages = [{"role": "user", "content": "Hello"}]
        system = "You are a helpful assistant."

        openai_messages, params = transform_anthropic_to_openai(
            messages=messages, system=system, max_tokens=100
        )

        assert len(openai_messages) == 2
        assert openai_messages[0]["role"] == "system"
        assert openai_messages[0]["content"] == system
        assert openai_messages[1]["role"] == "user"

    def test_transform_anthropic_to_openai_with_params(self):
        """Test transformation with all parameters"""
        messages = [{"role": "user", "content": "Hello"}]

        openai_messages, params = transform_anthropic_to_openai(
            messages=messages,
            max_tokens=100,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            stop_sequences=["STOP", "END"],
        )

        assert params["max_tokens"] == 100
        assert params["temperature"] == 0.7
        assert params["top_p"] == 0.9
        assert params["stop"] == ["STOP", "END"]
        # top_k is Anthropic-specific and should be logged but not in params
        assert "top_k" not in params

    def test_transform_anthropic_to_openai_content_blocks(self):
        """Test transformation with content blocks"""
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}],
            }
        ]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        assert len(openai_messages) == 1
        # Multiple blocks should be combined
        assert isinstance(openai_messages[0]["content"], list)

    def test_transform_anthropic_to_openai_single_text_block(self):
        """Test transformation with single text block (should unwrap)"""
        messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        # Single text block should be unwrapped to string
        assert openai_messages[0]["content"] == "Hello"

    def test_transform_anthropic_to_openai_image_blocks(self):
        """Test transformation with image content blocks"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "base64_encoded_data",
                        },
                    },
                ],
            }
        ]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        content = openai_messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "data:image/jpeg;base64," in content[1]["image_url"]["url"]

    def test_transform_openai_to_anthropic_basic(self):
        """Test OpenAI to Anthropic response transformation"""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        anthropic_response = transform_openai_to_anthropic(
            openai_response, model="claude-sonnet-4-5-20250929"
        )

        assert anthropic_response["id"] == "chatcmpl-123"
        assert anthropic_response["type"] == "message"
        assert anthropic_response["role"] == "assistant"
        assert anthropic_response["content"][0]["type"] == "text"
        assert anthropic_response["content"][0]["text"] == "Hello!"
        assert anthropic_response["model"] == "claude-sonnet-4-5-20250929"
        assert anthropic_response["stop_reason"] == "end_turn"
        assert anthropic_response["usage"]["input_tokens"] == 10
        assert anthropic_response["usage"]["output_tokens"] == 5

    def test_transform_openai_to_anthropic_finish_reasons(self):
        """Test finish reason mapping"""
        test_cases = [
            ("stop", "end_turn"),
            ("length", "max_tokens"),
            ("content_filter", "stop_sequence"),
            ("tool_calls", "tool_use"),
            ("function_call", "tool_use"),
            ("unknown", "end_turn"),
        ]

        for openai_reason, expected_anthropic in test_cases:
            openai_response = {
                "id": "test",
                "choices": [{"message": {"content": "test"}, "finish_reason": openai_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

            result = transform_openai_to_anthropic(openai_response, "claude")
            assert result["stop_reason"] == expected_anthropic

    def test_transform_openai_to_anthropic_tool_calls(self):
        """Test OpenAI to Anthropic transformation with tool_calls"""
        # Test case 1: tool_calls with null content (typical OpenAI response)
        openai_response_with_tools = {
            "id": "chatcmpl-456",
            "choices": [
                {
                    "message": {
                        "content": None,  # Typically None when tool_calls are present
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "San Francisco"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15},
        }

        anthropic_response = transform_openai_to_anthropic(
            openai_response_with_tools, model="claude-sonnet-4-5-20250929"
        )

        # Should have tool_use blocks, not empty text blocks
        assert len(anthropic_response["content"]) == 1
        assert anthropic_response["content"][0]["type"] == "tool_use"
        assert anthropic_response["content"][0]["id"] == "call_abc123"
        assert anthropic_response["content"][0]["name"] == "get_weather"
        assert anthropic_response["content"][0]["input"] == {"location": "San Francisco"}
        assert anthropic_response["stop_reason"] == "tool_use"

        # Test case 2: tool_calls with empty string content
        openai_response_empty_content = {
            "id": "chatcmpl-789",
            "choices": [
                {
                    "message": {
                        "content": "",  # Empty string
                        "tool_calls": [
                            {
                                "id": "call_def456",
                                "type": "function",
                                "function": {"name": "calculate", "arguments": '{"x": 5, "y": 3}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

        anthropic_response2 = transform_openai_to_anthropic(
            openai_response_empty_content, model="claude-sonnet-4-5-20250929"
        )

        # Should only have tool_use block, no empty text block
        assert len(anthropic_response2["content"]) == 1
        assert anthropic_response2["content"][0]["type"] == "tool_use"
        assert anthropic_response2["content"][0]["name"] == "calculate"

        # Test case 3: tool_calls with both text content and tool_calls (rare but possible)
        openai_response_with_both = {
            "id": "chatcmpl-101",
            "choices": [
                {
                    "message": {
                        "content": "Let me check that for you.",
                        "tool_calls": [
                            {
                                "id": "call_ghi789",
                                "type": "function",
                                "function": {"name": "search", "arguments": '{"query": "test"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 12},
        }

        anthropic_response3 = transform_openai_to_anthropic(
            openai_response_with_both, model="claude-sonnet-4-5-20250929"
        )

        # Should have both tool_use and text blocks
        assert len(anthropic_response3["content"]) == 2
        # Tool_use should come first (we process it first)
        assert anthropic_response3["content"][0]["type"] == "tool_use"
        assert anthropic_response3["content"][1]["type"] == "text"
        assert anthropic_response3["content"][1]["text"] == "Let me check that for you."

    def test_extract_text_from_string_content(self):
        """Test extracting text from string content"""
        text = extract_text_from_content("Hello, world!")
        assert text == "Hello, world!"

    def test_extract_text_from_content_blocks(self):
        """Test extracting text from content blocks"""
        content = [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}]
        text = extract_text_from_content(content)
        assert text == "Hello World"

    def test_extract_text_from_mixed_blocks(self):
        """Test extracting text from mixed content blocks"""
        content = [
            {"type": "text", "text": "Look at this:"},
            {"type": "image", "url": "http://example.com/image.jpg"},
        ]
        text = extract_text_from_content(content)
        assert text == "Look at this:"

    def test_extract_text_from_empty_blocks(self):
        """Test extracting text from empty content"""
        assert extract_text_from_content([]) == "[multimodal content]"
        assert extract_text_from_content(None) == ""


# ============================================================
# TEST CLASS: Messages Endpoint - Success Cases
# ============================================================


class TestMessagesEndpointSuccess:
    """Test successful message completions"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    @patch("src.routes.messages.get_rate_limit_manager")
    @patch("src.routes.messages.make_openrouter_request_openai")
    @patch("src.routes.messages.process_openrouter_response")
    @patch("src.routes.messages.calculate_cost")
    @patch("src.routes.messages.deduct_credits")
    @patch("src.routes.messages.record_usage")
    @patch("src.routes.messages.increment_api_key_usage")
    @patch("src.routes.messages.update_rate_limit_usage")
    @patch("src.routes.messages.log_activity")
    def test_messages_endpoint_basic_success(
        self,
        mock_log_activity,
        mock_update_rate_limit,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_calculate_cost,
        mock_process_response,
        mock_make_request,
        mock_rate_limit_mgr,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user,
        mock_openai_response,
        valid_messages_request,
    ):
        """Test successful Claude API message completion"""
        # Setup mocks
        mock_get_user.return_value = mock_user
        mock_enforce_plan.return_value = {"allowed": True}
        mock_validate_trial.return_value = {"is_valid": True, "is_trial": False}

        rate_limit_result = Mock()
        rate_limit_result.allowed = True
        rate_limit_result.remaining_requests = 249
        rate_limit_result.remaining_tokens = 9900
        rate_limit_result.ratelimit_limit_requests = 250
        rate_limit_result.ratelimit_limit_tokens = 10000
        rate_limit_result.ratelimit_reset_requests = 1700000000
        rate_limit_result.ratelimit_reset_tokens = 1700000000
        rate_limit_result.burst_window_description = "100 per 60 seconds"
        rate_limit_mgr_instance = Mock()
        rate_limit_mgr_instance.check_rate_limit = AsyncMock(return_value=rate_limit_result)
        rate_limit_mgr_instance.release_concurrency = AsyncMock()
        mock_rate_limit_mgr.return_value = rate_limit_mgr_instance

        mock_make_request.return_value = mock_openai_response
        mock_process_response.return_value = mock_openai_response
        mock_calculate_cost.return_value = 0.01

        # Execute
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test_api_key_12345"},
            json=valid_messages_request,
        )

        # Verify
        assert response.status_code == 200
        data = response.json()

        # Verify Anthropic response format
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert "content" in data
        assert isinstance(data["content"], list)
        assert data["content"][0]["type"] == "text"
        assert data["content"][0]["text"] == "Hello! How can I help you today?"
        assert data["stop_reason"] == "end_turn"
        assert data["usage"]["input_tokens"] == 10
        assert data["usage"]["output_tokens"] == 12

        # Verify gateway usage metadata
        assert "gateway_usage" in data
        assert "tokens_charged" in data["gateway_usage"]
        assert "cost_usd" in data["gateway_usage"]

        # Verify credits deducted
        mock_deduct_credits.assert_called_once()
        mock_record_usage.assert_called_once()


# ============================================================
# TEST CLASS: Messages Endpoint - Authentication
# ============================================================


class TestMessagesEndpointAuth:
    """Test authentication and authorization"""

    @patch("src.routes.messages.get_user")
    def test_messages_endpoint_no_auth_header(self, mock_get_user, client, valid_messages_request):
        """Test request without Authorization header"""
        response = client.post("/v1/messages", json=valid_messages_request)

        # Should fail with 401 or 403
        assert response.status_code in [401, 403]

    @patch("src.routes.messages.get_user")
    def test_messages_endpoint_invalid_api_key(self, mock_get_user, client, valid_messages_request):
        """Test request with invalid API key"""
        mock_get_user.return_value = None

        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer invalid_key"},
            json=valid_messages_request,
        )

        assert response.status_code == 401


# ============================================================
# TEST CLASS: Messages Endpoint - Credit Validation
# ============================================================


class TestMessagesEndpointCredits:
    """Test credit validation and deduction"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    @patch("src.routes.messages.get_rate_limit_manager")
    def test_messages_endpoint_insufficient_credits(
        self,
        mock_rate_limit_mgr,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user_no_credits,
        valid_messages_request,
    ):
        """Test request with insufficient credits"""
        mock_get_user.return_value = mock_user_no_credits
        mock_enforce_plan.return_value = {"allowed": True}
        mock_validate_trial.return_value = {"is_valid": True, "is_trial": False}

        rate_limit_result = Mock()
        rate_limit_result.allowed = True
        rate_limit_result.remaining_requests = 249
        rate_limit_result.remaining_tokens = 9900
        rate_limit_result.ratelimit_limit_requests = 250
        rate_limit_result.ratelimit_limit_tokens = 10000
        rate_limit_result.ratelimit_reset_requests = 1700000000
        rate_limit_result.ratelimit_reset_tokens = 1700000000
        rate_limit_result.burst_window_description = "100 per 60 seconds"
        rate_limit_mgr_instance = Mock()
        rate_limit_mgr_instance.check_rate_limit = AsyncMock(return_value=rate_limit_result)
        mock_rate_limit_mgr.return_value = rate_limit_mgr_instance

        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer broke_api_key_12345"},
            json=valid_messages_request,
        )

        assert response.status_code == 402
        assert "insufficient credits" in response.json()["detail"].lower()


# ============================================================
# TEST CLASS: Messages Endpoint - Rate Limiting
# ============================================================


class TestMessagesEndpointRateLimiting:
    """Test rate limiting enforcement"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    @patch("src.routes.messages.get_rate_limit_manager")
    @patch("src.routes.messages.make_openrouter_request_openai")
    @patch("src.routes.messages.process_openrouter_response")
    def test_messages_endpoint_rate_limit_exceeded(
        self,
        mock_process_or,
        mock_make_or,
        mock_rate_limit_mgr,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user,
        valid_messages_request,
    ):
        """Test rate limit exceeded"""
        mock_get_user.return_value = mock_user
        mock_enforce_plan.return_value = {"allowed": True}
        mock_validate_trial.return_value = {"is_valid": True, "is_trial": False}
        mock_make_or.return_value = {"_raw": True}
        mock_process_or.return_value = {
            "content": [{"type": "text", "text": "test"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        # Rate limit exceeded
        rate_limit_result = Mock()
        rate_limit_result.allowed = False
        rate_limit_result.reason = "Too many requests"
        rate_limit_result.retry_after = 60
        rate_limit_result.remaining_requests = 0
        rate_limit_result.remaining_tokens = 0
        rate_limit_result.ratelimit_limit_requests = 250
        rate_limit_result.ratelimit_limit_tokens = 10000
        rate_limit_result.ratelimit_reset_requests = 1700000000
        rate_limit_result.ratelimit_reset_tokens = 1700000000
        rate_limit_result.burst_window_description = "100 per 60 seconds"

        rate_limit_mgr_instance = Mock()
        rate_limit_mgr_instance.check_rate_limit = AsyncMock(return_value=rate_limit_result)
        mock_rate_limit_mgr.return_value = rate_limit_mgr_instance

        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test_api_key_12345"},
            json=valid_messages_request,
        )

        assert response.status_code == 429
        assert "rate limit" in response.json()["detail"].lower()


# ============================================================
# TEST CLASS: Messages Endpoint - Plan Limits
# ============================================================


class TestMessagesEndpointPlanLimits:
    """Test plan limit enforcement"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    @patch("src.routes.messages.make_openrouter_request_openai")
    @patch("src.routes.messages.process_openrouter_response")
    def test_messages_endpoint_plan_limit_exceeded(
        self,
        mock_process_or,
        mock_make_or,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user,
        valid_messages_request,
    ):
        """Test plan limit exceeded"""
        mock_get_user.return_value = mock_user
        mock_enforce_plan.return_value = {
            "allowed": False,
            "reason": "Monthly token limit exceeded",
        }
        mock_validate_trial.return_value = {"is_valid": True, "is_trial": False}
        mock_make_or.return_value = {"_raw": True}
        mock_process_or.return_value = {
            "content": [{"type": "text", "text": "test"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test_api_key_12345"},
            json=valid_messages_request,
        )

        assert response.status_code == 429
        assert "plan limit" in response.json()["detail"].lower()


# ============================================================
# TEST CLASS: Messages Endpoint - Trial Validation
# ============================================================


class TestMessagesEndpointTrialValidation:
    """Test trial access validation"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    def test_messages_endpoint_trial_expired(
        self,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user,
        valid_messages_request,
    ):
        """Test expired trial access"""
        mock_get_user.return_value = mock_user
        mock_enforce_plan.return_value = {"allowed": True}
        mock_validate_trial.return_value = {
            "is_valid": False,
            "is_trial": True,
            "is_expired": True,
            "error": "Trial period has ended",
            "trial_end_date": "2024-01-01",
        }

        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test_api_key_12345"},
            json=valid_messages_request,
        )

        assert response.status_code == 403
        assert "X-Trial-Expired" in response.headers


# ============================================================
# TEST CLASS: Messages Endpoint - Validation
# ============================================================


class TestMessagesEndpointValidation:
    """Test request validation"""

    def test_messages_endpoint_missing_max_tokens(self, client):
        """Test request without required max_tokens"""
        request_data = {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [{"role": "user", "content": "Hello"}],
            # max_tokens is missing
        }

        response = client.post(
            "/v1/messages", headers={"Authorization": "Bearer test_key"}, json=request_data
        )

        # Should fail validation
        assert response.status_code == 422

    def test_messages_endpoint_empty_messages(self, client):
        """Test request with empty messages array"""
        request_data = {"model": "claude-sonnet-4-5-20250929", "max_tokens": 100, "messages": []}

        response = client.post(
            "/v1/messages", headers={"Authorization": "Bearer test_key"}, json=request_data
        )

        assert response.status_code == 422

    def test_messages_endpoint_invalid_role(self, client):
        """Test request with invalid message role"""
        request_data = {
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 100,
            "messages": [{"role": "invalid_role", "content": "Hello"}],
        }

        response = client.post(
            "/v1/messages", headers={"Authorization": "Bearer test_key"}, json=request_data
        )

        assert response.status_code == 422


# ============================================================
# TEST CLASS: Messages Endpoint - Provider Failover
# ============================================================


class TestMessagesEndpointFailover:
    """Test provider failover logic"""

    @patch("src.routes.messages.get_user")
    @patch("src.routes.messages.enforce_plan_limits")
    @patch("src.routes.messages.validate_trial_access")
    @patch("src.routes.messages.get_rate_limit_manager")
    @patch("src.routes.messages.process_featherless_response")
    @patch("src.routes.messages.make_featherless_request_openai")
    @patch("src.routes.messages.process_openrouter_response")
    @patch("src.routes.messages.make_openrouter_request_openai")
    @patch("src.routes.messages.build_provider_failover_chain")
    @patch("src.routes.messages.calculate_cost")
    @patch("src.routes.messages.deduct_credits")
    @patch("src.routes.messages.record_usage")
    @patch("src.routes.messages.increment_api_key_usage")
    @patch("src.routes.messages.update_rate_limit_usage")
    @patch("src.routes.messages.log_activity")
    def test_messages_endpoint_provider_failover_success(
        self,
        mock_log_activity,
        mock_update_rate_limit,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_calculate_cost,
        mock_build_chain,
        mock_make_openrouter_request,
        mock_process_openrouter_response,
        mock_make_featherless_request,
        mock_process_featherless_response,
        mock_rate_limit_mgr,
        mock_validate_trial,
        mock_enforce_plan,
        mock_get_user,
        client,
        mock_user,
        mock_openai_response,
        valid_messages_request,
    ):
        """Test successful failover to backup provider"""
        # Setup mocks
        mock_get_user.return_value = mock_user
        mock_enforce_plan.return_value = {"allowed": True}
        mock_validate_trial.return_value = {"is_valid": True, "is_trial": False}

        rate_limit_result = Mock()
        rate_limit_result.allowed = True
        rate_limit_result.remaining_requests = 249
        rate_limit_result.remaining_tokens = 9900
        rate_limit_result.ratelimit_limit_requests = 250
        rate_limit_result.ratelimit_limit_tokens = 10000
        rate_limit_result.ratelimit_reset_requests = 1700000000
        rate_limit_result.ratelimit_reset_tokens = 1700000000
        rate_limit_result.burst_window_description = "100 per 60 seconds"
        rate_limit_mgr_instance = Mock()
        rate_limit_mgr_instance.check_rate_limit = AsyncMock(return_value=rate_limit_result)
        rate_limit_mgr_instance.release_concurrency = AsyncMock()
        mock_rate_limit_mgr.return_value = rate_limit_mgr_instance

        # First provider fails, second succeeds
        mock_build_chain.return_value = ["openrouter", "featherless"]
        mock_make_openrouter_request.side_effect = Exception("Provider error")
        mock_make_featherless_request.return_value = mock_openai_response
        mock_process_openrouter_response.return_value = mock_openai_response
        mock_process_featherless_response.return_value = mock_openai_response
        mock_calculate_cost.return_value = 0.01

        # Execute
        response = client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer test_api_key_12345"},
            json=valid_messages_request,
        )

        # Should succeed after failover
        assert response.status_code == 200
        data = response.json()
        assert data["content"][0]["text"] == "Hello! How can I help you today?"


# ============================================================
# TEST CLASS: Anthropic Transformer - New Features
# ============================================================


class TestAnthropicTransformerNewFeatures:
    """Test new Anthropic API features added for Claude API alignment"""

    def test_transform_system_as_array(self):
        """Test transformation with system as array of content blocks"""
        messages = [{"role": "user", "content": "Hello"}]
        system = [
            {"type": "text", "text": "You are a helpful assistant."},
            {"type": "text", "text": "Be concise in your responses."},
        ]

        openai_messages, params = transform_anthropic_to_openai(
            messages=messages, system=system, max_tokens=100
        )

        # System should be combined into single message
        assert len(openai_messages) == 2
        assert openai_messages[0]["role"] == "system"
        assert "You are a helpful assistant." in openai_messages[0]["content"]
        assert "Be concise in your responses." in openai_messages[0]["content"]

    def test_transform_document_content_block(self):
        """Test transformation of document content blocks"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize this document:"},
                    {
                        "type": "document",
                        "source": {"type": "text", "data": "This is the document content."},
                        "title": "My Document",
                        "context": "A sample document",
                    },
                ],
            }
        ]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        content = openai_messages[0]["content"]
        assert isinstance(content, list)
        # Document should be converted to text
        doc_block = [b for b in content if "Document" in b.get("text", "")]
        assert len(doc_block) > 0
        assert "My Document" in doc_block[0]["text"]

    def test_transform_tool_result_content_block(self):
        """Test transformation of tool_result content blocks"""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "The weather in San Francisco is 72°F and sunny.",
                    }
                ],
            }
        ]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        content = openai_messages[0]["content"]
        # Tool result should be converted to text
        assert "toolu_123" in content
        assert "72°F" in content

    def test_transform_tool_choice_types(self):
        """Test transformation of different tool_choice types"""
        messages = [{"role": "user", "content": "Hello"}]

        # Test auto
        _, params = transform_anthropic_to_openai(
            messages=messages, max_tokens=100, tool_choice={"type": "auto"}
        )
        assert params.get("tool_choice") == "auto"

        # Test any -> required
        _, params = transform_anthropic_to_openai(
            messages=messages, max_tokens=100, tool_choice={"type": "any"}
        )
        assert params.get("tool_choice") == "required"

        # Test none
        _, params = transform_anthropic_to_openai(
            messages=messages, max_tokens=100, tool_choice={"type": "none"}
        )
        assert params.get("tool_choice") == "none"

        # Test specific tool
        _, params = transform_anthropic_to_openai(
            messages=messages, max_tokens=100, tool_choice={"type": "tool", "name": "get_weather"}
        )
        assert params.get("tool_choice") == {
            "type": "function",
            "function": {"name": "get_weather"},
        }

    def test_transform_tools_to_openai_format(self):
        """Test transformation of Anthropic tool definitions to OpenAI format"""
        messages = [{"role": "user", "content": "Hello"}]
        tools = [
            {
                "name": "get_weather",
                "description": "Get weather for a location",
                "input_schema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            }
        ]

        _, params = transform_anthropic_to_openai(messages=messages, max_tokens=100, tools=tools)

        assert "tools" in params
        assert len(params["tools"]) == 1
        assert params["tools"][0]["type"] == "function"
        assert params["tools"][0]["function"]["name"] == "get_weather"
        assert params["tools"][0]["function"]["description"] == "Get weather for a location"
        assert "parameters" in params["tools"][0]["function"]

    def test_transform_assistant_tool_use_message(self):
        """Test transformation of assistant messages with tool_use blocks"""
        messages = [
            {"role": "user", "content": "What is the weather?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "get_weather",
                        "input": {"location": "San Francisco"},
                    }
                ],
            },
        ]

        openai_messages, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100)

        # Assistant message should have tool_calls
        assistant_msg = openai_messages[1]
        assert assistant_msg["role"] == "assistant"
        assert "tool_calls" in assistant_msg
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "toolu_123"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_transform_openai_to_anthropic_with_stop_sequence(self):
        """Test response transformation detecting stop sequences"""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello STOP"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        anthropic_response = transform_openai_to_anthropic(
            openai_response, model="claude-sonnet-4-5-20250929", stop_sequences=["STOP", "END"]
        )

        assert anthropic_response["stop_reason"] == "stop_sequence"
        assert anthropic_response["stop_sequence"] == "STOP"

    def test_transform_openai_to_anthropic_with_thinking(self):
        """Test response transformation with reasoning/thinking content"""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [
                {
                    "message": {
                        "content": "The answer is 42.",
                        "reasoning_content": "Let me think about this step by step...",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 15},
        }

        anthropic_response = transform_openai_to_anthropic(
            openai_response, model="claude-sonnet-4-5-20250929"
        )

        # Should have thinking block followed by text block
        assert len(anthropic_response["content"]) == 2
        assert anthropic_response["content"][0]["type"] == "thinking"
        assert "step by step" in anthropic_response["content"][0]["thinking"]
        assert anthropic_response["content"][1]["type"] == "text"
        assert anthropic_response["content"][1]["text"] == "The answer is 42."

    def test_transform_openai_to_anthropic_refusal(self):
        """Test response transformation mapping content_filter to refusal"""
        openai_response = {
            "id": "chatcmpl-123",
            "choices": [
                {
                    "message": {"content": "I cannot help with that."},
                    "finish_reason": "content_filter",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        anthropic_response = transform_openai_to_anthropic(
            openai_response, model="claude-sonnet-4-5-20250929"
        )

        assert anthropic_response["stop_reason"] == "refusal"


# ============================================================
# TEST CLASS: MessagesRequest Schema Validation
# ============================================================


class TestMessagesRequestSchema:
    """Test MessagesRequest schema validation"""

    def test_valid_request_with_string_system(self):
        """Test valid request with string system prompt"""
        from src.schemas import AnthropicMessage, MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[AnthropicMessage(role="user", content="Hello")],
            system="You are helpful.",
        )

        assert request.system == "You are helpful."

    def test_valid_request_with_array_system(self):
        """Test valid request with array system prompt"""
        from src.schemas import AnthropicMessage, MessagesRequest, SystemContentBlock

        request = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[AnthropicMessage(role="user", content="Hello")],
            system=[
                SystemContentBlock(type="text", text="You are helpful."),
                SystemContentBlock(type="text", text="Be concise."),
            ],
        )

        assert len(request.system) == 2
        assert request.system[0].text == "You are helpful."

    def test_valid_request_with_service_tier(self):
        """Test valid request with service_tier parameter"""
        from src.schemas import AnthropicMessage, MessagesRequest

        request = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[AnthropicMessage(role="user", content="Hello")],
            service_tier="auto",
        )

        assert request.service_tier == "auto"

        request2 = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[AnthropicMessage(role="user", content="Hello")],
            service_tier="standard_only",
        )

        assert request2.service_tier == "standard_only"

    def test_valid_request_with_thinking_config(self):
        """Test valid request with thinking configuration"""
        from src.schemas import AnthropicMessage, MessagesRequest, ThinkingConfig

        request = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            messages=[AnthropicMessage(role="user", content="Hello")],
            thinking=ThinkingConfig(type="enabled", budget_tokens=2048),
        )

        assert request.thinking.type == "enabled"
        assert request.thinking.budget_tokens == 2048

    def test_thinking_config_budget_validation(self):
        """Test thinking budget_tokens validation (must be >= 1024)"""
        import pytest

        from src.schemas import ThinkingConfig

        with pytest.raises(ValueError, match="at least 1024"):
            ThinkingConfig(type="enabled", budget_tokens=500)

    def test_temperature_validation(self):
        """Test temperature must be between 0.0 and 1.0"""
        import pytest

        from src.schemas import AnthropicMessage, MessagesRequest

        # Valid temperature
        request = MessagesRequest(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[AnthropicMessage(role="user", content="Hello")],
            temperature=0.7,
        )
        assert request.temperature == 0.7

        # Invalid temperature (too high)
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            MessagesRequest(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                messages=[AnthropicMessage(role="user", content="Hello")],
                temperature=1.5,
            )

    def test_content_block_types(self):
        """Test ContentBlock supports all required types"""
        from src.schemas import ContentBlock

        # Text block
        text_block = ContentBlock(type="text", text="Hello")
        assert text_block.type == "text"
        assert text_block.text == "Hello"

        # Image block
        image_block = ContentBlock(
            type="image", source={"type": "base64", "data": "abc123", "media_type": "image/png"}
        )
        assert image_block.type == "image"
        assert image_block.source["type"] == "base64"

        # Document block
        doc_block = ContentBlock(
            type="document",
            source={"type": "text", "data": "Document content"},
            title="My Doc",
            context="Additional context",
        )
        assert doc_block.type == "document"
        assert doc_block.title == "My Doc"

        # Tool use block
        tool_use_block = ContentBlock(
            type="tool_use", id="toolu_123", name="get_weather", input={"location": "SF"}
        )
        assert tool_use_block.type == "tool_use"
        assert tool_use_block.name == "get_weather"

        # Tool result block
        tool_result_block = ContentBlock(
            type="tool_result",
            tool_use_id="toolu_123",
            content="The weather is sunny.",
            is_error=False,
        )
        assert tool_result_block.type == "tool_result"
        assert tool_result_block.tool_use_id == "toolu_123"

    def test_tool_choice_models(self):
        """Test tool choice model types"""
        from src.schemas import ToolChoiceAny, ToolChoiceAuto, ToolChoiceNone, ToolChoiceTool

        auto = ToolChoiceAuto()
        assert auto.type == "auto"

        any_choice = ToolChoiceAny(disable_parallel_tool_use=True)
        assert any_choice.type == "any"
        assert any_choice.disable_parallel_tool_use is True

        none_choice = ToolChoiceNone()
        assert none_choice.type == "none"

        tool = ToolChoiceTool(name="get_weather")
        assert tool.type == "tool"
        assert tool.name == "get_weather"


# ============================================================
# TEST CLASS: MessagesResponse Schema
# ============================================================


class TestMessagesResponseSchema:
    """Test MessagesResponse schema"""

    def test_response_schema_structure(self):
        """Test MessagesResponse has correct structure"""
        from src.schemas import MessagesResponse, TextBlockResponse, UsageResponse

        response = MessagesResponse(
            id="msg_123",
            type="message",
            role="assistant",
            model="claude-sonnet-4-5-20250929",
            content=[TextBlockResponse(type="text", text="Hello!")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=UsageResponse(input_tokens=10, output_tokens=5),
        )

        assert response.id == "msg_123"
        assert response.type == "message"
        assert response.role == "assistant"
        assert response.stop_reason == "end_turn"
        assert response.usage.input_tokens == 10

    def test_usage_response_with_cache_fields(self):
        """Test UsageResponse with cache-related fields"""
        from src.schemas import UsageResponse

        usage = UsageResponse(
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=800,
        )

        assert usage.cache_creation_input_tokens == 200
        assert usage.cache_read_input_tokens == 800

    def test_stop_reason_values(self):
        """Test all valid stop_reason values"""
        from src.schemas import MessagesResponse, TextBlockResponse, UsageResponse

        valid_stop_reasons = [
            "end_turn",
            "max_tokens",
            "stop_sequence",
            "tool_use",
            "pause_turn",
            "refusal",
        ]

        for reason in valid_stop_reasons:
            response = MessagesResponse(
                id="msg_123",
                type="message",
                role="assistant",
                model="claude-sonnet-4-5-20250929",
                content=[TextBlockResponse(type="text", text="Hello!")],
                stop_reason=reason,
                usage=UsageResponse(input_tokens=10, output_tokens=5),
            )
            assert response.stop_reason == reason
