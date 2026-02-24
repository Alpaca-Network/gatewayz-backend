"""Tests for OpenAI client"""

from unittest.mock import Mock, patch

import pytest

from src.services.openai_client import (
    get_openai_client,
    make_openai_request,
    make_openai_request_stream,
    process_openai_response,
)


class TestOpenAIClient:
    """Test OpenAI client functionality"""

    @patch("src.services.openai_client.Config.OPENAI_API_KEY", "test_key")
    def test_get_openai_client(self):
        """Test getting OpenAI client"""
        client = get_openai_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://api.openai.com/v1"

    @patch("src.services.openai_client.Config.OPENAI_API_KEY", None)
    def test_get_openai_client_no_key(self):
        """Test getting OpenAI client without API key"""
        with pytest.raises(ValueError, match="OpenAI API key not configured"):
            get_openai_client()

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request(self, mock_get_client):
        """Test making request to OpenAI"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "gpt-4o"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_openai_request(messages, "gpt-4o")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request_with_kwargs(self, mock_get_client):
        """Test making request to OpenAI with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_openai_request(messages, "gpt-4o", max_tokens=100, temperature=0.7)

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request_error(self, mock_get_client):
        """Test handling errors from OpenAI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_openai_request(messages, "gpt-4o")

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request_stream(self, mock_get_client):
        """Test making streaming request to OpenAI"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_openai_request_stream(messages, "gpt-4o-mini")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini", messages=messages, stream=True
        )

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request to OpenAI with additional parameters"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_openai_request_stream(messages, "gpt-4-turbo", max_tokens=500)

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4-turbo", messages=messages, stream=True, max_tokens=500
        )

    @patch("src.services.openai_client.get_openai_client")
    def test_make_openai_request_stream_error(self, mock_get_client):
        """Test handling streaming errors from OpenAI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_openai_request_stream(messages, "gpt-4o")

    def test_process_openai_response(self):
        """Test processing OpenAI response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "chatcmpl-test123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "gpt-4o"

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

        processed = process_openai_response(mock_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "gpt-4o"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 15
        assert processed["usage"]["completion_tokens"] == 25
        assert processed["usage"]["total_tokens"] == 40

    def test_process_openai_response_no_usage(self):
        """Test processing OpenAI response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "gpt-4o"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        processed = process_openai_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    def test_process_openai_response_multiple_choices(self):
        """Test processing OpenAI response with multiple choices"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "gpt-4o"

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

        processed = process_openai_response(mock_response)

        assert len(processed["choices"]) == 3
        for i, choice in enumerate(processed["choices"]):
            assert choice["index"] == i
            assert choice["message"]["content"] == f"Response {i}"
