"""
Logging configuration with Grafana Loki integration.

This module configures Python logging to send structured logs to Grafana Loki
while maintaining console logging for local development.

Features:
- Structured JSON logging with trace correlation
- Automatic Loki push integration (non-blocking async queue)
- Environment-aware configuration
- Trace ID injection for log-to-trace correlation
"""

import atexit
import logging
import queue
import sys
import threading

from src.config.config import Config

logger = logging.getLogger(__name__)


class LokiLogHandler(logging.Handler):
    """
    Custom log handler that sends logs to Grafana Loki asynchronously.

    This handler uses a background thread and queue to send logs to Loki
    without blocking the main application. This is critical for fast startup
    and preventing healthcheck failures.

    Features:
    - Non-blocking: logs are queued and sent by background thread
    - Batching: multiple logs can be sent in one request
    - Graceful shutdown: flushes remaining logs on close
    - Fault-tolerant: silently drops logs if queue is full
    """

    def __init__(self, loki_url: str, tags: dict[str, str], max_queue_size: int = 10000):
        super().__init__()
        self.loki_url = loki_url
        self.tags = tags
        self._session = None
        self._session_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._shutdown = threading.Event()
        self._closed = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

        # Register cleanup on interpreter shutdown
        atexit.register(self._atexit_flush)

    def _get_session(self):
        """Lazy-load HTTP session for sending logs with connection limits.

        Thread-safe: uses a lock to prevent race conditions during session creation.
        Returns None if handler is closed to prevent resource leaks.
        """
        # Don't create new sessions after close() has been called
        if self._closed.is_set():
            return None

        with self._session_lock:
            # Double-check after acquiring lock
            if self._closed.is_set():
                return None
            if self._session is None:
                import httpx

                # Create client with strict timeouts and connection limits to prevent resource exhaustion
                # Set max_connections to prevent too many concurrent connections
                # Set max_keepalive_connections to limit persistent connections
                limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
                timeout = httpx.Timeout(5.0, connect=2.0)
                self._session = httpx.Client(timeout=timeout, limits=limits)
            return self._session

    def _worker(self) -> None:
        """Background worker thread that sends logs to Loki.

        The worker continues processing until both:
        1. The shutdown event is set
        2. The queue is empty (all remaining logs are flushed)
        """
        while True:
            try:
                # Wait for log entry with timeout to allow shutdown check
                try:
                    payload = self._queue.get(timeout=0.5)
                except queue.Empty:
                    # Queue is empty - check if we should exit
                    if self._shutdown.is_set():
                        break
                    continue

                # Send to Loki
                self._send_to_loki(payload)
                self._queue.task_done()

            except Exception:
                # Silently ignore errors to prevent worker thread crash.
                # Loki logging is best-effort - losing some logs is acceptable
                # to maintain application stability.
                pass

    def _send_to_loki(self, payload: dict) -> None:
        """Send a payload to Loki."""
        try:
            session = self._get_session()
            if session is None:
                # Handler is closed, skip sending
                return
            response = session.post(self.loki_url, json=payload, timeout=5.0)
            response.raise_for_status()
        except Exception:
            # Silently ignore Loki failures to prevent cascade errors.
            # Loki logging is best-effort - network issues or Loki downtime
            # should not affect application functionality.
            pass

    def emit(self, record: logging.LogRecord) -> None:
        """
        Queue log record for async sending to Loki.

        This method is non-blocking - it queues the log and returns immediately.

        Args:
            record: LogRecord to send
        """
        try:
            # Format the log message
            log_entry = self.format(record)

            # Build Loki labels with comprehensive filtering capabilities
            labels = {**self.tags}

            # Add log level for filtering (ERROR, WARNING, INFO, DEBUG)
            labels["level"] = record.levelname

            # Add logger name (module path: src.routes.chat, src.services.pricing)
            labels["logger"] = record.name

            # Get trace context if available
            trace_id = getattr(record, "trace_id", None)
            if trace_id:
                labels["trace_id"] = trace_id

            # Add span_id for trace correlation
            span_id = getattr(record, "span_id", None)
            if span_id:
                labels["span_id"] = span_id

            # Add request path if available (from middleware)
            if hasattr(record, "request_path"):
                labels["path"] = record.request_path

            # Add HTTP method if available
            if hasattr(record, "http_method"):
                labels["method"] = record.http_method

            # Add provider name for AI provider filtering
            if hasattr(record, "provider"):
                labels["provider"] = record.provider

            # Add model name for model-specific filtering
            if hasattr(record, "model"):
                labels["model"] = record.model

            # Add user_id for user-specific debugging (if not sensitive)
            if hasattr(record, "user_id"):
                labels["user_id"] = str(record.user_id)

            # Add error type for error categorization
            if record.exc_info and record.exc_info[0]:
                labels["error_type"] = record.exc_info[0].__name__

            # Build Loki push payload
            # Format: {"streams": [{"stream": {...labels}, "values": [[timestamp_ns, log_line]]}]}
            timestamp_ns = str(int(record.created * 1_000_000_000))
            payload = {"streams": [{"stream": labels, "values": [[timestamp_ns, log_entry]]}]}

            # Non-blocking queue put - drop log if queue is full
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                # Queue is full, drop this log to prevent blocking
                pass

        except Exception:
            # Silently ignore errors to prevent cascade failures.
            # Logging should never crash the application - drop the log
            # rather than raise an exception.
            pass

    def _atexit_flush(self) -> None:
        """Flush remaining logs on interpreter shutdown."""
        self.close()

    def flush(self, timeout: float = 10.0) -> None:
        """Flush all queued logs with timeout.

        Blocks until all currently queued logs have been fully processed
        (sent to Loki), or until timeout is reached.

        Args:
            timeout: Maximum seconds to wait for flush (default: 10.0)

        Note: Uses queue.all_tasks_done condition with timeout since
        Queue.join() doesn't support timeout directly. We track a deadline
        to ensure total wait time never exceeds the specified timeout,
        even with spurious wakeups or new items being added during the wait.
        """
        import time

        # Calculate deadline to ensure total wait never exceeds timeout
        deadline = time.monotonic() + timeout

        # Use the queue's internal condition variable to wait for all tasks
        # to complete (all task_done() calls made), with timeout support.
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Timeout reached
                    break
                if not self._queue.all_tasks_done.wait(timeout=remaining):
                    # Timeout reached (wait returned False)
                    break

    def close(self) -> None:
        """Gracefully shutdown the handler.

        Ensures all queued logs are sent before shutting down:
        1. Signal worker to begin shutdown (it will drain remaining queue items)
        2. Wait for worker thread to exit (with timeout to prevent hanging)
        3. Mark handler as closed to prevent new session creation
        4. Close HTTP session (with lock to prevent races)

        Note: The 5-second timeout is a safety limit to prevent hanging during
        shutdown. Since Loki logging is best-effort, some log loss is acceptable
        if the queue cannot be drained in time. The worker thread (being a daemon)
        will be terminated when the process exits anyway.
        """
        try:
            # Guard against Python interpreter shutdown where threading might be unavailable
            if not hasattr(self, "_shutdown") or self._shutdown is None:
                return

            # Signal worker to stop accepting new items and drain queue
            # Note: _closed is NOT set yet so worker can still send remaining items
            self._shutdown.set()

            # Wait for worker thread to finish draining and exit (with timeout)
            # The timeout prevents hanging during shutdown if Loki is slow/unreachable
            if hasattr(self, "_worker_thread") and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=5.0)

            # Only mark as closed and clean up session if worker has actually exited
            # If timeout expired but worker is still running, leave resources available
            # so it can continue draining. The daemon thread will be killed on process
            # exit anyway, and resources will be garbage collected.
            if hasattr(self, "_worker_thread") and not self._worker_thread.is_alive():
                # Worker has exited - safe to clean up
                if hasattr(self._closed, "set"):
                    self._closed.set()

                if hasattr(self, "_session_lock"):
                    with self._session_lock:
                        if self._session:
                            try:
                                self._session.close()
                            except Exception:
                                # Ignore session close errors to avoid shutdown failures.
                                # The session will be garbage collected anyway.
                                pass
                            self._session = None

        except Exception:
            # Silently ignore any errors during shutdown to prevent crash
            pass

        try:
            super().close()
        except Exception:
            pass


class TraceContextFilter(logging.Filter):
    """
    Logging filter that adds trace context to log records.

    This filter enriches log records with OpenTelemetry trace and span IDs,
    enabling correlation between logs and traces in Grafana.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add trace context to log record.

        Args:
            record: LogRecord to enrich

        Returns:
            bool: Always True (don't filter out records)
        """
        try:
            from src.config.opentelemetry_config import get_current_span_id, get_current_trace_id

            trace_id = get_current_trace_id()
            span_id = get_current_span_id()

            if trace_id:
                record.trace_id = trace_id
            if span_id:
                record.span_id = span_id

        except Exception:
            # Don't fail if we can't get trace context
            pass

        return True


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Formats log records as JSON with trace context and additional metadata.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: LogRecord to format

        Returns:
            str: JSON-formatted log entry
        """
        import json

        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add trace context if available
        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id
        if hasattr(record, "span_id"):
            log_data["span_id"] = record.span_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return json.dumps(log_data)


def configure_logging() -> bool:
    """
    Configure application logging with Loki integration.

    Sets up:
    - Console handler for local development
    - Loki handler for production (if enabled)
    - Trace context filter for log-to-trace correlation
    - JSON formatting for structured logs

    Returns:
        bool: True if Loki integration was enabled, False otherwise
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Add trace context filter to all loggers
    trace_filter = TraceContextFilter()
    root_logger.addFilter(trace_filter)

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Use simple format for console in development, JSON in production
    if Config.IS_DEVELOPMENT:
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    else:
        console_formatter = StructuredFormatter()

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    logger.info("📝 Console logging configured")

    # Loki handler (optional)
    loki_enabled = False
    if Config.LOKI_ENABLED:
        try:
            loki_handler = LokiLogHandler(
                loki_url=Config.LOKI_PUSH_URL,
                tags={
                    "app": Config.OTEL_SERVICE_NAME,
                    "environment": Config.APP_ENV,
                    "service": "gatewayz-api",
                },
            )
            loki_handler.setLevel(logging.INFO)
            loki_handler.setFormatter(StructuredFormatter())
            root_logger.addHandler(loki_handler)

            logger.info(f"✅ Loki logging enabled: {Config.LOKI_PUSH_URL}")
            loki_enabled = True

        except Exception as e:
            logger.warning(f"⚠️  Failed to configure Loki logging: {e}")

    else:
        logger.info("⏭️  Loki logging disabled (LOKI_ENABLED=false)")

    # Set log levels for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)

    return loki_enabled
