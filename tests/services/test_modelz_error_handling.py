"""
Tests for Modelz API 522 error handling improvements

Ensures that temporary server errors (502, 503, 522) are logged at WARNING level
instead of ERROR level, as these are expected when the provider is temporarily down.
"""

import pytest
from unittest.mock import MagicMock, patch
import httpx

from src.services.modelz_client import get_modelz_tokens, fetch_models_from_modelz


class TestModelzErrorHandling:
    """Test improved error handling for Modelz API errors"""

    @patch("src.services.modelz_client.httpx.get")
    @patch("src.services.modelz_client.logger")
    def test_522_error_logged_as_warning(self, mock_logger, mock_httpx_get):
        """Test that 522 errors are logged at WARNING level, not ERROR"""
        # Mock 522 response (CloudFlare origin server down)
        mock_response = MagicMock()
        mock_response.status_code = 522
        mock_response.text = "Origin server connection timed out"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "522 Server Error", request=MagicMock(), response=mock_response
        )

        mock_httpx_get.return_value = mock_response

        # Call should raise HTTPException
        with pytest.raises(Exception):
            get_modelz_tokens()

        # Verify warning was logged (not error)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert "522" in warning_message
        assert "temporarily unavailable" in warning_message.lower()

    @patch("src.services.modelz_client.httpx.get")
    @patch("src.services.modelz_client.logger")
    def test_502_error_logged_as_warning(self, mock_logger, mock_httpx_get):
        """Test that 502 errors are logged at WARNING level"""
        # Mock 502 response (Bad Gateway)
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "502 Server Error", request=MagicMock(), response=mock_response
        )

        mock_httpx_get.return_value = mock_response

        # Call should raise HTTPException
        with pytest.raises(Exception):
            get_modelz_tokens()

        # Verify warning was logged (not error)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert "502" in warning_message

    @patch("src.services.modelz_client.httpx.get")
    @patch("src.services.modelz_client.logger")
    def test_503_error_logged_as_warning(self, mock_logger, mock_httpx_get):
        """Test that 503 errors are logged at WARNING level"""
        # Mock 503 response (Service Unavailable)
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Temporarily Unavailable"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503 Server Error", request=MagicMock(), response=mock_response
        )

        mock_httpx_get.return_value = mock_response

        # Call should raise HTTPException
        with pytest.raises(Exception):
            get_modelz_tokens()

        # Verify warning was logged (not error)
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert "503" in warning_message

    @patch("src.services.modelz_client.httpx.get")
    @patch("src.services.modelz_client.logger")
    def test_other_http_errors_logged_as_error(self, mock_logger, mock_httpx_get):
        """Test that non-temporary errors (401, 404, etc) are still logged at ERROR level"""
        # Mock 401 response (Unauthorized)
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Client Error", request=MagicMock(), response=mock_response
        )

        mock_httpx_get.return_value = mock_response

        # Call should raise HTTPException
        with pytest.raises(Exception):
            get_modelz_tokens()

        # Verify error was logged (not warning)
        mock_logger.error.assert_called_once()
        error_message = mock_logger.error.call_args[0][0]
        assert "401" in error_message

    @patch("src.services.modelz_client.httpx.get")
    @patch("src.services.modelz_client.logger")
    async def test_fetch_models_handles_522_gracefully(
        self, mock_logger, mock_httpx_get
    ):
        """Test that fetch_models_from_modelz handles 522 errors gracefully"""
        # Mock 522 response
        mock_response = MagicMock()
        mock_response.status_code = 522
        mock_response.text = "Origin server connection timed out"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "522 Server Error", request=MagicMock(), response=mock_response
        )

        mock_httpx_get.return_value = mock_response

        # Call should return empty list (not crash)
        result = await fetch_models_from_modelz()

        assert result == []

        # Verify warning was logged
        mock_logger.warning.assert_called()
        warning_calls = [call[0][0] for call in mock_logger.warning.call_args_list]
        assert any("522" in msg for msg in warning_calls)
