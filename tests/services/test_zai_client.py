"""Tests for Z.AI client"""

from unittest.mock import Mock, patch

import pytest

from src.services.zai_client import (
    get_zai_client,
    make_zai_request_openai,
    make_zai_request_openai_stream,
    process_zai_response,
)


class TestZaiClient:
    """Test Z.AI client functionality"""

    @patch("src.services.zai_client.Config.ZAI_API_KEY", "test_key")
    @patch("src.services.zai_client.get_zai_pooled_client")
    def test_get_zai_client(self, mock_pooled_client):
        """Test getting Z.AI client"""
        mock_client = Mock()
        mock_pooled_client.return_value = mock_client

        client = get_zai_client()
        assert client is not None
        mock_pooled_client.assert_called_once()

    @patch("src.services.zai_client.Config.ZAI_API_KEY", None)
    def test_get_zai_client_no_key(self):
        """Test getting Z.AI client without API key"""
        with pytest.raises(ValueError, match="Z.AI API key not configured"):
            get_zai_client()

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai(self, mock_get_client):
        """Test making request to Z.AI"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "glm-4.7"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_zai_request_openai(messages, "glm-4.7")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai_with_kwargs(self, mock_get_client):
        """Test making request to Z.AI with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_zai_request_openai(messages, "glm-4.7", max_tokens=100, temperature=0.7)

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="glm-4.7",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai_error(self, mock_get_client):
        """Test handling errors from Z.AI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_zai_request_openai(messages, "glm-4.7")

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Z.AI"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_zai_request_openai_stream(messages, "glm-4.7")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="glm-4.7", messages=messages, stream=True
        )

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request to Z.AI with additional parameters"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_zai_request_openai_stream(messages, "glm-4.5-air", max_tokens=500)

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="glm-4.5-air", messages=messages, stream=True, max_tokens=500
        )

    @patch("src.services.zai_client.get_zai_client")
    def test_make_zai_request_openai_stream_error(self, mock_get_client):
        """Test handling streaming errors from Z.AI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_zai_request_openai_stream(messages, "glm-4.7")

    def test_process_zai_response(self):
        """Test processing Z.AI response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "chatcmpl-test123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "glm-4.7"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Hello! How can I help you today?"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # Mock usage
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 15
        mock_response.usage.completion_tokens = 25
        mock_response.usage.total_tokens = 40

        processed = process_zai_response(mock_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "glm-4.7"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 15
        assert processed["usage"]["completion_tokens"] == 25
        assert processed["usage"]["total_tokens"] == 40

    def test_process_zai_response_no_usage(self):
        """Test processing Z.AI response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "glm-4.7"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        processed = process_zai_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    def test_process_zai_response_multiple_choices(self):
        """Test processing Z.AI response with multiple choices"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "glm-4.7"

        # Create multiple choices
        choices = []
        for i in range(3):
            mock_choice = Mock()
            mock_choice.index = i
            mock_choice.message = Mock()
            mock_choice.message.role = "assistant"
            mock_choice.message.content = f"Response {i}"
            mock_choice.message.tool_calls = None
            mock_choice.finish_reason = "stop"
            choices.append(mock_choice)

        mock_response.choices = choices
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 30
        mock_response.usage.total_tokens = 40

        processed = process_zai_response(mock_response)

        assert len(processed["choices"]) == 3
        for i, choice in enumerate(processed["choices"]):
            assert choice["index"] == i
            assert choice["message"]["content"] == f"Response {i}"

    def test_process_zai_response_with_tool_calls(self):
        """Test processing Z.AI response with tool calls"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "glm-4.7"

        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "get_weather"
        mock_tool_call.function.arguments = '{"location": "San Francisco"}'

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = None
        mock_choice.message.tool_calls = [mock_tool_call]
        mock_choice.finish_reason = "tool_calls"
        mock_response.choices = [mock_choice]

        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 20
        mock_response.usage.completion_tokens = 15
        mock_response.usage.total_tokens = 35

        processed = process_zai_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["choices"][0]["finish_reason"] == "tool_calls"
        assert "tool_calls" in processed["choices"][0]["message"]
