"""Tests for Anthropic client.

NOTE: The Anthropic client uses Anthropic's OpenAI-compatible API endpoint
(https://docs.anthropic.com/en/api/openai-sdk), not the native Messages API.
Therefore, tests mock `chat.completions.create` as per the OpenAI SDK interface.
"""

from unittest.mock import Mock, patch

import pytest

from src.services.anthropic_client import (
    get_anthropic_client,
    make_anthropic_request,
    make_anthropic_request_stream,
    process_anthropic_response,
)


class TestAnthropicClient:
    """Test Anthropic client functionality"""

    @patch("src.services.anthropic_client.Config.ANTHROPIC_API_KEY", "test_key")
    def test_get_anthropic_client(self):
        """Test getting Anthropic client"""
        client = get_anthropic_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://api.anthropic.com/v1"

    @patch("src.services.anthropic_client.Config.ANTHROPIC_API_KEY", None)
    def test_get_anthropic_client_no_key(self):
        """Test getting Anthropic client without API key"""
        with pytest.raises(ValueError, match="Anthropic API key not configured"):
            get_anthropic_client()

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request(self, mock_get_client):
        """Test making request to Anthropic"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "claude-3-5-sonnet-20241022"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_anthropic_request(messages, "claude-3-5-sonnet-20241022")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request_with_kwargs(self, mock_get_client):
        """Test making request to Anthropic with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_anthropic_request(
            messages, "claude-3-5-sonnet-20241022", max_tokens=100, temperature=0.7
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="claude-3-5-sonnet-20241022",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request_error(self, mock_get_client):
        """Test handling errors from Anthropic"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_anthropic_request(messages, "claude-3-5-sonnet-20241022")

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request_stream(self, mock_get_client):
        """Test making streaming request to Anthropic"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_anthropic_request_stream(messages, "claude-3-5-haiku-20241022")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="claude-3-5-haiku-20241022", messages=messages, stream=True
        )

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request to Anthropic with additional parameters"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_anthropic_request_stream(messages, "claude-3-opus-20240229", max_tokens=500)

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="claude-3-opus-20240229", messages=messages, stream=True, max_tokens=500
        )

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_make_anthropic_request_stream_error(self, mock_get_client):
        """Test handling streaming errors from Anthropic"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_anthropic_request_stream(messages, "claude-3-5-sonnet-20241022")

    def test_process_anthropic_response(self):
        """Test processing Anthropic response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "chatcmpl-test123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "claude-3-5-sonnet-20241022"

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

        processed = process_anthropic_response(mock_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "claude-3-5-sonnet-20241022"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 15
        assert processed["usage"]["completion_tokens"] == 25
        assert processed["usage"]["total_tokens"] == 40

    def test_process_anthropic_response_no_usage(self):
        """Test processing Anthropic response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "claude-3-5-sonnet-20241022"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        processed = process_anthropic_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    def test_process_anthropic_response_multiple_choices(self):
        """Test processing Anthropic response with multiple choices"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "claude-3-5-sonnet-20241022"

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

        processed = process_anthropic_response(mock_response)

        assert len(processed["choices"]) == 3
        for i, choice in enumerate(processed["choices"]):
            assert choice["index"] == i
            assert choice["message"]["content"] == f"Response {i}"


class TestAnthropicModels:
    """Test Anthropic model-specific functionality"""

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_claude_3_5_sonnet_request(self, mock_get_client):
        """Test making request with Claude 3.5 Sonnet"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "claude-3-5-sonnet-20241022"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Explain quantum computing"}]
        response = make_anthropic_request(messages, "claude-3-5-sonnet-20241022")

        assert response is not None
        assert response.model == "claude-3-5-sonnet-20241022"

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_claude_3_opus_request(self, mock_get_client):
        """Test making request with Claude 3 Opus"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "claude-3-opus-20240229"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Write a poem"}]
        response = make_anthropic_request(messages, "claude-3-opus-20240229")

        assert response is not None
        assert response.model == "claude-3-opus-20240229"

    @patch("src.services.anthropic_client.get_anthropic_client")
    def test_claude_3_haiku_request(self, mock_get_client):
        """Test making request with Claude 3 Haiku"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "claude-3-haiku-20240307"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Quick question"}]
        response = make_anthropic_request(messages, "claude-3-haiku-20240307")

        assert response is not None
        assert response.model == "claude-3-haiku-20240307"
