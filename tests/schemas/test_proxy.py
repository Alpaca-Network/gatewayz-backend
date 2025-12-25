"""Tests for proxy schemas, including tools field validation and multimodal content"""

import pytest
from pydantic import ValidationError

from src.schemas.proxy import Message, ProxyRequest, ResponseRequest


class TestMessageMultimodalContent:
    """Test Message schema with multimodal content (images, audio, etc.)"""

    def test_message_with_string_content(self):
        """Test Message with simple string content"""
        message = Message(role="user", content="Hello, world!")
        assert message.content == "Hello, world!"
        assert message.role == "user"

    def test_message_with_multimodal_content_text_and_image(self):
        """Test Message with text and image content (multimodal)"""
        content = [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
        ]
        message = Message(role="user", content=content)
        assert isinstance(message.content, list)
        assert len(message.content) == 2
        assert message.content[0]["type"] == "text"
        assert message.content[1]["type"] == "image_url"

    def test_message_with_image_only_content(self):
        """Test Message with image-only content"""
        content = [
            {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
        ]
        message = Message(role="user", content=content)
        assert isinstance(message.content, list)
        assert len(message.content) == 1
        assert message.content[0]["type"] == "image_url"

    def test_message_with_file_content(self):
        """Test Message with file/document content"""
        content = [
            {"type": "text", "text": "Summarize this document"},
            {"type": "file_url", "file_url": {"url": "data:application/pdf;base64,..."}}
        ]
        message = Message(role="user", content=content)
        assert isinstance(message.content, list)
        assert len(message.content) == 2

    def test_message_empty_string_content_fails(self):
        """Test that empty string content fails validation"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="user", content="   ")
        assert "non-empty string" in str(exc_info.value).lower()

    def test_message_empty_array_content_fails(self):
        """Test that empty array content fails validation"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="user", content=[])
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_message_content_item_missing_type_fails(self):
        """Test that content items without 'type' field fail validation"""
        content = [
            {"text": "Hello"}  # Missing 'type' field
        ]
        with pytest.raises(ValidationError) as exc_info:
            Message(role="user", content=content)
        assert "type" in str(exc_info.value).lower()

    def test_message_content_item_not_dict_fails(self):
        """Test that non-dict content items fail validation"""
        content = [
            "just a string"  # Not a dict
        ]
        with pytest.raises(ValidationError) as exc_info:
            Message(role="user", content=content)
        assert "dictionary" in str(exc_info.value).lower()

    def test_message_invalid_role_fails(self):
        """Test that invalid role fails validation"""
        with pytest.raises(ValidationError) as exc_info:
            Message(role="invalid_role", content="Hello")
        assert "invalid message role" in str(exc_info.value).lower()

    def test_message_assistant_role_with_multimodal(self):
        """Test assistant role with multimodal content (for tool results, etc.)"""
        content = [
            {"type": "text", "text": "Here is the image you requested"},
            {"type": "image_url", "image_url": {"url": "https://example.com/result.png"}}
        ]
        message = Message(role="assistant", content=content)
        assert message.role == "assistant"
        assert isinstance(message.content, list)


class TestProxyRequestMultimodalMessages:
    """Test ProxyRequest with multimodal messages"""

    def test_proxy_request_with_multimodal_message(self):
        """Test ProxyRequest with multimodal message content"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                ]
            }
        ]
        request = ProxyRequest(model="gpt-4o", messages=messages)
        assert len(request.messages) == 1
        assert isinstance(request.messages[0].content, list)
        assert len(request.messages[0].content) == 2

    def test_proxy_request_with_mixed_messages(self):
        """Test ProxyRequest with mix of string and multimodal messages"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ]
        request = ProxyRequest(model="gpt-4o", messages=messages)
        assert len(request.messages) == 2
        assert isinstance(request.messages[0].content, str)
        assert isinstance(request.messages[1].content, list)


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

