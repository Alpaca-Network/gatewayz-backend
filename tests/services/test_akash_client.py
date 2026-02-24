"""Tests for Akash ML client"""

from unittest.mock import Mock, patch

import pytest

from src.services.akash_client import (
    get_akash_client,
    make_akash_request_openai,
    make_akash_request_openai_stream,
    process_akash_response,
)


class TestAkashClient:
    """Test Akash client functionality"""

    @patch("src.services.akash_client.Config.AKASH_API_KEY", "test_key")
    def test_get_akash_client(self):
        """Test getting Akash client"""
        client = get_akash_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == "https://api.akashml.com/v1"

    @patch("src.services.akash_client.Config.AKASH_API_KEY", None)
    def test_get_akash_client_no_key(self):
        """Test getting Akash client without API key"""
        with pytest.raises(ValueError, match="Akash API key not configured"):
            get_akash_client()

    @patch("src.services.akash_client.get_akash_client")
    def test_make_akash_request_openai(self, mock_get_client):
        """Test making request to Akash"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_akash_request_openai(messages, "meta-llama/Llama-3.3-70B-Instruct")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.akash_client.get_akash_client")
    def test_make_akash_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Akash"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_akash_request_openai_stream(messages, "meta-llama/Llama-3.3-70B-Instruct")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="meta-llama/Llama-3.3-70B-Instruct",
            messages=messages,
            stream=True,
        )

    def test_process_akash_response(self):
        """Test processing Akash response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # Mock usage
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30

        processed = process_akash_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "meta-llama/Llama-3.3-70B-Instruct"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_process_akash_response_no_usage(self):
        """Test processing Akash response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # No usage data
        mock_response.usage = None

        processed = process_akash_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    @patch("src.services.akash_client.get_akash_client")
    def test_make_akash_request_with_kwargs(self, mock_get_client):
        """Test making request to Akash with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_akash_request_openai(
            messages,
            "meta-llama/Llama-3.3-70B-Instruct",
            temperature=0.7,
            max_tokens=1024,
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="meta-llama/Llama-3.3-70B-Instruct",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )
