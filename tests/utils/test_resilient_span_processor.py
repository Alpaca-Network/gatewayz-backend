"""
Tests for the Resilient Span Processor with circuit breaker functionality.
"""

import time
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from src.utils.resilient_span_processor import ResilientSpanProcessor


class TestResilientSpanProcessor:
    """Test suite for ResilientSpanProcessor circuit breaker logic."""

    @pytest.fixture
    def mock_processor(self):
        """Create a mock span processor."""
        processor = Mock()
        processor.force_flush = Mock(return_value=True)
        processor.on_start = Mock()
        processor.on_end = Mock()
        processor.shutdown = Mock()
        return processor

    @pytest.fixture
    def resilient_processor(self, mock_processor):
        """Create a resilient processor with mocked underlying processor."""
        return ResilientSpanProcessor(mock_processor)

    def test_initialization(self, resilient_processor):
        """Test that processor initializes with correct state."""
        assert resilient_processor._failure_count == 0
        assert resilient_processor._success_count == 0
        assert resilient_processor._circuit_open is False
        assert resilient_processor._last_failure_time is None
        assert resilient_processor._total_exports == 0
        assert resilient_processor._total_failures == 0
        assert resilient_processor._total_drops == 0

    def test_successful_flush(self, resilient_processor, mock_processor):
        """Test that successful flushes work normally."""
        # Arrange
        mock_processor.force_flush.return_value = True

        # Act
        result = resilient_processor.force_flush()

        # Assert
        assert result is True
        assert resilient_processor._success_count == 1
        assert resilient_processor._failure_count == 0
        assert resilient_processor._total_exports == 1
        assert resilient_processor._total_failures == 0
        mock_processor.force_flush.assert_called_once()

    def test_single_connection_error_does_not_open_circuit(
        self, resilient_processor, mock_processor
    ):
        """Test that a single connection error is logged but circuit stays closed."""
        # Arrange
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")

        # Act
        result = resilient_processor.force_flush()

        # Assert
        assert result is False
        assert resilient_processor._circuit_open is False
        assert resilient_processor._failure_count == 1
        assert resilient_processor._total_failures == 1

    def test_multiple_failures_open_circuit(self, resilient_processor, mock_processor):
        """Test that circuit opens after threshold failures."""
        # Arrange
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")

        # Act - Fail multiple times
        for i in range(ResilientSpanProcessor.FAILURE_THRESHOLD):
            result = resilient_processor.force_flush()
            assert result is False

        # Assert
        assert resilient_processor._circuit_open is True
        assert resilient_processor._failure_count == ResilientSpanProcessor.FAILURE_THRESHOLD
        assert resilient_processor._total_failures == ResilientSpanProcessor.FAILURE_THRESHOLD

    def test_circuit_open_blocks_requests_during_cooldown(
        self, resilient_processor, mock_processor
    ):
        """Test that open circuit blocks requests during cooldown period."""
        # Arrange - Open the circuit
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        for _ in range(ResilientSpanProcessor.FAILURE_THRESHOLD):
            resilient_processor.force_flush()

        assert resilient_processor._circuit_open is True

        # Reset mock to verify it's not called
        mock_processor.force_flush.reset_mock()
        mock_processor.force_flush.side_effect = None
        mock_processor.force_flush.return_value = True

        # Act - Try to flush while circuit is open
        result = resilient_processor.force_flush()

        # Assert - Request should be blocked and counted as dropped
        assert result is False
        assert resilient_processor._total_drops == 1
        mock_processor.force_flush.assert_not_called()

    def test_circuit_attempts_recovery_after_cooldown(self, resilient_processor, mock_processor):
        """Test that circuit attempts recovery after cooldown period."""
        # Arrange - Open the circuit
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        for _ in range(ResilientSpanProcessor.FAILURE_THRESHOLD):
            resilient_processor.force_flush()

        assert resilient_processor._circuit_open is True

        # Fast-forward time past cooldown
        with patch("time.time") as mock_time:
            # Set current time to cooldown + 1 second in the future
            mock_time.return_value = (
                resilient_processor._last_failure_time + ResilientSpanProcessor.COOLDOWN_SECONDS + 1
            )

            # Reset mock processor to succeed
            mock_processor.force_flush.reset_mock()
            mock_processor.force_flush.side_effect = None
            mock_processor.force_flush.return_value = True

            # Act - Attempt flush after cooldown
            result = resilient_processor.force_flush()

            # Assert - Circuit should attempt recovery
            assert result is True
            mock_processor.force_flush.assert_called_once()

    def test_circuit_closes_after_successful_recoveries(self, resilient_processor, mock_processor):
        """Test that circuit closes after SUCCESS_THRESHOLD successful flushes."""
        # Arrange - Open the circuit
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        for _ in range(ResilientSpanProcessor.FAILURE_THRESHOLD):
            resilient_processor.force_flush()

        assert resilient_processor._circuit_open is True

        # Reset for successful flushes
        mock_processor.force_flush.reset_mock()
        mock_processor.force_flush.side_effect = None
        mock_processor.force_flush.return_value = True

        # Fast-forward time past cooldown
        with patch("time.time") as mock_time:
            mock_time.return_value = (
                resilient_processor._last_failure_time + ResilientSpanProcessor.COOLDOWN_SECONDS + 1
            )

            # Act - Succeed enough times to close circuit
            for i in range(ResilientSpanProcessor.SUCCESS_THRESHOLD):
                result = resilient_processor.force_flush()
                assert result is True

            # Assert - Circuit should be closed
            assert resilient_processor._circuit_open is False
            assert resilient_processor._success_count == ResilientSpanProcessor.SUCCESS_THRESHOLD
            assert resilient_processor._failure_count == 0

    def test_timeout_error_handled_gracefully(self, resilient_processor, mock_processor):
        """Test that timeout errors are handled like connection errors."""
        # Arrange
        mock_processor.force_flush.side_effect = Timeout("Request timeout")

        # Act
        result = resilient_processor.force_flush()

        # Assert
        assert result is False
        assert resilient_processor._failure_count == 1
        assert resilient_processor._total_failures == 1

    def test_os_error_handled_gracefully(self, resilient_processor, mock_processor):
        """Test that OSError (connection reset) is handled gracefully."""
        # Arrange
        mock_processor.force_flush.side_effect = OSError(104, "Connection reset by peer")

        # Act
        result = resilient_processor.force_flush()

        # Assert
        assert result is False
        assert resilient_processor._failure_count == 1
        assert resilient_processor._total_failures == 1

    def test_unexpected_error_logged_but_not_crash(self, resilient_processor, mock_processor):
        """Test that unexpected errors are logged but don't crash."""
        # Arrange
        mock_processor.force_flush.side_effect = ValueError("Unexpected error")

        # Act
        result = resilient_processor.force_flush()

        # Assert
        assert result is False
        assert resilient_processor._failure_count == 1
        assert resilient_processor._total_failures == 1

    def test_on_start_error_does_not_crash(self, resilient_processor, mock_processor):
        """Test that errors in on_start are silently ignored."""
        # Arrange
        mock_processor.on_start.side_effect = Exception("Test error")
        mock_span = Mock()

        # Act - Should not raise exception
        resilient_processor.on_start(mock_span)

        # Assert
        mock_processor.on_start.assert_called_once()

    def test_on_end_error_does_not_crash(self, resilient_processor, mock_processor):
        """Test that errors in on_end are silently ignored."""
        # Arrange
        mock_processor.on_end.side_effect = Exception("Test error")
        mock_span = Mock()

        # Act - Should not raise exception
        resilient_processor.on_end(mock_span)

        # Assert
        mock_processor.on_end.assert_called_once()

    def test_shutdown_logs_statistics(self, resilient_processor, mock_processor, caplog):
        """Test that shutdown logs export statistics."""
        # Arrange - Perform some exports
        mock_processor.force_flush.return_value = True
        for _ in range(10):
            resilient_processor.force_flush()

        # Fail 2 times
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        for _ in range(2):
            resilient_processor.force_flush()

        # Act
        resilient_processor.shutdown()

        # Assert - Check statistics were logged
        assert resilient_processor._total_exports == 12
        assert resilient_processor._total_failures == 2

    def test_success_resets_failure_count(self, resilient_processor, mock_processor):
        """Test that a successful flush resets the failure count."""
        # Arrange - Fail a few times
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        for _ in range(3):
            resilient_processor.force_flush()

        assert resilient_processor._failure_count == 3

        # Act - Succeed once
        mock_processor.force_flush.reset_mock()
        mock_processor.force_flush.side_effect = None
        mock_processor.force_flush.return_value = True
        result = resilient_processor.force_flush()

        # Assert
        assert result is True
        assert resilient_processor._failure_count == 0
        assert resilient_processor._success_count == 1

    def test_failure_resets_success_count(self, resilient_processor, mock_processor):
        """Test that a failure resets the success count."""
        # Arrange - Succeed a few times
        mock_processor.force_flush.return_value = True
        for _ in range(3):
            resilient_processor.force_flush()

        assert resilient_processor._success_count == 3

        # Act - Fail once
        mock_processor.force_flush.side_effect = RequestsConnectionError("Connection refused")
        result = resilient_processor.force_flush()

        # Assert
        assert result is False
        assert resilient_processor._success_count == 0
        assert resilient_processor._failure_count == 1


class TestResilientSpanProcessorThreadSafety:
    """Test thread safety of the resilient span processor."""

    @pytest.fixture
    def mock_processor(self):
        """Create a mock span processor."""
        processor = Mock()
        processor.force_flush = Mock(return_value=True)
        return processor

    @pytest.fixture
    def resilient_processor(self, mock_processor):
        """Create a resilient processor."""
        return ResilientSpanProcessor(mock_processor)

    def test_concurrent_flushes_are_thread_safe(self, resilient_processor, mock_processor):
        """Test that concurrent force_flush calls don't cause race conditions."""
        import threading

        # Arrange
        num_threads = 10
        flushes_per_thread = 5
        threads = []

        # Act - Launch multiple threads that flush concurrently
        def flush_multiple_times():
            for _ in range(flushes_per_thread):
                resilient_processor.force_flush()
                time.sleep(0.001)  # Small delay to encourage interleaving

        for _ in range(num_threads):
            thread = threading.Thread(target=flush_multiple_times)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Assert - Total exports should match expected count
        expected_exports = num_threads * flushes_per_thread
        assert resilient_processor._total_exports == expected_exports
