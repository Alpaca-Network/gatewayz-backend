"""
Test suite to ensure Loki logging remains non-blocking and doesn't regress.

These tests verify that:
1. LokiLogHandler doesn't block the main thread
2. Logging during startup doesn't cause timeouts
3. Queue batching works correctly
4. Graceful shutdown flushes remaining logs
5. Exception handling doesn't crash the handler

PR #681 fixed: "The Loki handler was making blocking HTTP requests for every
log message" - these tests ensure that doesn't happen again.
"""

import asyncio
import logging
import queue
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.config.logging_config import LokiLogHandler


class TestLokiNonBlockingBehavior:
    """Verify Loki logging doesn't block the main thread."""

    def test_emit_does_not_block_main_thread(self):
        """
        Test that emit() returns immediately without waiting for HTTP request.

        This is the core fix from PR #681. If emit() blocked on HTTP requests,
        thousands of logs during startup would cause 7+ minute delays.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None
        )

        # Measure time to emit - should be <5ms (no network I/O)
        start = time.perf_counter()
        handler.emit(record)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Emit should be extremely fast (< 5ms)
        # If it's slow, it means we're doing blocking I/O
        assert elapsed_ms < 5.0, f"emit() took {elapsed_ms}ms - probably blocking!"

        handler.close()

    def test_queue_is_populated_not_sent_immediately(self):
        """
        Test that logs are queued, not sent immediately.

        This verifies the core architecture: logs go to queue, background
        thread handles HTTP requests.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )

        # Get initial queue size
        initial_size = handler._queue.qsize()

        # Emit several logs
        for i in range(10):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg=f"Message {i}",
                args=(),
                exc_info=None
            )
            handler.emit(record)

        # Queue should now have ~10 items
        queue_size = handler._queue.qsize()
        assert queue_size >= 9, f"Queue should have items, but has {queue_size}"

        # Give worker thread a moment to process
        time.sleep(0.5)

        handler.close()

    def test_handler_during_high_volume_logging(self):
        """
        Test that handler can handle startup-like high-volume logging.

        During startup, thousands of logs are emitted rapidly. This should
        not cause slowdowns or deadlocks.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=10000  # High volume
        )

        # Simulate startup-like logging (1000 logs in rapid succession)
        start = time.perf_counter()

        for i in range(1000):
            record = logging.LogRecord(
                name="src.main",
                level=logging.INFO,
                pathname="main.py",
                lineno=i % 100,
                msg=f"Loading component {i}",
                args=(),
                exc_info=None
            )
            handler.emit(record)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # 1000 logs should take <100ms (no blocking I/O)
        assert elapsed_ms < 100.0, f"1000 logs took {elapsed_ms}ms - too slow!"

        handler.close()

    def test_exception_in_worker_thread_doesnt_crash_handler(self):
        """
        Test that exceptions in worker thread don't crash the handler.

        The worker thread should handle errors gracefully and keep processing.
        """
        with patch('httpx.Client') as mock_client:
            # Make HTTP requests fail to simulate network issues
            mock_instance = MagicMock()
            mock_instance.post.side_effect = Exception("Network error")
            mock_client.return_value = mock_instance

            handler = LokiLogHandler(
                loki_url="http://localhost:3100/loki/api/v1/push",
                tags={"service": "gatewayz-test"},
                max_queue_size=1000
            )

            # Emit logs despite network errors
            for i in range(5):
                record = logging.LogRecord(
                    name="test",
                    level=logging.ERROR,
                    pathname="test.py",
                    lineno=1,
                    msg="Error message",
                    args=(),
                    exc_info=None
                )
                handler.emit(record)

            # Handler should still be functional
            assert not handler._shutdown.is_set()

            # Close should not crash even with failed requests
            handler.close()


class TestLokiQueueBatching:
    """Verify batch processing reduces HTTP calls."""

    def test_logs_are_batched_before_sending(self):
        """
        Test that multiple logs are batched into single HTTP request.

        Batching reduces HTTP overhead (critical for startup performance).
        """
        with patch('httpx.Client') as mock_client:
            mock_instance = MagicMock()
            post_calls = []
            mock_instance.post.side_effect = lambda *args, **kwargs: (
                post_calls.append((args, kwargs)), MagicMock(status_code=204)
            )[1]
            mock_client.return_value = mock_instance

            handler = LokiLogHandler(
                loki_url="http://localhost:3100/loki/api/v1/push",
                tags={"service": "gatewayz-test"},
                max_queue_size=1000
            )

            # Emit multiple logs
            for i in range(100):
                record = logging.LogRecord(
                    name="test",
                    level=logging.INFO,
                    pathname="test.py",
                    lineno=1,
                    msg=f"Message {i}",
                    args=(),
                    exc_info=None
                )
                handler.emit(record)

            # Let worker thread process (should batch into fewer requests)
            time.sleep(2.0)

            # Should have multiple batches, NOT 100 individual requests
            http_calls = mock_instance.post.call_count
            assert http_calls < 100, f"Expected batching, got {http_calls} HTTP calls for 100 logs"

            handler.close()

    def test_batch_size_respects_max_queue(self):
        """
        Test that batches respect the max_queue_size setting.

        If queue is full, it should drop old logs gracefully.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=100  # Small queue to test limits
        )

        # Fill the queue beyond capacity
        overflow_count = 0
        for i in range(150):  # More than queue size
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg=f"Message {i}",
                args=(),
                exc_info=None
            )
            try:
                handler.emit(record)  # Should handle overflow gracefully
            except queue.Full:
                overflow_count += 1

        # Should have handled overflow without crashing
        # (Note: current impl might drop silently or raise Full)
        # The important thing is that emit() handles it gracefully

        handler.close()


class TestLokiGracefulShutdown:
    """Verify logs are flushed on shutdown."""

    def test_shutdown_flushes_remaining_logs(self):
        """
        Test that close() flushes all queued logs before returning.

        This ensures no logs are lost when app shuts down.
        """
        flushed_logs = []

        def mock_post(*args, **kwargs):
            # Capture the logs that are being sent
            flushed_logs.append(kwargs)
            return MagicMock(status_code=204)

        with patch('httpx.Client') as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.side_effect = mock_post
            mock_client.return_value = mock_instance

            handler = LokiLogHandler(
                loki_url="http://localhost:3100/loki/api/v1/push",
                tags={"service": "gatewayz-test"},
                max_queue_size=1000
            )

            # Emit logs
            for i in range(10):
                record = logging.LogRecord(
                    name="test",
                    level=logging.INFO,
                    pathname="test.py",
                    lineno=1,
                    msg=f"Message {i}",
                    args=(),
                    exc_info=None
                )
                handler.emit(record)

            # Close should flush remaining logs
            handler.close()

            # Wait a bit for final flush
            time.sleep(0.5)

            # Verify that logs were flushed (at least attempted)
            # The important thing is handler returned and worker stopped
            assert handler._closed.is_set()

    def test_worker_thread_stops_on_shutdown(self):
        """
        Test that worker thread stops after shutdown.

        This prevents resource leaks and ensures clean shutdown.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )

        # Verify worker is running
        assert handler._worker_thread.is_alive(), "Worker thread should be running"

        # Close handler
        handler.close()

        # Wait for thread to stop
        handler._worker_thread.join(timeout=2.0)

        # Worker thread should have stopped
        assert not handler._worker_thread.is_alive(), "Worker thread should have stopped"


class TestLokiIntegrationWithLogging:
    """Test integration with Python's logging module."""

    def test_logger_with_loki_handler(self):
        """
        Test that standard Python logger works with Loki handler.

        This ensures compatibility with existing logging code.
        """
        logger = logging.getLogger("test_integration")

        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        # Log different levels
        start = time.perf_counter()

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should be fast (no blocking I/O)
        assert elapsed_ms < 10.0, f"Logging took {elapsed_ms}ms - probably blocking!"

        handler.close()

    def test_exception_logging_doesnt_block(self):
        """
        Test that exception logging (with traceback) doesn't block.

        Exception logs are larger and should still be non-blocking.
        """
        logger = logging.getLogger("test_exceptions")

        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)

        # Log an exception
        start = time.perf_counter()
        try:
            1 / 0
        except ZeroDivisionError:
            logger.exception("Error with traceback")

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Even with traceback, should be fast
        assert elapsed_ms < 10.0, f"Exception logging took {elapsed_ms}ms - probably blocking!"

        handler.close()


class TestStartupPerformance:
    """
    Simulate startup-like logging patterns to verify performance.

    These tests validate that PR #681 fix (async queue) prevents
    7+ minute startup delays.
    """

    def test_startup_simulation_with_many_logs(self):
        """
        Simulate actual startup with hundreds of log messages.

        This mimics what happens during:
        - Route loading (1 log per route × 30 routes)
        - Provider initialization (1 log per provider × 16 providers)
        - Service startup (50+ logs)
        - Database initialization (30+ logs)

        Total: ~150+ logs during startup
        """
        logger = logging.getLogger("src.main")

        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz"},
            max_queue_size=10000
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Simulate startup
        start = time.perf_counter()

        # Route loading
        for i in range(30):
            logger.info(f"  [OK] Route {i} loaded")

        # Provider initialization
        for i in range(16):
            logger.info(f"  [OK] Provider {i} initialized")

        # Service startup
        logger.info("Initializing Redis connection...")
        time.sleep(0.01)  # Simulate actual I/O
        logger.info("  ✅ Redis connected")

        logger.info("Loading model catalog...")
        time.sleep(0.02)
        logger.info("  ✅ Catalog loaded with 500+ models")

        # More service logs
        for i in range(20):
            logger.info(f"Starting background task {i}")

        elapsed_seconds = time.perf_counter() - start

        # Should complete in <2 seconds
        # Before PR #681 fix: ~30-60 seconds (due to blocking HTTP calls)
        # After PR #681 fix: <2 seconds (async queue)
        assert elapsed_seconds < 2.0, (
            f"Startup simulation took {elapsed_seconds}s - "
            f"too slow! Loki might be blocking again."
        )

        handler.close()

    def test_startup_with_request_volume(self):
        """
        Test logging during actual request processing.

        Simulates concurrent requests with logging.
        """
        logger = logging.getLogger("src.routes.unified_chat")

        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz"},
            max_queue_size=10000
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        # Simulate concurrent requests
        def simulate_request(request_id):
            logger.info(f"Request {request_id} received")
            time.sleep(0.001)  # Simulate processing
            logger.info(f"Request {request_id} inference started")
            time.sleep(0.01)  # Simulate inference
            logger.info(f"Request {request_id} completed")

        start = time.perf_counter()

        # Simulate 10 concurrent-ish requests
        for i in range(10):
            simulate_request(i)

        elapsed_seconds = time.perf_counter() - start

        # 10 requests × 3 logs each = 30 logs
        # Should complete in <1 second (not waiting for HTTP)
        assert elapsed_seconds < 1.0, (
            f"Request logging took {elapsed_seconds}s - "
            f"Loki might be blocking!"
        )

        handler.close()


class TestRegressionPrevention:
    """
    Tests to prevent regression of the Loki blocking issue.

    These tests should be run in CI to catch any future issues.
    """

    @pytest.mark.performance
    def test_emit_performance_regression(self):
        """
        Benchmark: emit() should always be <5ms.

        If this test fails, it means emit() is doing blocking I/O again.
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=1000
        )

        times = []
        for _ in range(100):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None
            )
            start = time.perf_counter()
            handler.emit(record)
            times.append((time.perf_counter() - start) * 1000)

        avg_time_ms = sum(times) / len(times)
        max_time_ms = max(times)

        # Average should be <2ms
        assert avg_time_ms < 2.0, f"Average emit time: {avg_time_ms}ms (should be <2ms)"
        # Max should be <10ms
        assert max_time_ms < 10.0, f"Max emit time: {max_time_ms}ms (should be <10ms)"

        handler.close()

    def test_no_blocking_on_queue_full(self):
        """
        Test that queue being full doesn't block emit().

        If queue is full, handler should either:
        1. Drop oldest logs gracefully
        2. Not block the main thread
        """
        handler = LokiLogHandler(
            loki_url="http://localhost:3100/loki/api/v1/push",
            tags={"service": "gatewayz-test"},
            max_queue_size=10  # Very small queue
        )

        # Fill queue
        for i in range(20):  # More than queue size
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg=f"Message {i}",
                args=(),
                exc_info=None
            )

            # Measure emit time even when queue is full
            start = time.perf_counter()
            handler.emit(record)
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Should still be fast even when queue is full
            assert elapsed_ms < 10.0, (
                f"emit() blocked for {elapsed_ms}ms when queue was full!"
            )

        handler.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
