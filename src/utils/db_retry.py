"""
Database operation retry utilities for handling transient HTTP/2 connection errors.

This module provides retry logic for Supabase database operations to handle
transient connection issues like:
- StreamIDTooLowError: HTTP/2 stream ID conflicts from connection reuse
- ConnectionTerminated: Server closed the connection unexpectedly
- Server disconnected: Remote server terminated the connection
- LocalProtocolError: Invalid connection state (RECV_DATA/RECV_HEADERS in CLOSED state)

These errors are common with HTTP/2 long-lived connections and can be safely retried
by forcing a fresh connection.
"""

import functools
import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# Error types that indicate HTTP/2 connection issues that can be safely retried
HTTP2_CONNECTION_ERROR_PATTERNS = (
    "StreamIDTooLowError",
    "ConnectionTerminated",
    "Server disconnected",
    "LocalProtocolError",
    "Invalid input ConnectionInputs",
    "RemoteProtocolError",
    "connection closed",
    "Connection reset",
    "ConnectionResetError",
    "h2.exceptions",
    "RECV_DATA in state ConnectionState.CLOSED",
    "RECV_HEADERS in state ConnectionState.CLOSED",
)

# Maximum number of retries for transient errors
MAX_RETRIES = 2

# Delay between retries in seconds (with exponential backoff)
BASE_RETRY_DELAY = 0.1

T = TypeVar("T")


def is_http2_connection_error(error: Exception) -> bool:
    """
    Check if an exception is a transient HTTP/2 connection error.

    These errors occur when:
    1. The server closes a long-lived HTTP/2 connection
    2. The client tries to reuse a stale connection
    3. Stream ID conflicts occur from connection multiplexing

    Args:
        error: The exception to check

    Returns:
        True if the error is a transient HTTP/2 connection issue
    """
    error_str = str(error)
    error_type = type(error).__name__

    # Check error message for known patterns
    for pattern in HTTP2_CONNECTION_ERROR_PATTERNS:
        if pattern.lower() in error_str.lower() or pattern in error_type:
            return True

    # Check the exception chain for nested HTTP/2 errors
    if error.__cause__:
        if is_http2_connection_error(error.__cause__):
            return True

    return False


def reset_supabase_client() -> None:
    """
    Reset the Supabase client to force a fresh connection.

    This is called after detecting an HTTP/2 connection error to ensure
    the next operation gets a new, healthy connection.
    """
    try:
        from src.config.supabase_config import cleanup_supabase_client
        cleanup_supabase_client()
        logger.info("Reset Supabase client to recover from connection error")
    except Exception as e:
        logger.warning(f"Failed to reset Supabase client: {e}")


def with_db_retry(
    operation_name: str = "database operation",
    max_retries: int = MAX_RETRIES,
    reset_on_error: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to add retry logic for database operations.

    Automatically retries operations that fail due to transient HTTP/2
    connection errors, resetting the Supabase client between retries
    to ensure a fresh connection.

    Args:
        operation_name: Human-readable name for logging
        max_retries: Maximum number of retry attempts
        reset_on_error: Whether to reset the Supabase client on error

    Returns:
        Decorated function with retry logic

    Example:
        @with_db_retry("get user plan")
        def get_user_plan(user_id: int) -> dict:
            client = get_supabase_client()
            return client.table("plans").select("*").eq("user_id", user_id).execute()
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e

                    if not is_http2_connection_error(e):
                        # Not a transient connection error, don't retry
                        raise

                    if attempt < max_retries:
                        # Log the retry attempt
                        delay = BASE_RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"HTTP/2 connection error during {operation_name} "
                            f"(attempt {attempt + 1}/{max_retries + 1}): {type(e).__name__}: {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )

                        # Reset the client to get a fresh connection
                        if reset_on_error:
                            reset_supabase_client()

                        # Wait before retrying with exponential backoff
                        time.sleep(delay)
                    else:
                        # Final attempt failed
                        logger.error(
                            f"HTTP/2 connection error during {operation_name} "
                            f"after {max_retries + 1} attempts: {type(e).__name__}: {e}"
                        )
                        raise

            # Should not reach here, but raise last error if we do
            if last_error:
                raise last_error
            raise RuntimeError(f"Unexpected error in {operation_name}")

        return wrapper
    return decorator


def execute_with_retry(
    func: Callable[..., T],
    *args: Any,
    operation_name: str = "database operation",
    max_retries: int = MAX_RETRIES,
    reset_on_error: bool = True,
    **kwargs: Any,
) -> T:
    """
    Execute a function with retry logic for transient HTTP/2 errors.

    This is the imperative version of the @with_db_retry decorator,
    useful for inline retry logic without modifying function definitions.

    Args:
        func: The function to execute
        *args: Positional arguments to pass to the function
        operation_name: Human-readable name for logging
        max_retries: Maximum number of retry attempts
        reset_on_error: Whether to reset the Supabase client on error
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function call

    Example:
        result = execute_with_retry(
            lambda: client.table("users").select("*").execute(),
            operation_name="fetch users"
        )
    """
    @with_db_retry(operation_name, max_retries, reset_on_error)
    def wrapped() -> T:
        return func(*args, **kwargs)

    return wrapped()
