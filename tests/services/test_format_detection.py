"""Tests for format detection"""

import pytest
from src.services.format_detection import detect_request_format, validate_format_compatibility, ChatFormat


class TestFormatDetection:
    """Test request format auto-detection"""

    def test_explicit_format_openai(self):
        """Test explicit OpenAI format"""
        data = {"format": "openai", "messages": []}
        assert detect_request_format(data) == ChatFormat.OPENAI

    def test_explicit_format_anthropic(self):
        """Test explicit Anthropic format"""
        data = {"format": "anthropic", "messages": []}
        assert detect_request_format(data) == ChatFormat.ANTHROPIC

    def test_explicit_format_responses(self):
        """Test explicit Responses API format"""
        data = {"format": "responses", "input": []}
        assert detect_request_format(data) == ChatFormat.RESPONSES

    def test_auto_detect_openai(self):
        """Test auto-detection of OpenAI format"""
        data = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        assert detect_request_format(data) == ChatFormat.OPENAI

    def test_auto_detect_anthropic(self):
        """Test auto-detection of Anthropic format"""
        data = {
            "model": "claude-3-opus",
            "system": "You are helpful",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        assert detect_request_format(data) == ChatFormat.ANTHROPIC

    def test_auto_detect_responses(self):
        """Test auto-detection of Responses API format"""
        data = {
            "model": "gpt-4",
            "input": [{"role": "user", "content": "Hello"}],
            "response_format": {"type": "json_object"}
        }
        assert detect_request_format(data) == ChatFormat.RESPONSES

    def test_anthropic_with_stop_sequences(self):
        """Test Anthropic detection with stop_sequences"""
        data = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "stop_sequences": ["\n\n"]
        }
        assert detect_request_format(data) == ChatFormat.ANTHROPIC

    def test_invalid_explicit_format_falls_back_to_auto(self):
        """Test that invalid explicit format falls back to auto-detection"""
        data = {
            "format": "invalid_format",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        # Should fall back to OpenAI (default)
        assert detect_request_format(data) == ChatFormat.OPENAI

    def test_validate_openai_format_success(self):
        """Test validation of valid OpenAI format"""
        data = {"messages": [{"role": "user", "content": "Hello"}]}
        assert validate_format_compatibility(data, ChatFormat.OPENAI) is True

    def test_validate_openai_format_missing_messages(self):
        """Test validation fails for OpenAI format without messages"""
        data = {"model": "gpt-4"}
        with pytest.raises(ValueError, match="OpenAI format requires 'messages' field"):
            validate_format_compatibility(data, ChatFormat.OPENAI)

    def test_validate_anthropic_format_success(self):
        """Test validation of valid Anthropic format"""
        data = {"messages": [{"role": "user", "content": "Hello"}]}
        assert validate_format_compatibility(data, ChatFormat.ANTHROPIC) is True

    def test_validate_responses_format_success(self):
        """Test validation of valid Responses API format"""
        data = {"input": [{"role": "user", "content": "Hello"}]}
        assert validate_format_compatibility(data, ChatFormat.RESPONSES) is True

    def test_validate_responses_format_missing_input(self):
        """Test validation fails for Responses format without input"""
        data = {"model": "gpt-4"}
        with pytest.raises(ValueError, match="Responses API format requires 'input' field"):
            validate_format_compatibility(data, ChatFormat.RESPONSES)
