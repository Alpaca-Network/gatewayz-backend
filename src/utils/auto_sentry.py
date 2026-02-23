"""
Automatic Sentry Error Tracking Utilities

This module provides decorators and utilities for automatically capturing
ALL exceptions to Sentry with intelligent context detection.

Usage:
    from src.utils.auto_sentry import auto_capture_errors

    @auto_capture_errors
    async def my_route_handler():
        # All exceptions automatically captured to Sentry
        ...

    @auto_capture_errors(context_type="provider")
    async def make_provider_request():
        # Provider-specific context automatically added
        ...
"""

import asyncio
import functools
import inspect
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypeVar

try:
    import sentry_sdk
    from sentry_sdk import set_context, set_tag

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

from src.utils.sentry_context import (
    capture_auth_error,
    capture_cache_error,
    capture_database_error,
    capture_error,
    capture_payment_error,
    capture_provider_error,
)

logger = logging.getLogger(__name__)

# Context variable to track current request context
request_context: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})  # noqa: B039

F = TypeVar("F", bound=Callable[..., Any])


def auto_capture_errors(  # noqa: UP047
    func: F | None = None,
    *,
    context_type: str | None = None,
    reraise: bool = True,
    capture_locals: bool = False,
):
    """
    Decorator that automatically captures ALL exceptions to Sentry with intelligent context.

    This decorator inspects function arguments and return types to automatically
    determine the appropriate Sentry capture function and context.

    Args:
        func: Function to wrap (used when decorator is called without parentheses)
        context_type: Optional explicit context type (provider, database, payment, auth, cache)
        reraise: Whether to re-raise the exception after capturing (default: True)
        capture_locals: Whether to capture local variables (default: False for security)

    Example:
        @auto_capture_errors
        async def my_function():
            # All exceptions automatically captured
            ...

        @auto_capture_errors(context_type="provider")
        async def make_provider_request(provider: str, model: str):
            # Provider context automatically extracted from args
            ...

        @auto_capture_errors(context_type="database", reraise=False)
        def database_operation():
            # Exception captured but not re-raised
            ...
    """

    def decorator(func_to_wrap: F) -> F:
        @functools.wraps(func_to_wrap)
        async def async_wrapper(*args, **kwargs):
            if not SENTRY_AVAILABLE:
                return await func_to_wrap(*args, **kwargs)

            try:
                return await func_to_wrap(*args, **kwargs)
            except Exception as e:
                # Automatically capture with intelligent context detection
                _auto_capture_exception(
                    e, func_to_wrap, args, kwargs, context_type, capture_locals
                )
                if reraise:
                    raise

        @functools.wraps(func_to_wrap)
        def sync_wrapper(*args, **kwargs):
            if not SENTRY_AVAILABLE:
                return func_to_wrap(*args, **kwargs)

            try:
                return func_to_wrap(*args, **kwargs)
            except Exception as e:
                # Automatically capture with intelligent context detection
                _auto_capture_exception(
                    e, func_to_wrap, args, kwargs, context_type, capture_locals
                )
                if reraise:
                    raise

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func_to_wrap):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore

    # Handle both @decorator and @decorator() syntax
    if func is None:
        # Called with arguments: @auto_capture_errors(context_type="provider")
        return decorator
    else:
        # Called without arguments: @auto_capture_errors
        return decorator(func)


def _auto_capture_exception(
    exception: Exception,
    func: Callable,
    args: tuple,
    kwargs: dict,
    explicit_context_type: str | None,
    capture_locals: bool,
):
    """
    Intelligently capture exception to Sentry by inspecting function context.

    This function automatically:
    - Detects the error type based on function name and arguments
    - Extracts relevant context (provider, model, user_id, etc.)
    - Chooses the appropriate Sentry capture function
    - Adds structured tags and context
    """
    # Get function metadata
    func_name = func.__name__
    module_name = func.__module__
    sig = inspect.signature(func)

    # Bind arguments to parameters for easy access
    try:
        bound_args = sig.bind(*args, **kwargs)
        bound_args.apply_defaults()
        params = bound_args.arguments
    except Exception:
        params = {}

    # Determine context type if not explicitly provided
    context_type = explicit_context_type or _detect_context_type(
        func_name, module_name, params
    )

    # Extract context based on type
    context_data = _extract_context_data(context_type, params, func_name, module_name)

    # Add function metadata
    context_data["function"] = func_name
    context_data["module"] = module_name

    # Capture local variables if requested (be careful with secrets!)
    if capture_locals and not _contains_sensitive_data(params):
        context_data["parameters"] = {
            k: str(v)[:100] for k, v in params.items() if not k.startswith("_")
        }

    # Choose appropriate capture function
    try:
        if context_type == "provider":
            capture_provider_error(
                exception,
                provider=context_data.get("provider", "unknown"),
                model=context_data.get("model"),
                request_id=context_data.get("request_id"),
                endpoint=context_data.get("endpoint"),
            )
        elif context_type == "database":
            capture_database_error(
                exception,
                operation=context_data.get("operation", "unknown"),
                table=context_data.get("table", "unknown"),
                details=context_data,
            )
        elif context_type == "payment":
            capture_payment_error(
                exception,
                operation=context_data.get("operation", "unknown"),
                user_id=context_data.get("user_id"),
                amount=context_data.get("amount"),
                details=context_data,
            )
        elif context_type == "auth":
            capture_auth_error(
                exception,
                operation=context_data.get("operation", "unknown"),
                user_id=context_data.get("user_id"),
                details=context_data,
            )
        elif context_type == "cache":
            capture_cache_error(
                exception,
                operation=context_data.get("operation", "unknown"),
                cache_type=context_data.get("cache_type", "redis"),
                key=context_data.get("key"),
                details=context_data,
            )
        else:
            # Generic capture with context
            capture_error(
                exception,
                context_type=context_type or "general",
                context_data=context_data,
                tags={"function": func_name, "module": module_name},
            )
    except Exception as capture_err:
        # Fallback to basic Sentry capture if our smart capture fails
        logger.warning(f"Smart capture failed: {capture_err}, using basic capture")
        sentry_sdk.capture_exception(exception)


def _detect_context_type(
    func_name: str, module_name: str, params: dict[str, Any]
) -> str:
    """
    Automatically detect the context type based on function name and module.

    Detection rules:
    - Functions with "provider", "client", "request" -> provider
    - Functions in db/ modules or with "database", "table", "query" -> database
    - Functions with "payment", "stripe", "checkout", "webhook" -> payment
    - Functions with "auth", "login", "verify", "token" -> auth
    - Functions with "cache", "redis" -> cache
    """
    func_lower = func_name.lower()
    module_lower = module_name.lower()

    # Provider detection
    if any(
        keyword in func_lower
        for keyword in ["provider", "client", "request", "openrouter", "portkey"]
    ):
        return "provider"
    if "services" in module_lower and "_client" in module_lower:
        return "provider"

    # Database detection (check for .db., /db/, or \db\)
    if ".db." in module_name or "/db/" in module_name or "\\db\\" in module_name:
        return "database"
    if any(
        keyword in func_lower
        for keyword in [
            "database",
            "table",
            "query",
            "insert",
            "update",
            "delete",
            "select",
            "create",
            "supabase",
        ]
    ):
        return "database"

    # Payment detection
    if any(
        keyword in func_lower
        for keyword in [
            "payment",
            "stripe",
            "checkout",
            "webhook",
            "credit",
            "deduct",
            "charge",
        ]
    ):
        return "payment"

    # Auth detection
    if any(
        keyword in func_lower
        for keyword in [
            "auth",
            "login",
            "verify",
            "token",
            "authenticate",
            "privy",
            "api_key",
        ]
    ):
        return "auth"

    # Cache detection
    if any(keyword in func_lower for keyword in ["cache", "redis"]):
        return "cache"

    # Default to general
    return "general"


def _extract_context_data(
    context_type: str, params: dict[str, Any], func_name: str, module_name: str
) -> dict[str, Any]:
    """
    Extract relevant context data from function parameters based on context type.
    """
    context_data = {}

    if context_type == "provider":
        # Extract provider-specific context
        context_data["provider"] = (
            params.get("provider")
            or params.get("provider_name")
            or _extract_provider_from_module(module_name)
        )
        context_data["model"] = params.get("model") or params.get("model_id")
        context_data["request_id"] = params.get("request_id")
        context_data["endpoint"] = params.get("endpoint")

    elif context_type == "database":
        # Extract database context
        context_data["operation"] = _infer_db_operation(func_name)
        context_data["table"] = params.get("table") or params.get("table_name")
        context_data["record_id"] = params.get("id") or params.get("record_id")

    elif context_type == "payment":
        # Extract payment context
        context_data["operation"] = _infer_payment_operation(func_name)
        context_data["user_id"] = params.get("user_id")
        context_data["amount"] = params.get("amount") or params.get("cost")
        context_data["currency"] = params.get("currency", "USD")

    elif context_type == "auth":
        # Extract auth context
        context_data["operation"] = _infer_auth_operation(func_name)
        context_data["user_id"] = params.get("user_id")
        context_data["email"] = params.get("email")

    elif context_type == "cache":
        # Extract cache context
        context_data["operation"] = _infer_cache_operation(func_name)
        context_data["cache_type"] = params.get("cache_type", "redis")
        context_data["key"] = params.get("key") or params.get("cache_key")

    return context_data


def _extract_provider_from_module(module_name: str) -> str:
    """Extract provider name from module path."""
    if "openrouter" in module_name:
        return "openrouter"
    elif "portkey" in module_name:
        return "portkey"
    elif "featherless" in module_name:
        return "featherless"
    elif "chutes" in module_name:
        return "chutes"
    elif "deepinfra" in module_name:
        return "deepinfra"
    elif "fireworks" in module_name:
        return "fireworks"
    elif "together" in module_name:
        return "together"
    elif "huggingface" in module_name:
        return "huggingface"
    elif "google_vertex" in module_name:
        return "google_vertex"
    elif "xai" in module_name:
        return "xai"
    elif "aimo" in module_name:
        return "aimo"
    elif "near" in module_name:
        return "near"
    elif "fal" in module_name:
        return "fal"
    return "unknown"


def _infer_db_operation(func_name: str) -> str:
    """Infer database operation from function name."""
    func_lower = func_name.lower()
    if any(keyword in func_lower for keyword in ["create", "insert", "add"]):
        return "insert"
    elif any(keyword in func_lower for keyword in ["update", "modify", "edit"]):
        return "update"
    elif any(keyword in func_lower for keyword in ["delete", "remove"]):
        return "delete"
    elif any(keyword in func_lower for keyword in ["get", "fetch", "find", "select"]):
        return "select"
    return "unknown"


def _infer_payment_operation(func_name: str) -> str:
    """Infer payment operation from function name."""
    func_lower = func_name.lower()
    if "webhook" in func_lower:
        return "webhook_processing"
    elif "checkout" in func_lower:
        return "checkout"
    elif any(keyword in func_lower for keyword in ["charge", "deduct", "credit"]):
        return "charge"
    elif "refund" in func_lower:
        return "refund"
    return "unknown"


def _infer_auth_operation(func_name: str) -> str:
    """Infer auth operation from function name."""
    func_lower = func_name.lower()
    if "login" in func_lower:
        return "login"
    elif "verify" in func_lower:
        return "verify"
    elif "token" in func_lower:
        return "token_validation"
    elif "authenticate" in func_lower:
        return "authentication"
    return "unknown"


def _infer_cache_operation(func_name: str) -> str:
    """Infer cache operation from function name."""
    func_lower = func_name.lower()
    if "get" in func_lower:
        return "get"
    elif "set" in func_lower:
        return "set"
    elif "delete" in func_lower or "clear" in func_lower:
        return "delete"
    return "unknown"


def _contains_sensitive_data(params: dict[str, Any]) -> bool:
    """
    Check if parameters contain sensitive data that shouldn't be logged.

    Returns True if sensitive data is detected.
    """
    sensitive_keywords = [
        "password",
        "secret",
        "token",
        "api_key",
        "private_key",
        "credit_card",
        "ssn",
        "apikey",
    ]

    for key in params.keys():
        key_lower = key.lower()
        if any(keyword in key_lower for keyword in sensitive_keywords):
            return True

    return False


def set_request_context(context: dict[str, Any]):
    """
    Set request-level context that will be included in all Sentry captures.

    Usage:
        # In middleware or route handler
        set_request_context({
            "user_id": "user-123",
            "request_id": "req-456",
            "endpoint": "/v1/chat/completions"
        })
    """
    request_context.set(context)


def get_request_context() -> dict[str, Any]:
    """Get current request context."""
    return request_context.get()


def clear_request_context():
    """Clear request context (call at end of request)."""
    request_context.set({})
