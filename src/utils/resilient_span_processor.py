"""
Resilient Span Processor for OpenTelemetry.

Wraps the standard BatchSpanProcessor with error handling and circuit breaker logic
to prevent connection errors from polluting logs and degrading performance.
"""

import logging
import threading
import time
from typing import Optional

try:
    from opentelemetry.sdk.trace import SpanProcessor
    from requests.exceptions import ConnectionError, Timeout

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    SpanProcessor = object  # type: ignore

logger = logging.getLogger(__name__)


class ResilientSpanProcessor(SpanProcessor):
    """
    A resilient wrapper around OpenTelemetry's span processor that handles
    connection failures gracefully using a circuit breaker pattern.

    Circuit Breaker States:
    - CLOSED: Normal operation, traces are exported
    - OPEN: Too many failures, tracing temporarily disabled (in cooldown)
    - HALF_OPEN: Testing if service has recovered (after cooldown expires)

    Features:
    - Suppresses "Connection reset by peer" errors to avoid log pollution
    - Implements circuit breaker to temporarily disable failing exporters
    - Automatically attempts recovery after cooldown period
    - Logs summary statistics instead of individual failures
    """

    # Circuit breaker thresholds
    FAILURE_THRESHOLD = 5  # Open circuit after N consecutive failures
    SUCCESS_THRESHOLD = 2  # Close circuit after N consecutive successes in HALF_OPEN state
    COOLDOWN_SECONDS = 60  # Wait time before attempting recovery

    # Circuit breaker states
    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(self, span_processor: SpanProcessor):
        """
        Initialize resilient span processor.

        Args:
            span_processor: The underlying span processor to wrap (typically BatchSpanProcessor)
        """
        self._processor = span_processor
        self._failure_count = 0
        self._success_count = 0
        self._circuit_state = self.STATE_CLOSED
        self._last_failure_time: Optional[float] = None
        self._total_exports = 0
        self._total_failures = 0
        self._total_drops = 0  # Spans dropped due to open circuit
        self._lock = threading.Lock()

        logger.info("🛡️  Resilient span processor initialized with circuit breaker")

    @property
    def _circuit_open(self) -> bool:
        """Backward-compatible property for circuit open state."""
        return self._circuit_state != self.STATE_CLOSED

    def on_start(self, span, parent_context=None):
        """Called when a span is started."""
        try:
            # Skip if circuit is not closed (OPEN or HALF_OPEN states)
            if self._circuit_state == self.STATE_CLOSED:
                self._processor.on_start(span, parent_context)
        except Exception as e:
            # Silently ignore errors in span start - don't break request flow
            logger.debug(f"Error in span on_start: {e}")

    def on_end(self, span):
        """Called when a span ends - add to export queue."""
        try:
            # Skip if circuit is not closed (OPEN or HALF_OPEN states)
            # This ensures consistency with on_start behavior
            if self._circuit_state == self.STATE_CLOSED:
                self._processor.on_end(span)
        except Exception as e:
            # Silently ignore errors in span end - don't break request flow
            logger.debug(f"Error in span on_end: {e}")

    def shutdown(self):
        """Shutdown the processor and flush remaining spans."""
        try:
            logger.info("🛑 Shutting down resilient span processor...")
            self._log_statistics()
            self._processor.shutdown()
        except Exception as e:
            logger.warning(f"Error during span processor shutdown: {e}")

    def force_flush(self, timeout_millis: int = 30000):
        """
        Force flush all queued spans with error handling.

        The entire operation is protected by a lock to prevent race conditions
        where multiple threads could pass circuit checks simultaneously.

        Args:
            timeout_millis: Maximum time to wait for flush in milliseconds

        Returns:
            bool: True if flush succeeded, False otherwise
        """
        with self._lock:
            self._total_exports += 1

            # Check circuit breaker state
            if self._circuit_state == self.STATE_OPEN:
                # Check if cooldown period has passed
                if self._last_failure_time and (time.time() - self._last_failure_time) > self.COOLDOWN_SECONDS:
                    logger.info("🔄 Circuit breaker cooldown complete - entering HALF_OPEN state...")
                    # Transition to HALF_OPEN for testing recovery
                    self._circuit_state = self.STATE_HALF_OPEN
                    self._failure_count = 0
                    self._success_count = 0
                else:
                    # Still in cooldown - silently drop spans
                    self._total_drops += 1
                    logger.debug("Circuit breaker OPEN - dropping spans (in cooldown)")
                    return False

            try:
                # Attempt to flush spans
                result = self._processor.force_flush(timeout_millis)

                # Track success
                self._success_count += 1
                self._failure_count = 0  # Reset failure count on success

                # Handle state transitions based on current state
                if self._circuit_state == self.STATE_HALF_OPEN:
                    # In HALF_OPEN state, close circuit after enough successes
                    if self._success_count >= self.SUCCESS_THRESHOLD:
                        logger.info("✅ Circuit breaker CLOSED - tracing fully restored")
                        self._circuit_state = self.STATE_CLOSED

                return result

            except (ConnectionError, Timeout, OSError) as e:
                # Connection-related errors - handle gracefully
                self._total_failures += 1
                self._failure_count += 1
                self._success_count = 0  # Reset success count
                self._last_failure_time = time.time()

                # Handle state transitions based on current state
                if self._circuit_state == self.STATE_HALF_OPEN:
                    # Failed during recovery - go back to OPEN state
                    self._circuit_state = self.STATE_OPEN
                    logger.warning(
                        f"⚠️  Circuit breaker returning to OPEN - recovery failed. "
                        f"Will retry in {self.COOLDOWN_SECONDS}s. "
                        f"Cause: {type(e).__name__}: {str(e)}"
                    )
                elif self._circuit_state == self.STATE_CLOSED:
                    # Open circuit if threshold exceeded
                    if self._failure_count >= self.FAILURE_THRESHOLD:
                        self._circuit_state = self.STATE_OPEN
                        logger.warning(
                            f"⚠️  Circuit breaker OPEN - temporarily disabling OpenTelemetry tracing "
                            f"(failed {self._failure_count} times in a row). "
                            f"Will retry in {self.COOLDOWN_SECONDS}s. "
                            f"Cause: {type(e).__name__}: {str(e)}"
                        )
                    else:
                        # Log at debug level for occasional failures
                        logger.debug(
                            f"OpenTelemetry export failed ({self._failure_count}/{self.FAILURE_THRESHOLD}): "
                            f"{type(e).__name__}: {str(e)}"
                        )

                return False

            except Exception as e:
                # Unexpected errors - log at warning level
                self._total_failures += 1
                self._failure_count += 1

                logger.warning(
                    f"Unexpected error during span export: {type(e).__name__}: {str(e)}",
                    exc_info=False  # Don't log full stack trace for common errors
                )
                return False

    def _log_statistics(self):
        """Log summary statistics about exports."""
        if self._total_exports > 0:
            # Calculate success rate excluding dropped spans
            attempted_exports = self._total_exports - self._total_drops
            if attempted_exports > 0:
                success_rate = ((attempted_exports - self._total_failures) / attempted_exports) * 100
            else:
                success_rate = 0.0

            logger.info(
                f"📊 OpenTelemetry export stats: "
                f"{self._total_exports} total, "
                f"{attempted_exports} attempted, "
                f"{self._total_failures} failures, "
                f"{self._total_drops} dropped, "
                f"{success_rate:.1f}% success rate"
            )
