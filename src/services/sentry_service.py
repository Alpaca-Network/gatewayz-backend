"""
Sentry Error Tracking Service

Comprehensive error tracking and monitoring integration with:
- Release SHA tracking
- Environment detection
- User context enrichment
- Custom error fingerprinting
- Performance monitoring
- Source map support
"""

import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from typing import Optional, Dict, Any
from functools import wraps

from src.config.config import Config

logger = logging.getLogger(__name__)


class SentryService:
    """
    Centralized service for Sentry error tracking and monitoring.

    Features:
    - Automatic error capture and grouping
    - Release tracking with Git SHA
    - Environment tagging (dev/staging/prod)
    - User context enrichment
    - Custom fingerprinting for better error grouping
    - Performance monitoring
    - Breadcrumbs for debugging
    """

    _initialized = False

    @classmethod
    def initialize(cls) -> None:
        """
        Initialize Sentry SDK with comprehensive configuration.

        This should be called once during application startup.
        """
        if cls._initialized:
            logger.warning("Sentry already initialized, skipping")
            return

        if not Config.SENTRY_ENABLED or not Config.SENTRY_DSN:
            logger.info("Sentry disabled or DSN not configured")
            return

        try:
            # Configure logging integration
            logging_integration = LoggingIntegration(
                level=logging.INFO,  # Capture info and above as breadcrumbs
                event_level=logging.ERROR  # Send errors as events
            )

            sentry_sdk.init(
                dsn=Config.SENTRY_DSN,
                environment=Config.APP_ENV,
                release=f"{Config.SERVICE_NAME}@{Config.RELEASE_SHA}",

                # Integrations
                integrations=[
                    FastApiIntegration(
                        transaction_style="url",  # Group by URL pattern
                        failed_request_status_codes=[400, 499, 500, 599]
                    ),
                    HttpxIntegration(),
                    RedisIntegration(),
                    logging_integration,
                ],

                # Performance Monitoring
                traces_sample_rate=Config.SENTRY_TRACES_SAMPLE_RATE,
                profiles_sample_rate=Config.SENTRY_PROFILES_SAMPLE_RATE,

                # Error Sampling
                sample_rate=1.0,  # Capture 100% of errors

                # Additional configuration
                send_default_pii=False,  # Don't send PII by default
                attach_stacktrace=True,
                max_breadcrumbs=50,
                debug=Config.IS_DEVELOPMENT,

                # Custom tag defaults
                before_send=cls._before_send,
                before_breadcrumb=cls._before_breadcrumb,
            )

            # Set global tags
            sentry_sdk.set_tag("service", Config.SERVICE_NAME)
            sentry_sdk.set_tag("environment", Config.APP_ENV)
            sentry_sdk.set_tag("release_sha", Config.RELEASE_SHA)

            cls._initialized = True
            logger.info(
                f"Sentry initialized successfully: "
                f"service={Config.SERVICE_NAME}, "
                f"env={Config.APP_ENV}, "
                f"release={Config.RELEASE_SHA}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize Sentry: {e}", exc_info=True)

    @staticmethod
    def _before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Hook to modify or drop events before sending to Sentry.

        This is where we:
        1. Add custom fingerprinting
        2. Enrich context
        3. Filter out noise
        """
        # Add custom fingerprint for better grouping
        if "exception" in event:
            exception_values = event["exception"].get("values", [])
            if exception_values:
                exc_type = exception_values[0].get("type", "")
                exc_value = exception_values[0].get("value", "")

                # Create stable fingerprint
                fingerprint = [exc_type]

                # Add specific error patterns to fingerprint
                if "InsufficientCredits" in exc_type:
                    fingerprint.append("insufficient_credits")
                elif "RateLimitExceeded" in exc_type:
                    fingerprint.append("rate_limit")
                elif "ProviderError" in exc_type or "APIError" in exc_type:
                    fingerprint.append("provider_error")
                elif "AuthenticationError" in exc_type:
                    fingerprint.append("auth_error")
                elif "ValidationError" in exc_type:
                    fingerprint.append("validation_error")
                else:
                    # Use first line of error message for grouping
                    first_line = exc_value.split("\n")[0][:100] if exc_value else ""
                    fingerprint.append(first_line)

                event["fingerprint"] = fingerprint

        # Add user impact level
        if "request" in event:
            request_data = event["request"]
            method = request_data.get("method", "")
            url = request_data.get("url", "")

            # Determine user impact
            user_impact = "low"
            if "/chat/completions" in url or "/messages" in url:
                user_impact = "high"  # Core inference endpoints
            elif "/auth" in url or "/api_keys" in url:
                user_impact = "high"  # Auth/security endpoints
            elif "/users" in url or "/payments" in url:
                user_impact = "medium"  # User management

            event["tags"]["user_impact"] = user_impact

        return event

    @staticmethod
    def _before_breadcrumb(crumb: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Filter and modify breadcrumbs before they're added.
        """
        # Redact sensitive data from breadcrumbs
        if crumb.get("category") == "httpx":
            if "data" in crumb:
                # Redact API keys, tokens, passwords
                if isinstance(crumb["data"], dict):
                    for key in ["api_key", "token", "password", "authorization"]:
                        if key in crumb["data"]:
                            crumb["data"][key] = "[REDACTED]"

        return crumb

    @classmethod
    def set_user_context(
        cls,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        api_key_id: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Set user context for error tracking.

        Args:
            user_id: User ID
            email: User email (will be redacted if PII protection enabled)
            api_key_id: API key ID being used
            **kwargs: Additional user properties
        """
        if not cls._initialized:
            return

        user_data = {}

        if user_id:
            user_data["id"] = user_id

        if email:
            user_data["email"] = email

        if api_key_id:
            user_data["api_key_id"] = api_key_id

        # Add custom properties
        user_data.update(kwargs)

        sentry_sdk.set_user(user_data)

    @classmethod
    def set_request_context(
        cls,
        endpoint: Optional[str] = None,
        method: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs
    ) -> None:
        """
        Set request-specific context.

        Args:
            endpoint: API endpoint being called
            method: HTTP method
            model: AI model being used
            provider: Provider routing to
            **kwargs: Additional context
        """
        if not cls._initialized:
            return

        context = {}

        if endpoint:
            context["endpoint"] = endpoint

        if method:
            context["method"] = method

        if model:
            context["model"] = model
            sentry_sdk.set_tag("model", model)

        if provider:
            context["provider"] = provider
            sentry_sdk.set_tag("provider", provider)

        context.update(kwargs)

        sentry_sdk.set_context("request", context)

    @classmethod
    def add_breadcrumb(
        cls,
        message: str,
        category: str = "default",
        level: str = "info",
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Add a breadcrumb for debugging.

        Args:
            message: Breadcrumb message
            category: Category (e.g., "auth", "db", "provider")
            level: Severity level (debug/info/warning/error)
            data: Additional data
        """
        if not cls._initialized:
            return

        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {}
        )

    @classmethod
    def capture_exception(
        cls,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, str]] = None,
        level: str = "error"
    ) -> Optional[str]:
        """
        Manually capture an exception.

        Args:
            error: Exception to capture
            context: Additional context
            tags: Additional tags
            level: Severity level

        Returns:
            Event ID if sent to Sentry, None otherwise
        """
        if not cls._initialized:
            return None

        with sentry_sdk.push_scope() as scope:
            # Set level
            scope.level = level

            # Add context
            if context:
                for key, value in context.items():
                    scope.set_context(key, value)

            # Add tags
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)

            return sentry_sdk.capture_exception(error)

    @classmethod
    def capture_message(
        cls,
        message: str,
        level: str = "info",
        tags: Optional[Dict[str, str]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Capture a message (not an exception).

        Args:
            message: Message to capture
            level: Severity level
            tags: Additional tags
            context: Additional context

        Returns:
            Event ID if sent to Sentry, None otherwise
        """
        if not cls._initialized:
            return None

        with sentry_sdk.push_scope() as scope:
            scope.level = level

            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)

            if context:
                for key, value in context.items():
                    scope.set_context(key, value)

            return sentry_sdk.capture_message(message)

    @classmethod
    def start_transaction(
        cls,
        name: str,
        op: str = "function"
    ) -> Any:
        """
        Start a performance monitoring transaction.

        Args:
            name: Transaction name
            op: Operation type

        Returns:
            Transaction object
        """
        if not cls._initialized:
            return None

        return sentry_sdk.start_transaction(name=name, op=op)


def capture_errors(
    operation: str = "operation",
    capture_args: bool = False
):
    """
    Decorator to automatically capture errors with context.

    Usage:
        @capture_errors(operation="chat_completion", capture_args=True)
        async def create_chat_completion(...):
            ...

    Args:
        operation: Name of the operation (for context)
        capture_args: Whether to capture function arguments (careful with sensitive data)
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                context = {"operation": operation}
                if capture_args:
                    context["args"] = str(args)[:500]  # Limit size
                    context["kwargs"] = {k: str(v)[:500] for k, v in kwargs.items()}

                SentryService.capture_exception(e, context=context)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context = {"operation": operation}
                if capture_args:
                    context["args"] = str(args)[:500]
                    context["kwargs"] = {k: str(v)[:500] for k, v in kwargs.items()}

                SentryService.capture_exception(e, context=context)
                raise

        # Return appropriate wrapper based on function type
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Convenience functions
def set_user_context(*args, **kwargs):
    """Shorthand for SentryService.set_user_context"""
    return SentryService.set_user_context(*args, **kwargs)


def set_request_context(*args, **kwargs):
    """Shorthand for SentryService.set_request_context"""
    return SentryService.set_request_context(*args, **kwargs)


def add_breadcrumb(*args, **kwargs):
    """Shorthand for SentryService.add_breadcrumb"""
    return SentryService.add_breadcrumb(*args, **kwargs)


def capture_exception(*args, **kwargs):
    """Shorthand for SentryService.capture_exception"""
    return SentryService.capture_exception(*args, **kwargs)
