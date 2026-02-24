"""Tests for Groq client"""

from unittest.mock import Mock, patch

import pytest

from src.services.groq_client import (
    get_groq_client,
    make_groq_request_openai,
    make_groq_request_openai_stream,
    process_groq_response,
)


class TestGroqClient:
    """Test Groq client functionality"""

    @patch("src.services.groq_client.Config.GROQ_API_KEY", "test_key")
    def test_get_groq_client(self):
        """Test getting Groq client"""
        client = get_groq_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://api.groq.com/openai/v1"

    @patch("src.services.groq_client.Config.GROQ_API_KEY", None)
    def test_get_groq_client_no_key(self):
        """Test getting Groq client without API key"""
        with pytest.raises(ValueError, match="Groq API key not configured"):
            get_groq_client()

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai(self, mock_get_client):
        """Test making request to Groq"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "llama-3.3-70b-versatile"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_groq_request_openai(messages, "llama-3.3-70b-versatile")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai_with_kwargs(self, mock_get_client):
        """Test making request to Groq with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_groq_request_openai(
            messages, "llama-3.3-70b-versatile", max_tokens=100, temperature=0.7
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai_error(self, mock_get_client):
        """Test handling errors from Groq"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_groq_request_openai(messages, "llama-3.3-70b-versatile")

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Groq"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_groq_request_openai_stream(messages, "mixtral-8x7b-32768")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="mixtral-8x7b-32768", messages=messages, stream=True
        )

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request to Groq with additional parameters"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_groq_request_openai_stream(messages, "llama-3.1-8b-instant", max_tokens=500)

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="llama-3.1-8b-instant", messages=messages, stream=True, max_tokens=500
        )

    @patch("src.services.groq_client.get_groq_client")
    def test_make_groq_request_openai_stream_error(self, mock_get_client):
        """Test handling streaming errors from Groq"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_groq_request_openai_stream(messages, "llama-3.3-70b-versatile")

    def test_process_groq_response(self):
        """Test processing Groq response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "chatcmpl-test123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.3-70b-versatile"

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

        processed = process_groq_response(mock_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "llama-3.3-70b-versatile"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 15
        assert processed["usage"]["completion_tokens"] == 25
        assert processed["usage"]["total_tokens"] == 40

    def test_process_groq_response_no_usage(self):
        """Test processing Groq response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.3-70b-versatile"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        processed = process_groq_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    def test_process_groq_response_multiple_choices(self):
        """Test processing Groq response with multiple choices"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "llama-3.3-70b-versatile"

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

        processed = process_groq_response(mock_response)

        assert len(processed["choices"]) == 3
        for i, choice in enumerate(processed["choices"]):
            assert choice["index"] == i
            assert choice["message"]["content"] == f"Response {i}"
