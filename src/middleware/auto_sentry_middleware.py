"""
Automatic Sentry Error Capture Middleware

This middleware automatically captures ALL unhandled exceptions from routes
to Sentry with intelligent context extraction.

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.

Features:
- Captures all route-level exceptions automatically
- Extracts request context (method, path, headers, user info)
- Intelligently detects error type (provider, payment, auth, etc.)
- No code changes required in route handlers
- Works alongside global exception handler
"""

import hashlib
import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

logger = logging.getLogger(__name__)


class AutoSentryMiddleware:
    """
    Middleware that automatically captures all route exceptions to Sentry.

    This middleware:
    1. Sets request context before each request
    2. Captures any unhandled exceptions with full context
    3. Adds intelligent tags based on endpoint type
    4. Extracts user/API key information safely

    This is a pure ASGI middleware to properly support streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not SENTRY_AVAILABLE:
            await self.app(scope, receive, send)
            return

        # Start timing
        start_time = time.time()

        # Extract request context
        request_context = self._extract_request_context(scope)

        # Track status code from response
        status_code = None

        async def send_with_tracking(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        # Set Sentry context for this request
        with sentry_sdk.push_scope() as sentry_scope:
            # Add request context
            sentry_scope.set_context("request", request_context)

            # Add tags for filtering
            sentry_scope.set_tag("endpoint", request_context["path"])
            sentry_scope.set_tag("method", request_context["method"])
            sentry_scope.set_tag("endpoint_type", request_context["endpoint_type"])

            # Add user context if available
            user_context = self._extract_user_context(scope)
            if user_context:
                sentry_scope.set_user(user_context)
                sentry_scope.set_tag("has_user", "true")
            else:
                sentry_scope.set_tag("has_user", "false")

            try:
                # Process request
                await self.app(scope, receive, send_with_tracking)

                # Add response context
                duration_ms = (time.time() - start_time) * 1000
                sentry_scope.set_context(
                    "response",
                    {
                        "status_code": status_code,
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

            except Exception as exc:
                from fastapi import HTTPException

                # Don't capture HTTPException to Sentry - these are intentional user-facing errors
                if isinstance(exc, HTTPException):
                    # Log for debugging but don't send to Sentry
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

                # Exception occurred - capture to Sentry with full context
                duration_ms = (time.time() - start_time) * 1000

                # Add exception context
                sentry_scope.set_context(
                    "exception_context",
                    {
                        "duration_ms": duration_ms,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )

                # Add specific tags based on exception type
                self._add_exception_tags(sentry_scope, exc, request_context)

                # Deduplication: route-level error handlers (e.g. global exception
                # handlers in routes/) may have already reported this exception to
                # Sentry by setting `exc._sentry_reported = True` before re-raising.
                # Checking that flag here prevents double-reporting the same event.
                if not getattr(exc, "_sentry_reported", False):
                    sentry_sdk.capture_exception(exc)
                    # Mark as reported so any outer middleware layers also skip it.
                    try:
                        exc._sentry_reported = True
                    except AttributeError:
                        # Some built-in exception types are read-only; safe to ignore.
                        pass

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

    def _extract_request_context(self, scope: Scope) -> dict:
        """
        Extract relevant request context for Sentry.

        Returns sanitized request information (no sensitive data).
        """
        path = scope["path"]
        method = scope["method"]

        # Determine endpoint type
        endpoint_type = self._determine_endpoint_type(path)

        # Get headers
        headers = dict(scope.get("headers", []))
        sanitized_headers = self._sanitize_headers(headers)

        # Get client info
        client = scope.get("client")
        client_host = client[0] if client else "unknown"

        # Get query params
        query_string = scope.get("query_string", b"").decode()
        query_params = {}
        if query_string:
            for param in query_string.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    query_params[key] = value

        context = {
            "method": method,
            "path": path,
            "query_params": query_params,
            "endpoint_type": endpoint_type,
            "client_host": client_host,
            "headers": sanitized_headers,
        }

        # Add route info if available
        if "route" in scope:
            route = scope["route"]
            if hasattr(route, "path"):
                context["route_path"] = route.path

        return context

    def _extract_user_context(self, scope: Scope) -> dict | None:
        """
        Extract user context from scope or headers.

        Returns user information if available (email, ID, etc.).
        Does NOT include sensitive data like passwords or API keys.
        """
        user_context = {}

        # Try to get user from scope state (set by auth middleware)
        # State can be either a Starlette State object (with attributes) or a dict
        state = scope.get("state")
        if state is not None:
            # Handle both object-style (State) and dict-style access
            if hasattr(state, "user_id"):
                user_context["id"] = state.user_id
            elif isinstance(state, dict) and "user_id" in state:
                user_context["id"] = state["user_id"]

            if hasattr(state, "email"):
                user_context["email"] = state.email
            elif isinstance(state, dict) and "email" in state:
                user_context["email"] = state["email"]

            if hasattr(state, "api_key_id"):
                user_context["api_key_id"] = state.api_key_id
            elif isinstance(state, dict) and "api_key_id" in state:
                user_context["api_key_id"] = state["api_key_id"]

        # Try to get user from authorization header (hash it for privacy)
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        if auth_header:
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
            b"authorization",
            b"cookie",
            b"x-api-key",
            b"api-key",
            b"apikey",
            b"token",
            b"x-auth-token",
        ]

        sanitized = {}
        for key, value in headers.items():
            key_lower = key.lower() if isinstance(key, bytes) else key.lower().encode()
            if any(sensitive in key_lower for sensitive in sensitive_headers):
                sanitized[key.decode() if isinstance(key, bytes) else key] = "[REDACTED]"
            else:
                sanitized[key.decode() if isinstance(key, bytes) else key] = (
                    value.decode() if isinstance(value, bytes) else value
                )

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

    def _add_exception_tags(self, scope, exception: Exception, request_context: dict):
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
            scope.set_tag("is_timeout", str("timeout" in exc_type.lower()).lower())

        # Database errors
        elif any(
            keyword in exc_type.lower() for keyword in ["database", "supabase", "postgres", "query"]
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
