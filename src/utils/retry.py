"""
Retry utilities for handling transient network and database connection errors.

This module provides retry decorators with exponential backoff to handle
transient errors that may occur when background tasks execute after the
HTTP response is sent and connections become stale.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from functools import wraps

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 0.1,
    max_delay: float = 2.0,
    exponential_base: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    Decorator that retries a function with exponential backoff.

    This is particularly useful for database operations that may fail due to
    stale connections when executed in background tasks after HTTP responses
    are sent.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds before first retry (default: 0.1s)
        max_delay: Maximum delay in seconds between retries (default: 2.0s)
        exponential_base: Base for exponential backoff calculation (default: 2.0)
        exceptions: Tuple of exception types to catch and retry (default: all exceptions)

    Returns:
        Decorated function that will retry on specified exceptions

    Example:
        @with_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        def my_database_operation():
            # ... code that may fail with transient errors
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    # Check if this is a retryable error
                    error_str = str(e).lower()
                    is_retryable = any(
                        keyword in error_str
                        for keyword in [
                            "server disconnected",
                            "connection",
                            "timeout",
                            "network",
                            "remote protocol error",
                            "broken pipe",
                            "connection reset",
                        ]
                    )

                    if not is_retryable:
                        # Don't retry non-transient errors
                        logger.warning(f"{func.__name__} failed with non-retryable error: {e}")
                        raise

                    if attempt < max_attempts:
                        # Calculate delay with exponential backoff
                        delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)

                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt}/{max_attempts}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )

                        time.sleep(delay)
                    else:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")

            # All retries exhausted, raise the last exception
            raise last_exception

        return wrapper

    return decorator


def with_async_retry(
    max_attempts: int = 3,
    initial_delay: float = 0.1,
    max_delay: float = 2.0,
    exponential_base: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    Async version of retry decorator with exponential backoff.

    This is particularly useful for async database operations that may fail due to
    stale connections when executed in background tasks after HTTP responses
    are sent.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds before first retry (default: 0.1s)
        max_delay: Maximum delay in seconds between retries (default: 2.0s)
        exponential_base: Base for exponential backoff calculation (default: 2.0)
        exceptions: Tuple of exception types to catch and retry (default: all exceptions)

    Returns:
        Decorated async function that will retry on specified exceptions

    Example:
        @with_async_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        async def my_async_database_operation():
            # ... async code that may fail with transient errors
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    # Check if this is a retryable error
                    error_str = str(e).lower()
                    is_retryable = any(
                        keyword in error_str
                        for keyword in [
                            "server disconnected",
                            "connection",
                            "timeout",
                            "network",
                            "remote protocol error",
                            "broken pipe",
                            "connection reset",
                        ]
                    )

                    if not is_retryable:
                        # Don't retry non-transient errors
                        logger.warning(f"{func.__name__} failed with non-retryable error: {e}")
                        raise

                    if attempt < max_attempts:
                        # Calculate delay with exponential backoff
                        delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)

                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt}/{max_attempts}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )

                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")

            # All retries exhausted, raise the last exception
            raise last_exception

        return wrapper

    return decorator
