"""
Sentry error context utilities for enhanced error tracking and reporting.

This module provides helper functions and decorators to add structured context
to errors captured by Sentry across the application.
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypeVar

try:
    import sentry_sdk
    from sentry_sdk import capture_exception, set_context, set_tag
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Context variables for request-scoped Sentry context
sentry_context: ContextVar[dict[str, Any]] = ContextVar('sentry_context', default={})  # noqa: B039

F = TypeVar('F', bound=Callable[..., Any])


def set_error_context(context_type: str, data: dict[str, Any]) -> None:
    """
    Set structured context for Sentry error capture.

    Args:
        context_type: Type of context (e.g., 'provider', 'database', 'payment')
        data: Dictionary of contextual information

    Example:
        set_error_context('provider', {
            'provider_name': 'openrouter',
            'endpoint': '/api/chat/completions',
            'request_id': 'req-123'
        })
    """
    if not SENTRY_AVAILABLE:
        return

    try:
        set_context(context_type, data)
    except Exception as e:
        logger.warning(f"Failed to set Sentry context: {e}")


def set_error_tag(key: str, value: str | int | bool) -> None:
    """
    Set a tag for filtering errors in Sentry.

    Args:
        key: Tag key
        value: Tag value

    Example:
        set_error_tag('provider', 'openrouter')
        set_error_tag('request_type', 'chat_completion')
    """
    if not SENTRY_AVAILABLE:
        return

    try:
        set_tag(key, str(value))
    except Exception as e:
        logger.warning(f"Failed to set Sentry tag: {e}")


def capture_error(
    exception: Exception,
    context_type: str | None = None,
    context_data: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
    level: str = "error",
) -> str | None:
    """
    Capture an exception to Sentry with structured context.

    Args:
        exception: The exception to capture
        context_type: Type of context for the error
        context_data: Additional context information
        tags: Dictionary of tags for filtering
        level: Log level ('error', 'warning', 'info', etc.)

    Returns:
        Event ID if captured, None if Sentry is disabled

    Example:
        try:
            make_api_call()
        except APIError as e:
            event_id = capture_error(
                e,
                context_type='provider',
                context_data={'provider': 'openrouter', 'model': 'gpt-4'},
                tags={'provider': 'openrouter', 'error_type': 'api_error'}
            )
    """
    if not SENTRY_AVAILABLE:
        return None

    try:
        if context_type and context_data:
            set_context(context_type, context_data)

        if tags:
            for key, value in tags.items():
                set_tag(key, str(value))

        return capture_exception(exception)
    except Exception as e:
        logger.warning(f"Failed to capture exception to Sentry: {e}")
        return None


def with_sentry_context(
    context_type: str,
    context_fn: Callable[..., dict[str, Any]] | None = None,
):
    """
    Decorator to automatically capture exceptions with Sentry context.

    Args:
        context_type: Type of context for the error
        context_fn: Optional callable that returns context dict given function args/kwargs

    Example:
        @with_sentry_context('provider', lambda provider: {'provider': provider})
        async def make_provider_request(provider: str):
            ...

        @with_sentry_context('database')
        def create_user(email: str):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context_data = {}
                if context_fn:
                    try:
                        context_data = context_fn(*args, **kwargs)
                    except Exception as ctx_err:
                        logger.warning(f"Failed to build context for {func.__name__}: {ctx_err}")

                capture_error(
                    e,
                    context_type=context_type,
                    context_data=context_data,
                    tags={'function': func.__name__}
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context_data = {}
                if context_fn:
                    try:
                        context_data = context_fn(*args, **kwargs)
                    except Exception as ctx_err:
                        logger.warning(f"Failed to build context for {func.__name__}: {ctx_err}")

                capture_error(
                    e,
                    context_type=context_type,
                    context_data=context_data,
                    tags={'function': func.__name__}
                )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore

    return decorator


def capture_provider_error(
    exception: Exception,
    provider: str,
    model: str | None = None,
    request_id: str | None = None,
    endpoint: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a provider-related error with standard context.

    Args:
        exception: The exception to capture
        provider: Provider name (e.g., 'openrouter', 'portkey')
        model: Model ID if applicable
        request_id: Request ID for tracing
        endpoint: API endpoint called
        extra_context: Additional context information to include

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {'provider': provider}
    if model:
        context_data['model'] = model
    if request_id:
        context_data['request_id'] = request_id
    if endpoint:
        context_data['endpoint'] = endpoint
    if extra_context:
        context_data.update(extra_context)

    return capture_error(
        exception,
        context_type='provider',
        context_data=context_data,
        tags={'provider': provider}
    )


def capture_database_error(
    exception: Exception,
    operation: str,
    table: str,
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a database operation error with standard context.

    Args:
        exception: The exception to capture
        operation: Operation type (e.g., 'insert', 'update', 'delete', 'select')
        table: Table name
        details: Additional operation details

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {
        'operation': operation,
        'table': table,
    }
    if details:
        context_data.update(details)

    return capture_error(
        exception,
        context_type='database',
        context_data=context_data,
        tags={'operation': operation, 'table': table}
    )


def capture_payment_error(
    exception: Exception,
    operation: str,
    provider: str = 'stripe',
    user_id: str | None = None,
    amount: float | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a payment-related error with standard context.

    Args:
        exception: The exception to capture
        operation: Payment operation (e.g., 'charge', 'refund', 'webhook')
        provider: Payment provider (default: 'stripe')
        user_id: User ID if applicable
        amount: Transaction amount if applicable
        details: Additional details (customer ID, transaction ID, etc.)

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {
        'operation': operation,
        'provider': provider,
    }
    if user_id:
        context_data['user_id'] = user_id
    if amount:
        context_data['amount'] = amount
    if details:
        context_data.update(details)

    return capture_error(
        exception,
        context_type='payment',
        context_data=context_data,
        tags={'operation': operation, 'provider': provider}
    )


def capture_auth_error(
    exception: Exception,
    operation: str,
    user_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture an authentication/authorization error with standard context.

    Args:
        exception: The exception to capture
        operation: Auth operation (e.g., 'login', 'verify_key', 'validate_token')
        user_id: User ID if applicable
        details: Additional details

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {'operation': operation}
    if user_id:
        context_data['user_id'] = user_id
    if details:
        context_data.update(details)

    return capture_error(
        exception,
        context_type='authentication',
        context_data=context_data,
        tags={'operation': operation}
    )


def capture_cache_error(
    exception: Exception,
    operation: str,
    cache_type: str = 'redis',
    key: str | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a cache operation error with standard context.

    Args:
        exception: The exception to capture
        operation: Cache operation (e.g., 'get', 'set', 'delete')
        cache_type: Type of cache (default: 'redis')
        key: Cache key if applicable
        details: Additional details

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {
        'operation': operation,
        'cache_type': cache_type,
    }
    if key:
        context_data['key'] = key
    if details:
        context_data.update(details)

    return capture_error(
        exception,
        context_type='cache',
        context_data=context_data,
        tags={'operation': operation, 'cache_type': cache_type}
    )


def capture_model_health_error(
    exception: Exception,
    model_id: str,
    provider: str,
    gateway: str,
    operation: str = 'health_check',
    status: str | None = None,
    response_time_ms: float | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a model health/availability error with standard context.

    Args:
        exception: The exception to capture
        model_id: Model identifier
        provider: Provider name (e.g., 'openai', 'anthropic')
        gateway: Gateway name (e.g., 'openrouter', 'portkey')
        operation: Health check operation (default: 'health_check')
        status: Health status when error occurred (e.g., 'unhealthy', 'degraded')
        response_time_ms: Response time in milliseconds if available
        details: Additional details (error_count, success_rate, etc.)

    Returns:
        Event ID if captured, None if Sentry is disabled
    """
    context_data = {
        'model_id': model_id,
        'provider': provider,
        'gateway': gateway,
        'operation': operation,
    }
    if status:
        context_data['status'] = status
    if response_time_ms is not None:
        context_data['response_time_ms'] = response_time_ms
    if details:
        context_data.update(details)

    return capture_error(
        exception,
        context_type='model_health',
        context_data=context_data,
        tags={
            'provider': provider,
            'gateway': gateway,
            'model_id': model_id,
            'operation': operation,
        }
    )
