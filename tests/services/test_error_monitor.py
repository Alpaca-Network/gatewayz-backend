"""Tests for error monitoring service"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import httpx

from src.services.error_monitor import (
    ErrorMonitor,
    ErrorCategory,
    ErrorSeverity,
    ErrorPattern,
)


@pytest.fixture
def error_monitor():
    """Create an error monitor instance for testing."""
    monitor = ErrorMonitor()
    return monitor


@pytest.fixture
async def initialized_error_monitor():
    """Create and initialize an error monitor instance."""
    monitor = ErrorMonitor()
    await monitor.initialize()
    yield monitor
    await monitor.close()


class TestErrorMonitorLokiFetchErrors:
    """Tests for Loki fetch error handling"""

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_timeout(self, error_monitor, monkeypatch):
        """Test that ReadTimeout is handled gracefully."""
        monkeypatch.setattr(error_monitor, "loki_enabled", True)
        monkeypatch.setattr(error_monitor, "loki_query_url", "http://localhost:3100/loki/api/v1/push")

        # Create a mock session that raises ReadTimeout
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=httpx.ReadTimeout("Timeout after 10s"))
        error_monitor.session = mock_session

        # Should return empty list without raising
        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert result == []
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_http_error(self, error_monitor, monkeypatch):
        """Test that HTTP errors are handled gracefully."""
        monkeypatch.setattr(error_monitor, "loki_enabled", True)
        monkeypatch.setattr(error_monitor, "loki_query_url", "http://localhost:3100/loki/api/v1/push")

        # Create a mock response with 500 error
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        # Create a mock session that raises HTTPStatusError
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server error", request=MagicMock(), response=mock_response
            )
        )
        error_monitor.session = mock_session

        # Should return empty list without raising
        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert result == []
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_connection_error(self, error_monitor, monkeypatch):
        """Test that connection errors are handled gracefully."""
        monkeypatch.setattr(error_monitor, "loki_enabled", True)
        monkeypatch.setattr(error_monitor, "loki_query_url", "http://localhost:3100/loki/api/v1/push")

        # Create a mock session that raises ConnectError
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        error_monitor.session = mock_session

        # Should return empty list without raising
        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert result == []
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_generic_exception(self, error_monitor, monkeypatch):
        """Test that generic exceptions are handled gracefully."""
        monkeypatch.setattr(error_monitor, "loki_enabled", True)
        monkeypatch.setattr(error_monitor, "loki_query_url", "http://localhost:3100/loki/api/v1/push")

        # Create a mock session that raises a generic exception
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=ValueError("Unexpected error"))
        error_monitor.session = mock_session

        # Should return empty list without raising
        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert result == []
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_not_enabled(self, error_monitor, monkeypatch):
        """Test that fetch returns empty list when Loki is not enabled."""
        monkeypatch.setattr(error_monitor, "loki_enabled", False)

        await error_monitor.initialize()
        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert result == []
        await error_monitor.close()

    @pytest.mark.asyncio
    async def test_fetch_recent_errors_success(self, error_monitor, monkeypatch):
        """Test successful fetch from Loki."""
        monkeypatch.setattr(error_monitor, "loki_enabled", True)
        monkeypatch.setattr(error_monitor, "loki_query_url", "http://localhost:3100/loki/api/v1/push")

        # Create a mock response with valid data
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "stream": {"level": "ERROR"},
                        "values": [
                            ["1234567890000000000", '{"message": "Test error 1", "level": "ERROR"}'],
                            ["1234567891000000000", '{"message": "Test error 2", "level": "ERROR"}'],
                        ],
                    }
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        # Create a mock session that returns the response
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        error_monitor.session = mock_session

        result = await error_monitor.fetch_recent_errors(hours=1, limit=100)

        assert len(result) == 2
        assert result[0]["message"] == "Test error 1"
        assert result[1]["message"] == "Test error 2"
        mock_session.get.assert_called_once()


class TestErrorClassification:
    """Tests for error classification logic"""

    def test_classify_provider_timeout(self, error_monitor):
        """Test classification of provider timeout errors."""
        error_data = {
            "message": "OpenRouter request timed out after 30s",
            "stack_trace": "",
        }
        category, severity = error_monitor.classify_error(error_data)

        assert category == ErrorCategory.PROVIDER_ERROR
        assert severity == ErrorSeverity.HIGH

    def test_classify_database_error(self, error_monitor):
        """Test classification of database errors."""
        error_data = {
            "message": "Supabase connection pool exhausted",
            "stack_trace": "",
        }
        category, severity = error_monitor.classify_error(error_data)

        assert category == ErrorCategory.DATABASE_ERROR
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_rate_limit(self, error_monitor):
        """Test classification of rate limit errors."""
        error_data = {
            "message": "Rate limit exceeded: 429 Too Many Requests",
            "stack_trace": "",
        }
        category, severity = error_monitor.classify_error(error_data)

        assert category == ErrorCategory.RATE_LIMIT_ERROR
        assert severity == ErrorSeverity.MEDIUM

    def test_classify_timeout(self, error_monitor):
        """Test classification of timeout errors."""
        error_data = {
            "message": "Request timeout after 60s",
            "stack_trace": "",
        }
        category, severity = error_monitor.classify_error(error_data)

        assert category == ErrorCategory.TIMEOUT_ERROR
        assert severity == ErrorSeverity.MEDIUM


class TestErrorPatternGrouping:
    """Tests for error pattern grouping"""

    def test_group_similar_errors(self, error_monitor):
        """Test that similar errors are grouped together."""
        timestamp = datetime.now(timezone.utc)

        # Both messages start with same 50 chars, so they should be grouped
        error1 = ErrorPattern(
            error_type="ValueError",
            message="Database connection failed due to timeout on server",
            category=ErrorCategory.DATABASE_ERROR,
            severity=ErrorSeverity.CRITICAL,
            file="db.py",
            line=100,
            function="connect",
            stack_trace="",
            timestamp=timestamp,
        )

        error2 = ErrorPattern(
            error_type="ValueError",
            message="Database connection failed due to authentication error",
            category=ErrorCategory.DATABASE_ERROR,
            severity=ErrorSeverity.CRITICAL,
            file="db.py",
            line=105,
            function="connect",
            stack_trace="",
            timestamp=timestamp,
        )

        grouped = error_monitor.group_similar_errors([error1, error2])

        # Both messages start with "Database connection failed due to " (first 50 chars are similar)
        # So they should be grouped into one pattern
        assert len(grouped) == 1
        pattern = list(grouped.values())[0]
        assert pattern.count == 2
        assert len(pattern.examples) == 2


class TestFixabilitySuggestions:
    """Tests for error fixability detection"""

    def test_timeout_error_fixable(self, error_monitor):
        """Test that timeout errors are detected as fixable."""
        error = ErrorPattern(
            error_type="TimeoutError",
            message="Provider request timed out",
            category=ErrorCategory.TIMEOUT_ERROR,
            severity=ErrorSeverity.MEDIUM,
            file="client.py",
            line=50,
            function="request",
            stack_trace="",
            timestamp=datetime.now(timezone.utc),
        )

        fixable, suggestion = error_monitor.determine_fixability(error)

        assert fixable is True
        assert "retry" in suggestion.lower()
        assert "backoff" in suggestion.lower()

    def test_rate_limit_fixable(self, error_monitor):
        """Test that rate limit errors are detected as fixable."""
        error = ErrorPattern(
            error_type="RateLimitError",
            message="Rate limit exceeded",
            category=ErrorCategory.RATE_LIMIT_ERROR,
            severity=ErrorSeverity.MEDIUM,
            file="api.py",
            line=200,
            function="call_api",
            stack_trace="",
            timestamp=datetime.now(timezone.utc),
        )

        fixable, suggestion = error_monitor.determine_fixability(error)

        assert fixable is True
        assert "backoff" in suggestion.lower() or "queue" in suggestion.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
