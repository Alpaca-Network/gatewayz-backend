"""
Provider Timing Tracker

Utility for tracking and alerting on slow provider responses.
Helps identify bottlenecks in the inference pipeline by logging
response times and exposing Prometheus metrics.

Usage:
    @track_provider_timing("openrouter")
    async def make_provider_request(...):
        ...
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Thresholds for alerting (in seconds)
SLOW_THRESHOLD = 30.0  # Warn if request takes > 30s
VERY_SLOW_THRESHOLD = 45.0  # Error if request takes > 45s
CRITICAL_THRESHOLD = 60.0  # Critical if request takes > 60s


def track_provider_timing(
    provider_name: str,
    slow_threshold: float = SLOW_THRESHOLD,
    very_slow_threshold: float = VERY_SLOW_THRESHOLD,
    critical_threshold: float = CRITICAL_THRESHOLD,
):
    """
    Decorator to track and alert on slow provider responses.

    Logs warnings/errors when providers take too long and exposes
    Prometheus metrics for monitoring.

    Args:
        provider_name: Name of the provider (e.g., "openrouter", "cerebras")
        slow_threshold: Log warning if duration exceeds this (default 30s)
        very_slow_threshold: Log error if duration exceeds this (default 45s)
        critical_threshold: Log critical if duration exceeds this (default 60s)

    Example:
        @track_provider_timing("openrouter")
        async def make_openrouter_request(messages, model, **kwargs):
            # Make API call
            return response
    """

    def decorator(func: Callable) -> Callable:
        # Import here to avoid circular dependencies
        try:
            from src.services.prometheus_metrics import (
                provider_response_duration,
                provider_slow_requests_total,
            )

            metrics_available = True
        except ImportError:
            metrics_available = False
            logger.debug("Prometheus metrics not available for provider timing")

        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start_time = time.monotonic()
            model_name = kwargs.get("model", "unknown")
            operation = func.__name__

            try:
                # Execute the provider call
                result = await func(*args, **kwargs)
                duration = time.monotonic() - start_time

                # Log based on duration thresholds
                _log_provider_timing(
                    provider_name=provider_name,
                    model_name=model_name,
                    duration=duration,
                    operation=operation,
                    slow_threshold=slow_threshold,
                    very_slow_threshold=very_slow_threshold,
                    critical_threshold=critical_threshold,
                )

                # Record Prometheus metrics
                if metrics_available:
                    provider_response_duration.labels(
                        provider=provider_name, model=model_name, status="success"
                    ).observe(duration)

                    # Track slow requests
                    if duration > slow_threshold:
                        provider_slow_requests_total.labels(
                            provider=provider_name,
                            model=model_name,
                            severity="slow" if duration < very_slow_threshold else "very_slow",
                        ).inc()

                return result

            except Exception as e:
                duration = time.monotonic() - start_time

                # Log error with timing
                logger.error(
                    f"PROVIDER ERROR: {provider_name} ({operation}) "
                    f"failed after {duration:.1f}s "
                    f"(model: {model_name}): {type(e).__name__}: {str(e)[:100]}"
                )

                # Record failure metric
                if metrics_available:
                    provider_response_duration.labels(
                        provider=provider_name, model=model_name, status="error"
                    ).observe(duration)

                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start_time = time.monotonic()
            model_name = kwargs.get("model", "unknown")
            operation = func.__name__

            try:
                # Execute the provider call
                result = func(*args, **kwargs)
                duration = time.monotonic() - start_time

                # Log based on duration thresholds
                _log_provider_timing(
                    provider_name=provider_name,
                    model_name=model_name,
                    duration=duration,
                    operation=operation,
                    slow_threshold=slow_threshold,
                    very_slow_threshold=very_slow_threshold,
                    critical_threshold=critical_threshold,
                )

                # Record Prometheus metrics
                if metrics_available:
                    provider_response_duration.labels(
                        provider=provider_name, model=model_name, status="success"
                    ).observe(duration)

                    # Track slow requests
                    if duration > slow_threshold:
                        provider_slow_requests_total.labels(
                            provider=provider_name,
                            model=model_name,
                            severity="slow" if duration < very_slow_threshold else "very_slow",
                        ).inc()

                return result

            except Exception as e:
                duration = time.monotonic() - start_time

                # Log error with timing
                logger.error(
                    f"PROVIDER ERROR: {provider_name} ({operation}) "
                    f"failed after {duration:.1f}s "
                    f"(model: {model_name}): {type(e).__name__}: {str(e)[:100]}"
                )

                # Record failure metric
                if metrics_available:
                    provider_response_duration.labels(
                        provider=provider_name, model=model_name, status="error"
                    ).observe(duration)

                raise

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def _log_provider_timing(
    provider_name: str,
    model_name: str,
    duration: float,
    operation: str,
    slow_threshold: float,
    very_slow_threshold: float,
    critical_threshold: float,
) -> None:
    """
    Internal function to log provider timing based on thresholds.

    Args:
        provider_name: Provider name
        model_name: Model identifier
        duration: Request duration in seconds
        operation: Function name (e.g., "make_openrouter_request")
        slow_threshold: Warning threshold
        very_slow_threshold: Error threshold
        critical_threshold: Critical threshold
    """
    if duration >= critical_threshold:
        logger.critical(
            f"⚠️  CRITICAL SLOW PROVIDER: {provider_name} ({operation}) "
            f"took {duration:.1f}s (>={critical_threshold:.0f}s threshold) "
            f"for model: {model_name}. "
            f"This is blocking a concurrency slot and causing 503 errors!"
        )
    elif duration >= very_slow_threshold:
        logger.error(
            f"🐌 VERY SLOW PROVIDER: {provider_name} ({operation}) "
            f"took {duration:.1f}s (>={very_slow_threshold:.0f}s threshold) "
            f"for model: {model_name}. "
            f"Consider implementing failover or increasing timeouts."
        )
    elif duration >= slow_threshold:
        logger.warning(
            f"⏱️  SLOW PROVIDER: {provider_name} ({operation}) "
            f"took {duration:.1f}s (>={slow_threshold:.0f}s threshold) "
            f"for model: {model_name}. "
            f"Monitor for patterns."
        )
    else:
        # Normal response - only log at debug level
        logger.debug(
            f"✓ {provider_name} ({operation}) responded in {duration:.2f}s "
            f"(model: {model_name})"
        )


# Context manager for manual timing (when decorator isn't suitable)
class ProviderTimingContext:
    """
    Context manager for manual provider timing tracking.

    Usage:
        async with ProviderTimingContext("openrouter", "gpt-4"):
            response = await client.chat.completions.create(...)
    """

    def __init__(
        self,
        provider_name: str,
        model_name: str = "unknown",
        operation: str = "manual_operation",
    ):
        self.provider_name = provider_name
        self.model_name = model_name
        self.operation = operation
        self.start_time: Optional[float] = None
        self.duration: Optional[float] = None

    async def __aenter__(self):
        self.start_time = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.duration = time.monotonic() - self.start_time

        if exc_type is None:
            # Success
            _log_provider_timing(
                provider_name=self.provider_name,
                model_name=self.model_name,
                duration=self.duration,
                operation=self.operation,
                slow_threshold=SLOW_THRESHOLD,
                very_slow_threshold=VERY_SLOW_THRESHOLD,
                critical_threshold=CRITICAL_THRESHOLD,
            )

            # Record success metric
            try:
                from src.services.prometheus_metrics import provider_response_duration

                provider_response_duration.labels(
                    provider=self.provider_name, model=self.model_name, status="success"
                ).observe(self.duration)
            except ImportError:
                pass
        else:
            # Error occurred
            logger.error(
                f"PROVIDER ERROR: {self.provider_name} ({self.operation}) "
                f"failed after {self.duration:.1f}s "
                f"(model: {self.model_name}): {exc_type.__name__}"
            )

            # Record error metric
            try:
                from src.services.prometheus_metrics import provider_response_duration

                provider_response_duration.labels(
                    provider=self.provider_name, model=self.model_name, status="error"
                ).observe(self.duration)
            except ImportError:
                pass

        return False  # Don't suppress exceptions

    def __enter__(self):
        self.start_time = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = time.monotonic() - self.start_time

        if exc_type is None:
            # Success
            _log_provider_timing(
                provider_name=self.provider_name,
                model_name=self.model_name,
                duration=self.duration,
                operation=self.operation,
                slow_threshold=SLOW_THRESHOLD,
                very_slow_threshold=VERY_SLOW_THRESHOLD,
                critical_threshold=CRITICAL_THRESHOLD,
            )

            # Record success metric
            try:
                from src.services.prometheus_metrics import provider_response_duration

                provider_response_duration.labels(
                    provider=self.provider_name, model=self.model_name, status="success"
                ).observe(self.duration)
            except ImportError:
                pass
        else:
            # Error occurred
            logger.error(
                f"PROVIDER ERROR: {self.provider_name} ({self.operation}) "
                f"failed after {self.duration:.1f}s "
                f"(model: {self.model_name}): {exc_type.__name__}"
            )

            # Record error metric
            try:
                from src.services.prometheus_metrics import provider_response_duration

                provider_response_duration.labels(
                    provider=self.provider_name, model=self.model_name, status="error"
                ).observe(self.duration)
            except ImportError:
                pass

        return False  # Don't suppress exceptions
