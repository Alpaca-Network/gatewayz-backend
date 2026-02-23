"""
Request ID Middleware

Middleware to generate and track unique request IDs for all API requests.
Request IDs are used for error tracking, logging, and support queries.

Usage:
    from src.middleware.request_id_middleware import RequestIDMiddleware

    # In main.py
    app.add_middleware(RequestIDMiddleware)

Features:
- Generates unique UUID for each request
- Accepts existing X-Request-ID header if provided
- Attaches request_id to request.state for access in routes
- Adds X-Request-ID to response headers
- Thread-safe and async-compatible
"""

import logging
import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Maximum length for client-supplied request IDs
_MAX_REQUEST_ID_LENGTH = 128

# Pattern to strip control characters (newlines, tabs, etc.) that enable log injection
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Allowlist pattern for validated request IDs: alphanumeric, dots, underscores, hyphens.
# Rejects anything with special characters that survived sanitization (e.g. spaces, quotes,
# angle brackets, percent-encoded sequences, etc.).  Does not require strict UUID v4 format
# so that clients using custom ID schemes (e.g. "trace-abc123", "req.xyz") are still accepted.
_VALID_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to generate and attach request IDs to all requests.

    Request IDs are used throughout the application for:
    - Error tracking and debugging
    - Correlation across distributed systems
    - Support ticket identification
    - Audit logging

    The middleware:
    1. Checks for existing X-Request-ID header
    2. Generates new UUID if not present
    3. Attaches to request.state.request_id
    4. Adds X-Request-ID to response headers
    """

    def __init__(self, app: ASGIApp):
        """
        Initialize the middleware.

        Args:
            app: ASGI application
        """
        super().__init__(app)
        logger.info("RequestIDMiddleware initialized")

    async def dispatch(self, request: Request, call_next):
        """
        Process request and inject request ID.

        Args:
            request: Incoming FastAPI request
            call_next: Next middleware in chain

        Returns:
            Response with X-Request-ID header
        """
        # Generate or extract request ID
        # Priority: X-Request-ID header > X-Correlation-ID > generate new
        raw_request_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or ""
        )

        # Sanitize client-supplied request IDs to prevent log injection:
        # strip control characters (newlines, carriage returns, etc.) and limit length.
        # Then validate the sanitized value against the allowlist pattern; if it does not
        # match (e.g. contains spaces, quotes, angle brackets, or is empty after stripping)
        # discard it and generate a fresh UUID so the bad value never reaches logs or headers.
        if raw_request_id:
            sanitized = _CONTROL_CHARS_RE.sub("", raw_request_id)[:_MAX_REQUEST_ID_LENGTH]
            if _VALID_REQUEST_ID_RE.match(sanitized):
                request_id = sanitized
            else:
                logger.debug(
                    "Client-supplied request ID failed validation and was replaced with a generated one"
                )
                request_id = f"req_{uuid.uuid4().hex[:12]}"
        else:
            request_id = f"req_{uuid.uuid4().hex[:12]}"

        # Normalize to ensure consistent format
        if not request_id.startswith("req_"):
            request_id = f"req_{request_id}"

        # Attach to request state for access in routes
        request.state.request_id = request_id

        # Log request with ID for debugging
        logger.debug(
            f"Request ID: {request_id} | {request.method} {request.url.path}"
        )

        # Call next middleware/route
        try:
            response = await call_next(request)
        except Exception as e:
            # Ensure request_id is available even if request fails
            logger.error(
                f"Request ID: {request_id} | Error during request processing: {e}",
                exc_info=True,
            )
            raise

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        # Also add as X-Correlation-ID for compatibility
        response.headers["X-Correlation-ID"] = request_id

        return response


def get_request_id(request: Request) -> str:
    """
    Get the request ID from the request state.

    Helper function to retrieve request ID in routes and handlers.

    Args:
        request: FastAPI Request object

    Returns:
        Request ID string, or generates new one if not found

    Usage:
        from src.middleware.request_id_middleware import get_request_id

        @router.get("/example")
        async def example(request: Request):
            request_id = get_request_id(request)
            # Use request_id...
    """
    return getattr(request.state, "request_id", f"req_{uuid.uuid4().hex[:12]}")


# Convenience function for backward compatibility
get_correlation_id = get_request_id
