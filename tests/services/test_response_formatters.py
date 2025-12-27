"""Tests for response formatters"""

import pytest
from src.services.response_formatters import ResponseFormatter, _map_finish_reason_to_anthropic


class TestResponseFormatters:
    """Test response formatting for different API formats"""

    @pytest.fixture
    def unified_response(self):
        """Sample unified response"""
        return {
            "id": "test-123",
            "created": 1234567890,
            "model": "gpt-4",
            "content": "Hello, world!",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15
            },
            "gateway_usage": {
                "cost_usd": 0.001,
                "provider": "openrouter"
            }
        }

    def test_format_to_openai(self, unified_response):
        """Test formatting to OpenAI format"""
        result = ResponseFormatter.to_openai(unified_response)

        assert result["object"] == "chat.completion"
        assert result["id"] == "test-123"
        assert result["model"] == "gpt-4"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["message"]["content"] == "Hello, world!"
        assert result["choices"][0]["message"]["role"] == "assistant"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["total_tokens"] == 15
        assert result["gateway_usage"]["cost_usd"] == 0.001

    def test_format_to_openai_with_defaults(self):
        """Test OpenAI formatting with minimal response"""
        minimal_response = {
            "content": "Test"
        }

        result = ResponseFormatter.to_openai(minimal_response)

        assert result["object"] == "chat.completion"
        assert "id" in result
        assert "created" in result
        assert result["model"] == "unknown"
        assert result["choices"][0]["message"]["content"] == "Test"
        assert result["usage"]["total_tokens"] == 0

    def test_format_to_anthropic(self, unified_response):
        """Test formatting to Anthropic format"""
        result = ResponseFormatter.to_anthropic(unified_response)

        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Hello, world!"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5

    def test_format_to_anthropic_with_defaults(self):
        """Test Anthropic formatting with minimal response"""
        minimal_response = {
            "content": "Test"
        }

        result = ResponseFormatter.to_anthropic(minimal_response)

        assert result["type"] == "message"
        assert "id" in result
        assert result["content"][0]["text"] == "Test"
        assert result["usage"]["input_tokens"] == 0

    def test_format_to_responses_api(self, unified_response):
        """Test formatting to Responses API format"""
        result = ResponseFormatter.to_responses_api(unified_response)

        assert result["object"] == "response"
        assert len(result["output"]) == 1
        assert result["output"][0]["role"] == "assistant"
        assert result["output"][0]["content"] == "Hello, world!"
        assert result["output"][0]["finish_reason"] == "stop"
        assert result["usage"]["total_tokens"] == 15

    def test_format_to_responses_api_with_defaults(self):
        """Test Responses API formatting with minimal response"""
        minimal_response = {
            "content": "Test"
        }

        result = ResponseFormatter.to_responses_api(minimal_response)

        assert result["object"] == "response"
        assert "id" in result
        assert result["output"][0]["content"] == "Test"

    def test_format_with_tool_calls_openai(self):
        """Test formatting response with tool calls (OpenAI)"""
        response = {
            "id": "test-123",
            "model": "gpt-4",
            "content": "",
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "SF"}'
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }

        # OpenAI format should include tool_calls
        openai = ResponseFormatter.to_openai(response)
        assert "tool_calls" in openai["choices"][0]["message"]
        assert len(openai["choices"][0]["message"]["tool_calls"]) == 1

    def test_format_with_tool_calls_anthropic(self):
        """Test formatting response with tool calls (Anthropic)"""
        response = {
            "id": "test-123",
            "model": "gpt-4",
            "content": "I'll check the weather",
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "SF"}'
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }

        # Anthropic format should convert to tool_use
        anthropic = ResponseFormatter.to_anthropic(response)
        assert len(anthropic["content"]) == 2  # Text + tool_use
        assert anthropic["content"][0]["type"] == "text"
        assert anthropic["content"][1]["type"] == "tool_use"
        assert anthropic["content"][1]["name"] == "get_weather"

    def test_format_with_tool_calls_responses(self):
        """Test formatting response with tool calls (Responses API)"""
        response = {
            "id": "test-123",
            "model": "gpt-4",
            "content": "",
            "finish_reason": "tool_calls",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "SF"}'
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }

        # Responses format should include tool_calls
        responses = ResponseFormatter.to_responses_api(response)
        assert "tool_calls" in responses["output"][0]

    def test_format_response_router_openai(self, unified_response):
        """Test format_response routing to OpenAI"""
        result = ResponseFormatter.format_response(unified_response, "openai")
        assert result["object"] == "chat.completion"

    def test_format_response_router_anthropic(self, unified_response):
        """Test format_response routing to Anthropic"""
        result = ResponseFormatter.format_response(unified_response, "anthropic")
        assert result["type"] == "message"

    def test_format_response_router_responses(self, unified_response):
        """Test format_response routing to Responses API"""
        result = ResponseFormatter.format_response(unified_response, "responses")
        assert result["object"] == "response"

    def test_format_response_router_default(self, unified_response):
        """Test format_response defaults to OpenAI for unknown format"""
        result = ResponseFormatter.format_response(unified_response, "unknown")
        assert result["object"] == "chat.completion"

    def test_format_response_router_case_insensitive(self, unified_response):
        """Test format_response is case-insensitive"""
        result1 = ResponseFormatter.format_response(unified_response, "OpenAI")
        result2 = ResponseFormatter.format_response(unified_response, "OPENAI")
        result3 = ResponseFormatter.format_response(unified_response, "openai")

        assert result1["object"] == result2["object"] == result3["object"] == "chat.completion"


class TestFinishReasonMapping:
    """Test finish reason mapping to Anthropic stop reasons"""

    def test_map_stop_to_end_turn(self):
        """Test 'stop' maps to 'end_turn'"""
        assert _map_finish_reason_to_anthropic("stop") == "end_turn"

    def test_map_length_to_max_tokens(self):
        """Test 'length' maps to 'max_tokens'"""
        assert _map_finish_reason_to_anthropic("length") == "max_tokens"

    def test_map_function_call_to_tool_use(self):
        """Test 'function_call' maps to 'tool_use'"""
        assert _map_finish_reason_to_anthropic("function_call") == "tool_use"

    def test_map_tool_calls_to_tool_use(self):
        """Test 'tool_calls' maps to 'tool_use'"""
        assert _map_finish_reason_to_anthropic("tool_calls") == "tool_use"

    def test_map_content_filter_to_end_turn(self):
        """Test 'content_filter' maps to 'end_turn'"""
        assert _map_finish_reason_to_anthropic("content_filter") == "end_turn"

    def test_map_unknown_to_end_turn(self):
        """Test unknown finish reason defaults to 'end_turn'"""
        assert _map_finish_reason_to_anthropic("unknown_reason") == "end_turn"
