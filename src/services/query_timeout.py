"""Query timeout utilities for Supabase operations.

This module provides timeout guards for database operations to prevent
the authentication endpoint from hanging indefinitely.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# Default timeout for database queries (in seconds)
DEFAULT_QUERY_TIMEOUT = 10
AUTH_QUERY_TIMEOUT = 8  # Stricter timeout for auth operations
USER_LOOKUP_TIMEOUT = 5  # Very fast lookup should not take long

T = TypeVar("T")


class QueryTimeoutError(Exception):
    """Raised when a database query exceeds the timeout threshold."""

    pass


def execute_with_timeout(  # noqa: UP047
    func: Callable[..., T],
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT,
    operation_name: str = "database operation",
) -> T:
    """Execute a function with a timeout guard.

    This is a simple synchronous timeout mechanism suitable for blocking
    Supabase queries. For truly async operations, consider using asyncio.wait_for().

    Args:
        func: The callable to execute
        timeout_seconds: Maximum time to wait for completion
        operation_name: Human-readable name for logging

    Returns:
        The result of the function call

    Raises:
        QueryTimeoutError: If the operation exceeds the timeout

    Example:
        >>> def slow_query():
        ...     result = client.table("users").select("*").execute()
        ...     return result
        >>>
        >>> try:
        ...     result = execute_with_timeout(slow_query, timeout_seconds=5, operation_name="user lookup")
        ... except QueryTimeoutError:
        ...     logger.error("User lookup timed out")
        ...     raise
    """
    result = [None]
    exception = [None]
    event = threading.Event()

    def target():
        try:
            result[0] = func()
        except Exception as e:
            exception[0] = e
        finally:
            event.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    if not event.wait(timeout=timeout_seconds):
        logger.error(
            f"Query timeout: {operation_name} exceeded {timeout_seconds}s threshold"
        )
        raise QueryTimeoutError(
            f"{operation_name} exceeded timeout of {timeout_seconds}s"
        )

    if exception[0]:
        raise exception[0]

    return result[0]


def safe_query_with_timeout(
    client: Any,
    table_name: str,
    operation: Callable,
    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT,
    operation_name: str = "database operation",
    fallback_value: Any = None,
    log_errors: bool = True,
) -> Any:
    """Execute a Supabase query with timeout and fallback value.

    This wraps execute_with_timeout with better error handling for Supabase operations.

    Args:
        client: Supabase client
        table_name: Name of the table being queried (for logging)
        operation: Callable that performs the Supabase query
        timeout_seconds: Maximum time to wait
        operation_name: Human-readable operation name
        fallback_value: Value to return if operation times out
        log_errors: Whether to log errors

    Returns:
        Query result or fallback_value if timeout occurs

    Example:
        >>> result = safe_query_with_timeout(
        ...     client,
        ...     "users",
        ...     lambda: client.table("users").select("*").eq("id", user_id).execute(),
        ...     timeout_seconds=5,
        ...     operation_name="fetch user",
        ...     fallback_value=None,
        ... )
    """
    try:
        return execute_with_timeout(
            operation,
            timeout_seconds=timeout_seconds,
            operation_name=f"{operation_name} on table {table_name}",
        )
    except QueryTimeoutError:
        if log_errors:
            logger.warning(
                f"Query timeout on {table_name}: {operation_name}. "
                f"Returning fallback value: {fallback_value}"
            )
        return fallback_value
    except Exception as e:
        if log_errors:
            logger.error(f"Query error on {table_name}: {operation_name}. Error: {e}")
        return fallback_value
