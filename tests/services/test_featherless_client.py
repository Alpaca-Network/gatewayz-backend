"""Unit tests for Featherless client"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os


@pytest.fixture
def mock_featherless_api_key():
    """Mock the Featherless API key"""
    with patch('src.services.featherless_client.Config') as mock_config:
        mock_config.FEATHERLESS_API_KEY = "test_featherless_key_123"
        yield mock_config


@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI-style response"""
    mock_response = Mock()
    mock_response.id = "chatcmpl-test123"
    mock_response.object = "chat.completion"
    mock_response.created = 1234567890
    mock_response.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    # Mock choice
    mock_choice = Mock()
    mock_choice.index = 0
    mock_choice.message = Mock()
    mock_choice.message.role = "assistant"
    mock_choice.message.content = "Hello! How can I help you?"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    # Mock usage
    mock_response.usage = Mock()
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 8
    mock_response.usage.total_tokens = 18

    return mock_response


@pytest.fixture
def mock_openai_response_no_usage():
    """Create a mock response without usage data"""
    mock_response = Mock()
    mock_response.id = "chatcmpl-test123"
    mock_response.object = "chat.completion"
    mock_response.created = 1234567890
    mock_response.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    mock_choice = Mock()
    mock_choice.index = 0
    mock_choice.message = Mock()
    mock_choice.message.role = "assistant"
    mock_choice.message.content = "Test response"
    mock_choice.finish_reason = "stop"
    mock_response.choices = [mock_choice]

    mock_response.usage = None

    return mock_response


class TestGetFeatherlessClient:
    """Test get_featherless_client function"""

    def test_get_featherless_client_success(self, mock_featherless_api_key):
        """Test successful client initialization"""
        from src.services.featherless_client import get_featherless_client

        mock_client = Mock()
        with patch('src.services.featherless_client.get_featherless_pooled_client') as mock_get_pooled:
            mock_get_pooled.return_value = mock_client

            client = get_featherless_client()

            assert client is mock_client
            mock_get_pooled.assert_called_once()

    def test_get_featherless_client_missing_key(self):
        """Test client initialization with missing API key"""
        from src.services.featherless_client import get_featherless_client

        with patch('src.services.featherless_client.Config') as mock_config:
            mock_config.FEATHERLESS_API_KEY = None

            with pytest.raises(ValueError, match="Featherless API key not configured"):
                get_featherless_client()


class TestMakeFeatherlessRequest:
    """Test make_featherless_request_openai function"""

    def test_make_featherless_request_openai_forwards_args(
        self, mock_featherless_api_key, mock_openai_response
    ):
        """Test that request forwards all arguments correctly"""
        from src.services.featherless_client import make_featherless_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_openai_response
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            response = make_featherless_request_openai(
                messages=messages,
                model=model,
                max_tokens=100,
                temperature=0.7
            )

            assert response == mock_openai_response
            mock_completions.create.assert_called_once_with(
                model=model,
                messages=messages,
                max_tokens=100,
                temperature=0.7
            )

    def test_make_featherless_request_openai_error(self, mock_featherless_api_key):
        """Test request error handling"""
        from src.services.featherless_client import make_featherless_request_openai

        messages = [{"role": "user", "content": "Hello"}]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception, match="API error"):
                make_featherless_request_openai(messages=messages, model=model)


class TestMakeFeatherlessRequestStream:
    """Test make_featherless_request_openai_stream function"""

    def test_make_featherless_request_openai_stream(self, mock_featherless_api_key):
        """Test streaming request"""
        from src.services.featherless_client import make_featherless_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        mock_stream = Mock()

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_stream
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            stream = make_featherless_request_openai_stream(
                messages=messages,
                model=model,
                max_tokens=100
            )

            assert stream == mock_stream
            mock_completions.create.assert_called_once_with(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=100
            )

    def test_make_featherless_request_openai_stream_error(self, mock_featherless_api_key):
        """Test streaming request error handling"""
        from src.services.featherless_client import make_featherless_request_openai_stream

        messages = [{"role": "user", "content": "Hello"}]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_client.chat.completions.create.side_effect = Exception("Streaming error")
            mock_get_client.return_value = mock_client

            with pytest.raises(Exception, match="Streaming error"):
                make_featherless_request_openai_stream(messages=messages, model=model)


class TestProcessFeatherlessResponse:
    """Test process_featherless_response function"""

    def test_process_featherless_response_happy(self, mock_openai_response):
        """Test processing a normal response"""
        from src.services.featherless_client import process_featherless_response

        processed = process_featherless_response(mock_openai_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["created"] == 1234567890
        assert processed["model"] == "meta-llama/Meta-Llama-3.1-8B-Instruct"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["role"] == "assistant"
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 10
        assert processed["usage"]["completion_tokens"] == 8
        assert processed["usage"]["total_tokens"] == 18

    def test_process_featherless_response_no_usage(self, mock_openai_response_no_usage):
        """Test processing response without usage data"""
        from src.services.featherless_client import process_featherless_response

        processed = process_featherless_response(mock_openai_response_no_usage)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["model"] == "meta-llama/Meta-Llama-3.1-8B-Instruct"
        assert len(processed["choices"]) == 1
        assert processed["usage"] == {}

    def test_process_featherless_response_error(self):
        """Test processing error handling"""
        from src.services.featherless_client import process_featherless_response

        bad_response = Mock()
        bad_response.id = None  # This will cause an error

        # The function doesn't explicitly handle errors, so it should raise
        with pytest.raises(Exception):
            process_featherless_response(bad_response)


class TestSanitizeMessagesForFeatherless:
    """Test _sanitize_messages_for_featherless function"""

    def test_sanitize_removes_null_tool_calls(self):
        """Test that null tool_calls are removed to prevent Featherless 422 errors"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!", "tool_calls": None},
            {"role": "user", "content": "How are you?"},
        ]

        sanitized = _sanitize_messages_for_featherless(messages)

        assert len(sanitized) == 3
        assert sanitized[0] == {"role": "user", "content": "Hello"}
        # tool_calls: None should be removed
        assert "tool_calls" not in sanitized[1]
        assert sanitized[1] == {"role": "assistant", "content": "Hi there!"}
        assert sanitized[2] == {"role": "user", "content": "How are you?"}

    def test_sanitize_preserves_valid_tool_calls(self):
        """Test that valid tool_calls arrays are preserved"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        tool_calls = [
            {"id": "call_123", "type": "function", "function": {"name": "test", "arguments": "{}"}}
        ]
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": None, "tool_calls": tool_calls},
        ]

        sanitized = _sanitize_messages_for_featherless(messages)

        assert len(sanitized) == 2
        assert sanitized[1]["tool_calls"] == tool_calls

    def test_sanitize_removes_null_tool_call_id(self):
        """Test that null tool_call_id is removed"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {"role": "tool", "content": "result", "tool_call_id": None},
        ]

        sanitized = _sanitize_messages_for_featherless(messages)

        assert len(sanitized) == 1
        assert "tool_call_id" not in sanitized[0]
        assert sanitized[0] == {"role": "tool", "content": "result"}

    def test_sanitize_preserves_valid_tool_call_id(self):
        """Test that valid tool_call_id is preserved"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {"role": "tool", "content": "result", "tool_call_id": "call_123"},
        ]

        sanitized = _sanitize_messages_for_featherless(messages)

        assert len(sanitized) == 1
        assert sanitized[0]["tool_call_id"] == "call_123"

    def test_sanitize_does_not_mutate_original(self):
        """Test that original messages are not mutated"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        original_messages = [
            {"role": "assistant", "content": "Hi", "tool_calls": None},
        ]
        original_copy = [msg.copy() for msg in original_messages]

        _sanitize_messages_for_featherless(original_messages)

        # Original should be unchanged
        assert original_messages == original_copy

    def test_sanitize_empty_list(self):
        """Test that empty list is handled"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        sanitized = _sanitize_messages_for_featherless([])
        assert sanitized == []

    def test_sanitize_preserves_all_other_fields(self):
        """Test that all non-null fields are preserved"""
        from src.services.featherless_client import _sanitize_messages_for_featherless

        messages = [
            {
                "role": "assistant",
                "content": "Response",
                "name": "assistant_name",
                "tool_calls": None,
                "custom_field": "preserved",
            },
        ]

        sanitized = _sanitize_messages_for_featherless(messages)

        assert sanitized[0]["role"] == "assistant"
        assert sanitized[0]["content"] == "Response"
        assert sanitized[0]["name"] == "assistant_name"
        assert sanitized[0]["custom_field"] == "preserved"
        assert "tool_calls" not in sanitized[0]


class TestMakeFeatherlessRequestWithSanitization:
    """Test that requests use sanitized messages"""

    def test_make_request_sanitizes_messages(self, mock_featherless_api_key, mock_openai_response):
        """Test that make_featherless_request_openai sanitizes messages"""
        from src.services.featherless_client import make_featherless_request_openai

        # Messages with null tool_calls that would cause Featherless to return 422
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi", "tool_calls": None},
        ]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_openai_response
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            make_featherless_request_openai(messages=messages, model=model)

            # Check that the call used sanitized messages (without null tool_calls)
            call_args = mock_completions.create.call_args
            called_messages = call_args.kwargs.get('messages') or call_args[1].get('messages')

            # The second message should not have tool_calls
            assert "tool_calls" not in called_messages[1]

    def test_make_stream_request_sanitizes_messages(self, mock_featherless_api_key):
        """Test that make_featherless_request_openai_stream sanitizes messages"""
        from src.services.featherless_client import make_featherless_request_openai_stream

        # Messages with null tool_calls
        messages = [
            {"role": "assistant", "content": "Hi", "tool_calls": None},
        ]
        model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        with patch('src.services.featherless_client.get_featherless_client') as mock_get_client:
            mock_client = Mock()
            mock_stream = Mock()
            mock_completions = Mock()
            mock_completions.create.return_value = mock_stream
            mock_client.chat.completions = mock_completions
            mock_get_client.return_value = mock_client

            make_featherless_request_openai_stream(messages=messages, model=model)

            # Check that the call used sanitized messages
            call_args = mock_completions.create.call_args
            called_messages = call_args.kwargs.get('messages') or call_args[1].get('messages')

            # The message should not have tool_calls
            assert "tool_calls" not in called_messages[0]
