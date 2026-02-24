"""Tests for proxy schemas, including tools field validation.

These tests verify alignment with OpenAI's Chat Completions API specification.
See: https://platform.openai.com/docs/api-reference/chat/create
"""

import pytest
from pydantic import ValidationError

from src.schemas.proxy import Message, ProxyRequest, ResponseRequest, StreamOptions


class TestMessageSchema:
    """Test Message schema for OpenAI compatibility"""

    def test_basic_message(self):
        """Test basic message creation"""
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_with_tool_calls(self):
        """Test assistant message with tool_calls"""
        tool_calls = [
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "Boston"}'},
            }
        ]
        msg = Message(role="assistant", content=None, tool_calls=tool_calls)
        assert msg.role == "assistant"
        assert msg.content is None
        assert msg.tool_calls == tool_calls

    def test_tool_response_message(self):
        """Test tool response message"""
        msg = Message(role="tool", content='{"temperature": 72}', tool_call_id="call_123")
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_123"

    def test_function_message(self):
        """Test function message with name"""
        msg = Message(role="function", content='{"result": "success"}', name="my_function")
        assert msg.role == "function"
        assert msg.name == "my_function"

    def test_multimodal_content(self):
        """Test message with multimodal content array"""
        content = [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        ]
        msg = Message(role="user", content=content)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2

    def test_valid_roles(self):
        """Test all valid message roles"""
        valid_roles = ["system", "user", "assistant", "tool", "function", "developer"]
        for role in valid_roles:
            msg = Message(role=role, content="test")
            assert msg.role == role

    def test_invalid_role(self):
        """Test invalid message role raises error"""
        with pytest.raises(ValidationError):
            Message(role="invalid_role", content="test")

    def test_user_message_requires_content(self):
        """Test that user messages must have content"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="user", content=None)
        assert "'user' messages must have non-empty content" in str(exc_info.value)

    def test_system_message_requires_content(self):
        """Test that system messages must have content"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="system", content=None)
        assert "'system' messages must have non-empty content" in str(exc_info.value)

    def test_developer_message_requires_content(self):
        """Test that developer messages must have content"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="developer", content=None)
        assert "'developer' messages must have non-empty content" in str(exc_info.value)

    def test_developer_message_basic(self):
        """Test basic developer message creation (OpenAI developer role)"""
        msg = Message(role="developer", content="You are a helpful assistant.")
        assert msg.role == "developer"
        assert msg.content == "You are a helpful assistant."

    def test_tool_message_requires_content(self):
        """Test that tool messages must have content (the response)"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="tool", content=None, tool_call_id="call_123")
        assert "'tool' messages must have non-empty content" in str(exc_info.value)

    def test_function_message_requires_content(self):
        """Test that function messages must have content"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="function", content=None, name="my_function")
        assert "'function' messages must have non-empty content" in str(exc_info.value)

    def test_assistant_message_allows_null_content_with_tool_calls(self):
        """Test that assistant messages can have null content when tool_calls is present"""
        tool_calls = [{"id": "call_123", "type": "function", "function": {"name": "test"}}]
        msg = Message(role="assistant", content=None, tool_calls=tool_calls)
        assert msg.content is None
        assert msg.tool_calls == tool_calls

    def test_assistant_message_requires_content_or_tool_calls(self):
        """Test that assistant messages need either content or tool_calls"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="assistant", content=None)
        assert "must have either non-empty content or tool_calls" in str(exc_info.value)


class TestProxyRequestOpenAIAlignment:
    """Test ProxyRequest schema alignment with OpenAI Chat Completions API"""

    def test_minimal_request(self):
        """Test minimal valid request"""
        request = ProxyRequest(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
        assert request.model == "gpt-4"
        assert len(request.messages) == 1

    def test_all_sampling_parameters(self):
        """Test all sampling parameters"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
            top_p=0.9,
            n=2,
            stop=["END", "STOP"],
        )
        assert request.max_tokens == 100
        assert request.temperature == 0.7
        assert request.top_p == 0.9
        assert request.n == 2
        assert request.stop == ["END", "STOP"]

    def test_penalty_parameters(self):
        """Test frequency and presence penalty parameters"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            frequency_penalty=0.5,
            presence_penalty=-0.5,
        )
        assert request.frequency_penalty == 0.5
        assert request.presence_penalty == -0.5

    def test_penalty_bounds(self):
        """Test penalty parameter bounds (-2.0 to 2.0)"""
        # Valid boundary values
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            frequency_penalty=-2.0,
            presence_penalty=2.0,
        )
        assert request.frequency_penalty == -2.0
        assert request.presence_penalty == 2.0

        # Invalid values should raise error
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                frequency_penalty=-3.0,
            )

        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                presence_penalty=3.0,
            )

    def test_temperature_bounds(self):
        """Test temperature parameter bounds (0 to 2)"""
        # Valid values
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.0,
        )
        assert request.temperature == 0.0

        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=2.0,
        )
        assert request.temperature == 2.0

        # Invalid values
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=-0.1,
            )

        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=2.1,
            )

    def test_streaming_parameters(self):
        """Test streaming parameters"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        assert request.stream is True
        assert request.stream_options is not None

    def test_tool_choice_string(self):
        """Test tool_choice with string value"""
        for choice in ["none", "auto", "required"]:
            request = ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "test", "parameters": {}},
                    }
                ],
                tool_choice=choice,
            )
            assert request.tool_choice == choice

    def test_tool_choice_object(self):
        """Test tool_choice with object value (specific function)"""
        tool_choice = {"type": "function", "function": {"name": "get_weather"}}
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "get_weather", "parameters": {}},
                }
            ],
            tool_choice=tool_choice,
        )
        assert request.tool_choice == tool_choice

    def test_parallel_tool_calls(self):
        """Test parallel_tool_calls parameter"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}],
            parallel_tool_calls=False,
        )
        assert request.parallel_tool_calls is False

    def test_response_format_json_object(self):
        """Test response_format with json_object type"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Return JSON"}],
            response_format={"type": "json_object"},
        )
        assert request.response_format == {"type": "json_object"}

    def test_response_format_json_schema(self):
        """Test response_format with json_schema type"""
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "person",
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                },
            },
        }
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Return person info"}],
            response_format=schema,
        )
        assert request.response_format == schema

    def test_logprobs_parameters(self):
        """Test logprobs and top_logprobs parameters"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            logprobs=True,
            top_logprobs=5,
        )
        assert request.logprobs is True
        assert request.top_logprobs == 5

    def test_top_logprobs_bounds(self):
        """Test top_logprobs bounds (0 to 20)"""
        # Valid values
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            logprobs=True,
            top_logprobs=0,
        )
        assert request.top_logprobs == 0

        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            logprobs=True,
            top_logprobs=20,
        )
        assert request.top_logprobs == 20

        # Invalid value
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                logprobs=True,
                top_logprobs=21,
            )

    def test_logit_bias(self):
        """Test logit_bias parameter"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            logit_bias={"50256": -100, "15496": 50},
        )
        assert request.logit_bias == {"50256": -100, "15496": 50}

    def test_seed_parameter(self):
        """Test seed parameter for deterministic sampling"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            seed=42,
        )
        assert request.seed == 42

    def test_user_parameter(self):
        """Test user parameter for end-user identification"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            user="user-123",
        )
        assert request.user == "user-123"

    def test_service_tier_parameter(self):
        """Test service_tier parameter"""
        for tier in ["auto", "default"]:
            request = ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                service_tier=tier,
            )
            assert request.service_tier == tier

    def test_stop_string(self):
        """Test stop parameter with single string"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            stop="END",
        )
        assert request.stop == "END"

    def test_stop_list(self):
        """Test stop parameter with list of strings"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            stop=["END", "STOP", ".", "\n"],
        )
        assert request.stop == ["END", "STOP", ".", "\n"]

    def test_stop_max_sequences(self):
        """Test stop parameter enforces max 4 sequences"""
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                stop=["1", "2", "3", "4", "5"],
            )

    def test_gateway_provider_parameter(self):
        """Test gateway-specific provider parameter"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            provider="openrouter",
        )
        assert request.provider == "openrouter"

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed for forward compatibility"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            unknown_field="value",
        )
        assert request.model == "gpt-4"

    def test_empty_messages_rejected(self):
        """Test that empty messages list is rejected"""
        with pytest.raises(ValidationError):
            ProxyRequest(model="gpt-4", messages=[])

    def test_n_minimum_value(self):
        """Test n parameter minimum value (1)"""
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                n=0,
            )

    def test_complete_request(self):
        """Test complete request with all parameters"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=1000,
            temperature=0.7,
            top_p=0.95,
            n=1,
            stop=["\n"],
            frequency_penalty=0.5,
            presence_penalty=0.5,
            stream=True,
            stream_options={"include_usage": True},
            tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}],
            tool_choice="auto",
            parallel_tool_calls=True,
            response_format={"type": "json_object"},
            logprobs=True,
            top_logprobs=5,
            logit_bias={"50256": -100},
            seed=42,
            user="user-123",
            provider="openrouter",
        )
        assert request.model == "gpt-4"
        assert len(request.messages) == 2
        assert request.max_tokens == 1000
        assert request.temperature == 0.7
        assert request.top_p == 0.95
        assert request.n == 1
        assert request.stop == ["\n"]
        assert request.frequency_penalty == 0.5
        assert request.presence_penalty == 0.5
        assert request.stream is True
        assert request.stream_options is not None
        assert request.tools is not None
        assert request.tool_choice == "auto"
        assert request.parallel_tool_calls is True
        assert request.response_format == {"type": "json_object"}
        assert request.logprobs is True
        assert request.top_logprobs == 5
        assert request.logit_bias == {"50256": -100}
        assert request.seed == 42
        assert request.user == "user-123"
        assert request.provider == "openrouter"


class TestStreamOptionsSchema:
    """Test StreamOptions schema"""

    def test_stream_options_include_usage(self):
        """Test StreamOptions with include_usage"""
        options = StreamOptions(include_usage=True)
        assert options.include_usage is True

    def test_stream_options_default(self):
        """Test StreamOptions with default values"""
        options = StreamOptions()
        assert options.include_usage is None


class TestProxyRequestTools:
    """Test ProxyRequest schema with tools field"""

    def test_proxy_request_without_tools(self):
        """Test ProxyRequest without tools field"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert request.tools is None

    def test_proxy_request_with_tools(self):
        """Test ProxyRequest with tools field"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                },
            }
        ]
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            tools=tools,
        )
        assert request.tools == tools
        assert len(request.tools) == 1

    def test_proxy_request_with_empty_tools_list(self):
        """Test ProxyRequest with empty tools list"""
        request = ProxyRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
        )
        assert request.tools == []

    def test_proxy_request_tools_extra_fields(self):
        """Test ProxyRequest accepts tools via extra fields (backward compatibility)"""
        # Even though tools is now explicitly defined, extra="allow" should still work
        request_data = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "test_function",
                        "description": "Test",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        request = ProxyRequest(**request_data)
        assert request.tools is not None
        assert len(request.tools) == 1


class TestResponseRequestTools:
    """Test ResponseRequest schema with tools field"""

    def test_response_request_without_tools(self):
        """Test ResponseRequest without tools field"""
        request = ResponseRequest(
            model="gpt-4",
            input=[{"role": "user", "content": "Hello"}],
        )
        assert request.tools is None

    def test_response_request_with_tools(self):
        """Test ResponseRequest with tools field"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                },
            }
        ]
        request = ResponseRequest(
            model="gpt-4",
            input=[{"role": "user", "content": "Hello"}],
            tools=tools,
        )
        assert request.tools == tools
        assert len(request.tools) == 1

    def test_response_request_with_multiple_tools(self):
        """Test ResponseRequest with multiple tools"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Get time",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        request = ResponseRequest(
            model="gpt-4",
            input=[{"role": "user", "content": "Hello"}],
            tools=tools,
        )
        assert len(request.tools) == 2


class TestMessageContentValidation:
    """Test empty string validation for message content"""

    def test_user_message_rejects_empty_string(self):
        """Test that user messages reject empty string content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="user", content="")

    def test_user_message_rejects_whitespace_only(self):
        """Test that user messages reject whitespace-only content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="user", content="   \n\t  ")

    def test_system_message_rejects_empty_string(self):
        """Test that system messages reject empty string content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="system", content="")

    def test_developer_message_rejects_empty_string(self):
        """Test that developer messages reject empty string content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="developer", content="")

    def test_developer_message_rejects_whitespace_only(self):
        """Test that developer messages reject whitespace-only content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="developer", content="   \n\t  ")

    def test_tool_message_rejects_empty_string(self):
        """Test that tool messages reject empty string content"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="tool", content="", tool_call_id="123")

    def test_assistant_message_rejects_empty_without_tool_calls(self):
        """Test that assistant messages reject empty content without tool_calls"""
        with pytest.raises(
            ValidationError, match="must have either non-empty content or tool_calls"
        ):
            Message(role="assistant", content="")

    def test_assistant_message_allows_null_with_tool_calls(self):
        """Test that assistant messages allow null content with tool_calls"""
        msg = Message(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "test", "arguments": "{}"},
                }
            ],
        )
        assert msg.content is None
        assert len(msg.tool_calls) == 1

    def test_user_message_allows_non_empty_string(self):
        """Test that user messages accept valid non-empty content"""
        msg = Message(role="user", content="Hello world")
        assert msg.content == "Hello world"

    def test_multimodal_content_empty_list_rejected(self):
        """Test that empty list content is rejected for user messages"""
        with pytest.raises(ValidationError, match="must have non-empty content"):
            Message(role="user", content=[])
