"""Tests for unified chat schemas"""

import pytest
from pydantic import ValidationError

from src.schemas.unified_chat import UnifiedChatRequest


class TestUnifiedChatRequest:
    """Test unified chat request schema"""

    def test_openai_format_valid(self):
        """Test valid OpenAI format request"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}]
        )
        assert req.model == "gpt-4"
        assert len(req.get_normalized_messages()) == 1

    def test_anthropic_format_valid(self):
        """Test valid Anthropic format request"""
        req = UnifiedChatRequest(
            model="claude-3-opus",
            system="You are helpful",
            messages=[{"role": "user", "content": "Hello"}]
        )

        # System should be prepended to messages
        normalized = req.get_normalized_messages()
        assert len(normalized) == 2
        assert normalized[0]["role"] == "system"
        assert normalized[0]["content"] == "You are helpful"

    def test_anthropic_format_with_existing_system_message(self):
        """Test Anthropic format doesn't duplicate system message"""
        req = UnifiedChatRequest(
            model="claude-3-opus",
            system="You are helpful",
            messages=[
                {"role": "system", "content": "Existing system"},
                {"role": "user", "content": "Hello"}
            ]
        )

        # Should not prepend system if already exists
        normalized = req.get_normalized_messages()
        assert len(normalized) == 2
        assert normalized[0]["content"] == "Existing system"

    def test_responses_format_valid(self):
        """Test valid Responses API format request"""
        req = UnifiedChatRequest(
            model="gpt-4",
            input=[{"role": "user", "content": "Hello"}],
            response_format={"type": "json_object"}
        )
        assert req.input is not None
        assert req.response_format is not None

    def test_no_messages_or_input_raises(self):
        """Test that missing messages/input raises validation error"""
        with pytest.raises(ValidationError):
            UnifiedChatRequest(model="gpt-4")

    def test_optional_params_extraction(self):
        """Test extraction of optional parameters"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.7,
            max_tokens=1000,
            top_p=0.9
        )

        params = req.get_optional_params()
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 1000
        assert params["top_p"] == 0.9

    def test_optional_params_excludes_none(self):
        """Test that None values are excluded from optional params"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.7
            # max_tokens not provided (None)
        )

        params = req.get_optional_params()
        assert "temperature" in params
        assert "max_tokens" not in params

    def test_tools_parameter(self):
        """Test function calling tools parameter"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {}
                }
            }]
        )

        params = req.get_optional_params()
        assert "tools" in params
        assert len(params["tools"]) == 1

    def test_stop_sequences_mapped_to_stop(self):
        """Test that stop_sequences is mapped to stop parameter"""
        req = UnifiedChatRequest(
            model="claude-3-opus",
            messages=[{"role": "user", "content": "Hello"}],
            stop_sequences=["\n\n", "END"]
        )

        params = req.get_optional_params()
        assert "stop" in params
        assert params["stop"] == ["\n\n", "END"]

    def test_response_format_parameter(self):
        """Test response_format parameter"""
        req = UnifiedChatRequest(
            model="gpt-4",
            input=[{"role": "user", "content": "Hello"}],
            response_format={"type": "json_object"}
        )

        params = req.get_optional_params()
        assert "response_format" in params
        assert params["response_format"]["type"] == "json_object"

    def test_stream_defaults_to_false(self):
        """Test that stream defaults to False"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}]
        )

        assert req.stream is False

    def test_stream_can_be_set_to_true(self):
        """Test that stream can be set to True"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            stream=True
        )

        assert req.stream is True

    def test_provider_parameter(self):
        """Test provider parameter"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            provider="openrouter"
        )

        assert req.provider == "openrouter"

    def test_temperature_validation_min(self):
        """Test temperature minimum value validation"""
        with pytest.raises(ValidationError):
            UnifiedChatRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=-0.1  # Below minimum
            )

    def test_temperature_validation_max(self):
        """Test temperature maximum value validation"""
        with pytest.raises(ValidationError):
            UnifiedChatRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                temperature=2.1  # Above maximum
            )

    def test_max_tokens_validation_min(self):
        """Test max_tokens minimum value validation"""
        with pytest.raises(ValidationError):
            UnifiedChatRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=0  # Below minimum
            )

    def test_max_tokens_validation_max(self):
        """Test max_tokens maximum value validation"""
        with pytest.raises(ValidationError):
            UnifiedChatRequest(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=130000  # Above maximum
            )

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed (provider-specific params)"""
        req = UnifiedChatRequest(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            custom_param="custom_value"  # Extra field
        )

        # Should not raise validation error
        assert req.model == "gpt-4"
