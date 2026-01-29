"""
Tests for API key configuration warning improvements

Ensures that missing API keys for optional providers (OpenAI, Anthropic)
are logged at DEBUG level instead of ERROR level, as these providers are optional.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.models import fetch_models_from_openai, fetch_models_from_anthropic


class TestApiKeyWarnings:
    """Test that optional API key warnings are logged at DEBUG level"""

    @patch("src.services.models.Config")
    @patch("src.services.models.logger")
    def test_missing_openai_key_logged_as_debug(self, mock_logger, mock_config):
        """Test that missing OpenAI API key is logged at DEBUG level, not ERROR"""
        # Mock missing API key
        mock_config.OPENAI_API_KEY = None

        # Call should return None
        result = fetch_models_from_openai()

        assert result is None

        # Verify debug was logged (not error)
        mock_logger.debug.assert_called_once()
        debug_message = mock_logger.debug.call_args[0][0]
        assert "OpenAI API key not configured" in debug_message
        assert "skipping" in debug_message.lower()

        # Verify error was NOT logged
        mock_logger.error.assert_not_called()

    @patch("src.services.models.Config")
    @patch("src.services.models.logger")
    def test_missing_anthropic_key_logged_as_debug(self, mock_logger, mock_config):
        """Test that missing Anthropic API key is logged at DEBUG level, not ERROR"""
        # Mock missing API key
        mock_config.ANTHROPIC_API_KEY = None

        # Call should return None
        result = fetch_models_from_anthropic()

        assert result is None

        # Verify debug was logged (not error)
        mock_logger.debug.assert_called_once()
        debug_message = mock_logger.debug.call_args[0][0]
        assert "Anthropic API key not configured" in debug_message
        assert "skipping" in debug_message.lower()

        # Verify error was NOT logged
        mock_logger.error.assert_not_called()

    @patch("src.services.models.Config")
    @patch("src.services.models.httpx.get")
    @patch("src.services.models.logger")
    def test_openai_with_valid_key_proceeds(self, mock_logger, mock_httpx, mock_config):
        """Test that with valid OpenAI key, function proceeds normally"""
        # Mock valid API key
        mock_config.OPENAI_API_KEY = "sk-test-key"

        # Mock successful API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_httpx.return_value = mock_response

        # Call should not log debug about missing key
        result = fetch_models_from_openai()

        # Verify debug about missing key was NOT logged
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        assert not any(
            "OpenAI API key not configured" in msg for msg in debug_calls
        )

    @patch("src.services.models.Config")
    @patch("src.services.models.httpx.get")
    @patch("src.services.models.logger")
    def test_anthropic_with_valid_key_proceeds(
        self, mock_logger, mock_httpx, mock_config
    ):
        """Test that with valid Anthropic key, function proceeds normally"""
        # Mock valid API key
        mock_config.ANTHROPIC_API_KEY = "sk-ant-test-key"

        # Mock successful API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "has_more": False}
        mock_httpx.return_value = mock_response

        # Call should not log debug about missing key
        result = fetch_models_from_anthropic()

        # Verify debug about missing key was NOT logged
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        assert not any(
            "Anthropic API key not configured" in msg for msg in debug_calls
        )
