"""
Tests for error monitor Loki logging improvements.

Tests the fix for suppressing empty Loki errors.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.error_monitor import ErrorMonitor


class TestLokiErrorLogging:
    """Test Loki error logging behavior."""

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_empty_error_not_logged(self, mock_httpx_client):
        """Test that empty Loki errors are suppressed and not logged at ERROR level."""
        monitor = ErrorMonitor()

        # Setup mock client
        mock_session = AsyncMock()
        monitor.session = mock_session
        monitor.loki_enabled = True
        monitor.loki_query_url = "http://loki.example.com/loki/api/v1/query_range"

        # Simulate an empty error
        mock_session.get.side_effect = Exception("")

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            # Should return empty list
            assert errors == []

            # Should NOT have called logger.error (empty error suppressed)
            mock_logger.error.assert_not_called()

            # Should have called logger.debug instead
            mock_logger.debug.assert_called_once_with("Loki fetch returned empty/no response")

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_none_error_not_logged(self, mock_httpx_client):
        """Test that 'None' string errors from Loki are suppressed."""
        monitor = ErrorMonitor()

        mock_session = AsyncMock()
        monitor.session = mock_session
        monitor.loki_enabled = True
        monitor.loki_query_url = "http://loki.example.com/loki/api/v1/query_range"

        # Simulate a "None" error
        mock_session.get.side_effect = Exception("None")

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            assert errors == []
            mock_logger.error.assert_not_called()
            mock_logger.debug.assert_called()

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_real_error_still_logged(self, mock_httpx_client):
        """Test that real Loki errors are still logged at ERROR level."""
        monitor = ErrorMonitor()

        mock_session = AsyncMock()
        monitor.session = mock_session
        monitor.loki_enabled = True
        monitor.loki_query_url = "http://loki.example.com/loki/api/v1/query_range"

        # Simulate a real error
        real_error = Exception("Connection timeout to Loki server")
        mock_session.get.side_effect = real_error

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            assert errors == []

            # Real error SHOULD be logged
            mock_logger.error.assert_called_once()
            error_call_args = str(mock_logger.error.call_args)
            assert "Connection timeout to Loki server" in error_call_args

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_successful_fetch(self, mock_httpx_client):
        """Test that successful Loki fetches work correctly."""
        monitor = ErrorMonitor()

        mock_session = AsyncMock()
        monitor.session = mock_session
        monitor.loki_enabled = True
        monitor.loki_query_url = "http://loki.example.com/loki/api/v1/query_range"

        # Mock successful response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "values": [
                            ["1234567890", '{"message": "Test error", "level": "ERROR"}'],
                            ["1234567891", '{"message": "Another error", "level": "ERROR"}'],
                        ]
                    }
                ]
            }
        }
        mock_session.get.return_value = mock_response

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            # Should return errors
            assert len(errors) == 2
            assert errors[0]["message"] == "Test error"
            assert errors[1]["message"] == "Another error"

            # Should NOT log any errors
            mock_logger.error.assert_not_called()
            mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_disabled_no_error(self, mock_httpx_client):
        """Test that disabled Loki doesn't trigger errors."""
        monitor = ErrorMonitor()

        # Loki disabled
        monitor.loki_enabled = False

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            # Should return empty list
            assert errors == []

            # Should log warning about Loki not being enabled
            mock_logger.warning.assert_called_once_with("Loki not enabled or not initialized")

            # Should NOT log error
            mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.services.error_monitor.httpx.AsyncClient")
    async def test_loki_whitespace_error_suppressed(self, mock_httpx_client):
        """Test that whitespace-only errors are suppressed."""
        monitor = ErrorMonitor()

        mock_session = AsyncMock()
        monitor.session = mock_session
        monitor.loki_enabled = True
        monitor.loki_query_url = "http://loki.example.com/loki/api/v1/query_range"

        # Simulate a whitespace error
        mock_session.get.side_effect = Exception("   \n\t  ")

        with patch("src.services.error_monitor.logger") as mock_logger:
            errors = await monitor.fetch_recent_errors(hours=1, limit=100)

            assert errors == []

            # Whitespace error should be suppressed
            mock_logger.error.assert_not_called()
            mock_logger.debug.assert_called_once()
