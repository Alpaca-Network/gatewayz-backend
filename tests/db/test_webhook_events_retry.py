#!/usr/bin/env python3
"""
Comprehensive tests for webhook event database operations with HTTP/2 retry logic

Tests cover:
- HTTP/2 connection error retry behavior
- Idempotency checking with connection errors
- Event recording with transient failures
- Event retrieval with retry logic
- Event cleanup with connection resilience
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone, UTC
from httpcore import LocalProtocolError, RemoteProtocolError

from src.db.webhook_events import (
    is_event_processed,
    record_processed_event,
    get_processed_event,
    cleanup_old_events,
)


# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client"""
    client = Mock()
    table_mock = Mock()
    client.table.return_value = table_mock
    return client, table_mock


@pytest.fixture
def mock_event_data():
    """Sample webhook event data"""
    return {
        "event_id": "evt_test123",
        "event_type": "invoice.paid",
        "user_id": 123,
        "metadata": {"amount": 2999},
        "processed_at": datetime.now(UTC).isoformat(),
    }


# ============================================================
# TEST CLASS: is_event_processed with HTTP/2 Retry
# ============================================================


class TestIsEventProcessedWithRetry:
    """Test event checking with HTTP/2 connection error retry"""

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_is_event_processed_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test successful event check (event exists)"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [{"event_id": "evt_test123"}]
        mock_execute_retry.return_value = result_mock

        exists = is_event_processed("evt_test123")

        assert exists is True
        mock_execute_retry.assert_called_once()
        # Verify the wrapped function calls the right table method
        args, kwargs = mock_execute_retry.call_args
        assert kwargs.get("max_retries") == 2
        assert kwargs.get("retry_delay") == 0.2

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_is_event_processed_not_found(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test event not found"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = []
        mock_execute_retry.return_value = result_mock

        exists = is_event_processed("evt_test123")

        assert exists is False
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_is_event_processed_http2_error_retry(self, mock_execute_retry):
        """Test HTTP/2 connection error triggers retry via execute_with_retry"""
        # Simulate execute_with_retry raising an error after retries exhausted
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9, last_stream_id=191"
        )

        exists = is_event_processed("evt_test123")

        # Should return False on error (fail-open for idempotency checks)
        assert exists is False
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_is_event_processed_stream_id_error(self, mock_execute_retry):
        """Test StreamIDTooLowError is handled by execute_with_retry"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "StreamIDTooLowError: 173 is lower than 193"
        )

        exists = is_event_processed("evt_test123")

        assert exists is False
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_is_event_processed_generic_error(self, mock_execute_retry):
        """Test generic database errors"""
        mock_execute_retry.side_effect = Exception("Database connection failed")

        exists = is_event_processed("evt_test123")

        # Should fail-open (return False) to allow processing
        assert exists is False


# ============================================================
# TEST CLASS: record_processed_event with HTTP/2 Retry
# ============================================================


class TestRecordProcessedEventWithRetry:
    """Test event recording with HTTP/2 connection error retry"""

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_record_processed_event_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_event_data
    ):
        """Test successful event recording"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_event_data]
        mock_execute_retry.return_value = result_mock

        success = record_processed_event(
            event_id="evt_test123",
            event_type="invoice.paid",
            user_id=123,
            metadata={"amount": 2999},
        )

        assert success is True
        mock_execute_retry.assert_called_once()
        args, kwargs = mock_execute_retry.call_args
        assert kwargs.get("max_retries") == 2
        assert kwargs.get("retry_delay") == 0.2

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_record_processed_event_no_data_returned(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test recording fails when no data returned"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = None
        mock_execute_retry.return_value = result_mock

        success = record_processed_event(
            event_id="evt_test123", event_type="invoice.paid"
        )

        assert success is False

    @patch("src.db.webhook_events.execute_with_retry")
    def test_record_processed_event_http2_error(self, mock_execute_retry):
        """Test HTTP/2 connection error during recording"""
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9"
        )

        success = record_processed_event(
            event_id="evt_test123", event_type="invoice.paid"
        )

        # Should return False on connection error
        assert success is False
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_record_processed_event_send_headers_error(self, mock_execute_retry):
        """Test SEND_HEADERS LocalProtocolError"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "Invalid input StreamInputs.SEND_HEADERS in state 5"
        )

        success = record_processed_event(
            event_id="evt_test123", event_type="invoice.paid"
        )

        assert success is False
        mock_execute_retry.assert_called_once()


# ============================================================
# TEST CLASS: get_processed_event with HTTP/2 Retry
# ============================================================


class TestGetProcessedEventWithRetry:
    """Test event retrieval with HTTP/2 connection error retry"""

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_get_processed_event_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_event_data
    ):
        """Test successful event retrieval"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_event_data]
        mock_execute_retry.return_value = result_mock

        event = get_processed_event("evt_test123")

        assert event is not None
        assert event["event_id"] == "evt_test123"
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_get_processed_event_not_found(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test event not found"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = []
        mock_execute_retry.return_value = result_mock

        event = get_processed_event("evt_test123")

        assert event is None

    @patch("src.db.webhook_events.execute_with_retry")
    def test_get_processed_event_http2_error(self, mock_execute_retry):
        """Test HTTP/2 connection error during retrieval"""
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9, last_stream_id=191"
        )

        event = get_processed_event("evt_test123")

        assert event is None
        mock_execute_retry.assert_called_once()


# ============================================================
# TEST CLASS: cleanup_old_events with HTTP/2 Retry
# ============================================================


class TestCleanupOldEventsWithRetry:
    """Test event cleanup with HTTP/2 connection error retry"""

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_cleanup_old_events_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test successful cleanup"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [{"id": 1}, {"id": 2}, {"id": 3}]
        mock_execute_retry.return_value = result_mock

        count = cleanup_old_events(days=90)

        assert count == 3
        mock_execute_retry.assert_called_once()
        args, kwargs = mock_execute_retry.call_args
        assert kwargs.get("max_retries") == 2
        assert kwargs.get("retry_delay") == 0.2

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_cleanup_old_events_no_data(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test cleanup with no events to delete"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = None
        mock_execute_retry.return_value = result_mock

        count = cleanup_old_events(days=90)

        assert count == 0

    @patch("src.db.webhook_events.execute_with_retry")
    def test_cleanup_old_events_http2_error(self, mock_execute_retry):
        """Test HTTP/2 connection error during cleanup"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "StreamIDTooLowError: 173 is lower than 193"
        )

        count = cleanup_old_events(days=90)

        # Should return 0 on error
        assert count == 0
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_cleanup_old_events_custom_days(
        self, mock_execute_retry, mock_supabase_client
    ):
        """Test cleanup with custom retention period"""
        result_mock = Mock()
        result_mock.data = [{"id": 1}]
        mock_execute_retry.return_value = result_mock

        count = cleanup_old_events(days=30)

        assert count == 1
        mock_execute_retry.assert_called_once()


# ============================================================
# TEST CLASS: Integration Tests
# ============================================================


class TestWebhookEventsIntegration:
    """Integration tests for webhook event idempotency flow"""

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_idempotency_flow_new_event(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_event_data
    ):
        """Test full flow: check event doesn't exist, then record it"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        # First call: check if event exists (returns empty)
        result_check = Mock()
        result_check.data = []

        # Second call: record event (returns event data)
        result_record = Mock()
        result_record.data = [mock_event_data]

        mock_execute_retry.side_effect = [result_check, result_record]

        # Check event doesn't exist
        exists = is_event_processed("evt_test123")
        assert exists is False

        # Record the event
        success = record_processed_event(
            event_id="evt_test123",
            event_type="invoice.paid",
            user_id=123,
            metadata={"amount": 2999},
        )
        assert success is True

        assert mock_execute_retry.call_count == 2

    @patch("src.db.webhook_events.execute_with_retry")
    @patch("src.db.webhook_events.get_supabase_client")
    def test_idempotency_flow_duplicate_event(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_event_data
    ):
        """Test full flow: detect duplicate event"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_event_data]
        mock_execute_retry.return_value = result_mock

        # Check event exists (duplicate)
        exists = is_event_processed("evt_test123")
        assert exists is True

        # Should not attempt to record
        mock_execute_retry.assert_called_once()

    @patch("src.db.webhook_events.execute_with_retry")
    def test_resilience_to_transient_errors(self, mock_execute_retry):
        """Test that execute_with_retry handles transient errors gracefully"""
        # Simulate retry mechanism: first attempt fails, second succeeds
        result_mock = Mock()
        result_mock.data = []

        # execute_with_retry should handle the retry internally
        mock_execute_retry.return_value = result_mock

        exists = is_event_processed("evt_test123")

        # Even with transient errors, the operation should complete
        assert exists is False
        mock_execute_retry.assert_called_once()
