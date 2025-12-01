"""
Automatic Sentry Error Capture Middleware

This middleware automatically captures ALL unhandled exceptions from routes
to Sentry with intelligent context extraction.

Features:
- Captures all route-level exceptions automatically
- Extracts request context (method, path, headers, user info)
- Intelligently detects error type (provider, payment, auth, etc.)
- No code changes required in route handlers
- Works alongside global exception handler
"""

import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

try:
    import sentry_sdk
    from sentry_sdk import set_context, set_tag

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

logger = logging.getLogger(__name__)


class AutoSentryMiddleware(BaseHTTPMiddleware):
    """
    Middleware that automatically captures all route exceptions to Sentry.

    This middleware:
    1. Sets request context before each request
    2. Captures any unhandled exceptions with full context
    3. Adds intelligent tags based on endpoint type
    4. Extracts user/API key information safely
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not SENTRY_AVAILABLE:
            return await call_next(request)

        # Start timing
        start_time = time.time()

        # Extract request context
        request_context = self._extract_request_context(request)

        # Set Sentry context for this request
        with sentry_sdk.push_scope() as scope:
            # Add request context
            scope.set_context("request", request_context)

            # Add tags for filtering
            scope.set_tag("endpoint", request_context["path"])
            scope.set_tag("method", request_context["method"])
            scope.set_tag("endpoint_type", request_context["endpoint_type"])

            # Add user context if available
            user_context = self._extract_user_context(request)
            if user_context:
                scope.set_user(user_context)
                scope.set_tag("has_user", "true")
            else:
                scope.set_tag("has_user", "false")

            try:
                # Process request
                response = await call_next(request)

                # Add response context
                duration_ms = (time.time() - start_time) * 1000
                scope.set_context(
                    "response",
                    {
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )

                # Track slow requests (>5s) as breadcrumbs
                if duration_ms > 5000:
                    sentry_sdk.add_breadcrumb(
                        category="performance",
                        message=f"Slow request: {request_context['path']}",
                        level="warning",
                        data={
                            "duration_ms": duration_ms,
                            "endpoint": request_context["path"],
                        },
                    )

                return response

            except Exception as exc:
                from fastapi import HTTPException

                # Filter HTTPException based on status code:
                # - 4xx errors are intentional user-facing errors (401, 403, 404, 422) - don't send to Sentry
                # - 5xx errors are server errors and should be captured for investigation
                if isinstance(exc, HTTPException):
                    if exc.status_code < 500:
                        # Client error - log for debugging but don't send to Sentry
                        logger.debug(
                            f"HTTP exception in {request_context['path']}: {exc.status_code} - {exc.detail}",
                            extra={
                                "status_code": exc.status_code,
                                "detail": exc.detail,
                                "request_context": request_context,
                            },
                        )
                        # Re-raise without Sentry capture
                        raise
                    # For 5xx errors, continue to Sentry capture below

                # Exception occurred - capture to Sentry with full context
                duration_ms = (time.time() - start_time) * 1000

                # Add exception context
                scope.set_context(
                    "exception_context",
                    {
                        "duration_ms": duration_ms,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )

                # Add specific tags based on exception type
                self._add_exception_tags(scope, exc, request_context)

                # Capture exception
                sentry_sdk.capture_exception(exc)

                # Log the error
                logger.error(
                    f"Unhandled exception in {request_context['path']}: {exc}",
                    exc_info=True,
                    extra={
                        "request_context": request_context,
                        "duration_ms": duration_ms,
                    },
                )

                # Re-raise to let global exception handler format response
                raise

    def _extract_request_context(self, request: Request) -> dict:
        """
        Extract relevant request context for Sentry.

        Returns sanitized request information (no sensitive data).
        """
        # Determine endpoint type
        endpoint_type = self._determine_endpoint_type(request.url.path)

        context = {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "endpoint_type": endpoint_type,
            "client_host": request.client.host if request.client else "unknown",
            "headers": self._sanitize_headers(dict(request.headers)),
        }

        # Add route info if available
        if hasattr(request, "scope") and "route" in request.scope:
            route = request.scope["route"]
            if hasattr(route, "path"):
                context["route_path"] = route.path

        return context

    def _extract_user_context(self, request: Request) -> dict | None:
        """
        Extract user context from request state or headers.

        Returns user information if available (email, ID, etc.).
        Does NOT include sensitive data like passwords or API keys.
        """
        user_context = {}

        # Try to get user from request state (set by auth middleware)
        if hasattr(request.state, "user_id"):
            user_context["id"] = request.state.user_id
        if hasattr(request.state, "email"):
            user_context["email"] = request.state.email
        if hasattr(request.state, "api_key_id"):
            user_context["api_key_id"] = request.state.api_key_id

        # Try to get user from authorization header (hash it for privacy)
        if "authorization" in request.headers:
            import hashlib

            auth_header = request.headers["authorization"]
            # Create a hash of the API key for tracking (not the actual key!)
            auth_hash = hashlib.sha256(auth_header.encode()).hexdigest()[:16]
            user_context["api_key_hash"] = auth_hash

        return user_context if user_context else None

    def _sanitize_headers(self, headers: dict) -> dict:
        """
        Sanitize headers to remove sensitive information.

        Removes: Authorization, API keys, cookies, etc.
        """
        sensitive_headers = [
            "authorization",
            "cookie",
            "x-api-key",
            "api-key",
            "apikey",
            "token",
            "x-auth-token",
        ]

        sanitized = {}
        for key, value in headers.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in sensitive_headers):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value

        return sanitized

    def _determine_endpoint_type(self, path: str) -> str:
        """
        Determine the type of endpoint based on path.

        Used for intelligent error categorization.
        """
        path_lower = path.lower()

        # Critical inference endpoints
        if "/v1/chat/completions" in path_lower:
            return "inference_chat"
        elif "/v1/messages" in path_lower:
            return "inference_messages"
        elif "/v1/images" in path_lower:
            return "inference_images"

        # Payment endpoints
        elif "/payment" in path_lower or "/stripe" in path_lower:
            return "payment"
        elif "/checkout" in path_lower:
            return "checkout"

        # Auth endpoints
        elif "/auth" in path_lower or "/login" in path_lower:
            return "authentication"
        elif "/api/keys" in path_lower or "/api-keys" in path_lower:
            return "api_key_management"

        # Admin endpoints (check before /users to catch /admin/users)
        elif "/admin" in path_lower:
            return "admin"

        # User management
        elif "/users" in path_lower:
            return "user_management"

        # Catalog/discovery
        elif "/catalog" in path_lower or "/models" in path_lower:
            return "catalog"
        elif "/providers" in path_lower:
            return "providers"

        # Monitoring
        elif "/health" in path_lower:
            return "health_check"
        elif "/metrics" in path_lower:
            return "metrics"
        elif "/monitoring" in path_lower:
            return "monitoring"

        # Other
        return "general"

    def _add_exception_tags(
        self, scope, exception: Exception, request_context: dict
    ):
        """
        Add intelligent tags based on exception type and context.

        This helps filter and categorize errors in Sentry.
        """
        exc_type = type(exception).__name__
        scope.set_tag("exception_type", exc_type)

        # HTTP-related exceptions
        if "HTTPException" in exc_type:
            if hasattr(exception, "status_code"):
                scope.set_tag("http_status", str(exception.status_code))
                scope.set_tag("error_category", self._categorize_http_error(exception.status_code))

        # Provider-related errors
        elif any(
            keyword in exc_type.lower()
            for keyword in ["timeout", "connection", "network", "httpx", "request"]
        ):
            scope.set_tag("error_category", "provider_error")
            scope.set_tag("is_timeout", "timeout" in exc_type.lower())

        # Database errors
        elif any(
            keyword in exc_type.lower()
            for keyword in ["database", "supabase", "postgres", "query"]
        ):
            scope.set_tag("error_category", "database_error")

        # Payment errors
        elif any(keyword in exc_type.lower() for keyword in ["stripe", "payment"]):
            scope.set_tag("error_category", "payment_error")

        # Add endpoint-based category
        endpoint_type = request_context.get("endpoint_type", "general")
        if endpoint_type in ["inference_chat", "inference_messages", "inference_images"]:
            scope.set_tag("is_revenue_critical", "true")
        elif endpoint_type == "payment":
            scope.set_tag("is_revenue_critical", "true")
        else:
            scope.set_tag("is_revenue_critical", "false")

    def _categorize_http_error(self, status_code: int) -> str:
        """Categorize HTTP error by status code."""
        if status_code < 400:
            return "success"
        elif status_code < 500:
            return "client_error"
        else:
            return "server_error"
