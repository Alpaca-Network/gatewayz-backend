"""
Unit tests for Ranking retry logic.

These tests verify the connection retry functionality without requiring
actual database access.
"""
import pytest
from unittest.mock import Mock, patch
from httpx import RemoteProtocolError, ConnectError, ReadTimeout


class TestExecuteWithConnectionRetry:
    """Test the _execute_with_connection_retry helper function"""

    def test_retry_on_remote_protocol_error(self):
        """Test that RemoteProtocolError triggers retry"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RemoteProtocolError("ConnectionTerminated")
            return "success"

        result = _execute_with_connection_retry(
            operation=operation,
            operation_name="test_operation",
            max_retries=3,
            initial_delay=0.01,  # Fast for testing
        )

        assert result == "success"
        assert call_count == 3

    def test_retry_on_connect_error(self):
        """Test that ConnectError triggers retry"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectError("Connection refused")
            return "success"

        result = _execute_with_connection_retry(
            operation=operation,
            operation_name="test_operation",
            max_retries=3,
            initial_delay=0.01,
        )

        assert result == "success"
        assert call_count == 2

    def test_retry_on_read_timeout(self):
        """Test that ReadTimeout triggers retry"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ReadTimeout("Read timed out")
            return "success"

        result = _execute_with_connection_retry(
            operation=operation,
            operation_name="test_operation",
            max_retries=3,
            initial_delay=0.01,
        )

        assert result == "success"
        assert call_count == 2

    def test_raises_after_max_retries(self):
        """Test that exception is raised after max retries exceeded"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            raise RemoteProtocolError("ConnectionTerminated")

        with pytest.raises(RemoteProtocolError):
            _execute_with_connection_retry(
                operation=operation,
                operation_name="test_operation",
                max_retries=3,
                initial_delay=0.01,
            )

        # Should attempt initial + 3 retries = 4 total
        assert call_count == 4

    def test_non_retryable_errors_fail_immediately(self):
        """Test that non-transient errors fail immediately without retry"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        with pytest.raises(ValueError):
            _execute_with_connection_retry(
                operation=operation,
                operation_name="test_operation",
                max_retries=3,
                initial_delay=0.01,
            )

        # Should only attempt once since ValueError is not retryable
        assert call_count == 1

    def test_success_on_first_attempt(self):
        """Test that successful operation doesn't trigger any retries"""
        from src.db.ranking import _execute_with_connection_retry

        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            return {"data": "test"}

        result = _execute_with_connection_retry(
            operation=operation,
            operation_name="test_operation",
            max_retries=3,
            initial_delay=0.01,
        )

        assert result == {"data": "test"}
        assert call_count == 1


class TestGetAllLatestModelsRetry:
    """Test get_all_latest_models with retry behavior"""

    @patch('src.db.ranking.get_supabase_client')
    @patch('src.db.ranking.time.sleep')  # Skip actual sleep in tests
    def test_get_all_latest_models_with_retry(self, mock_sleep, mock_get_client):
        """Test that get_all_latest_models retries on connection errors"""
        from src.db.ranking import get_all_latest_models

        call_count = 0
        mock_result = Mock()
        mock_result.data = [
            {"id": 1, "rank": 1, "author": "openai", "name": "gpt-4"}
        ]

        def mock_execute():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RemoteProtocolError("ConnectionTerminated")
            return mock_result

        mock_client = Mock()
        mock_query = Mock()
        mock_query.select.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute = mock_execute
        mock_client.table.return_value = mock_query
        mock_get_client.return_value = mock_client

        result = get_all_latest_models()

        assert len(result) == 1
        assert result[0]["author"] == "openai"
        assert call_count == 2  # Failed once, succeeded on retry

    @patch('src.db.ranking.get_supabase_client')
    def test_get_all_latest_models_generates_logo_urls(self, mock_get_client):
        """Test that logo URLs are generated for models without them"""
        from src.db.ranking import get_all_latest_models

        mock_result = Mock()
        mock_result.data = [
            {"id": 1, "rank": 1, "author": "openai", "name": "gpt-4"},
            {"id": 2, "rank": 2, "author": "anthropic", "name": "claude"},
        ]

        mock_client = Mock()
        mock_query = Mock()
        mock_query.select.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute.return_value = mock_result
        mock_client.table.return_value = mock_query
        mock_get_client.return_value = mock_client

        result = get_all_latest_models()

        assert len(result) == 2
        assert "logo_url" in result[0]
        assert "openai.com" in result[0]["logo_url"]
        assert "anthropic.com" in result[1]["logo_url"]


class TestGetAllLatestAppsRetry:
    """Test get_all_latest_apps with retry behavior"""

    @patch('src.db.ranking.get_supabase_client')
    @patch('src.db.ranking.time.sleep')  # Skip actual sleep in tests
    def test_get_all_latest_apps_with_retry(self, mock_sleep, mock_get_client):
        """Test that get_all_latest_apps retries on connection errors"""
        from src.db.ranking import get_all_latest_apps

        call_count = 0
        mock_result = Mock()
        mock_result.data = [{"id": 1, "name": "App1"}]

        def mock_execute():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RemoteProtocolError("ConnectionTerminated")
            return mock_result

        mock_client = Mock()
        mock_query = Mock()
        mock_query.select.return_value = mock_query
        mock_query.execute = mock_execute
        mock_client.table.return_value = mock_query
        mock_get_client.return_value = mock_client

        result = get_all_latest_apps()

        assert len(result) == 1
        assert result[0]["name"] == "App1"
        assert call_count == 2  # Failed once, succeeded on retry
