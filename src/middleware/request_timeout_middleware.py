"""
Request Timeout Middleware

Prevents individual requests from exceeding a maximum duration, helping to avoid 504 Gateway Timeouts.
This middleware wraps each request with an asyncio timeout to ensure requests complete within acceptable time limits.

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.
"""

import asyncio
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Default timeout for most requests (in seconds)
# Set to 55 seconds to stay within Vercel's 60-second limit
DEFAULT_REQUEST_TIMEOUT = 55.0

# Paths that are exempt from timeout enforcement (e.g., streaming endpoints, admin operations)
TIMEOUT_EXEMPT_PATHS = [
    "/v1/chat/completions",  # OpenAI-compatible streaming
    "/v1/messages",  # Anthropic Messages API streaming
    "/ai-sdk/chat/completions",  # AI SDK streaming
    "/admin/",  # Admin operations (model sync, background jobs)
    "/api/catalog",  # Model catalog fetches (can be slow)
    "/v1/models",  # Model listing (can be slow with many providers)
    "/health",  # Health checks
    "/metrics",  # Prometheus metrics
]


class RequestTimeoutMiddleware:
    """
    Middleware to enforce request timeouts and prevent 504 Gateway Timeouts.

    This middleware wraps each request in an asyncio timeout, ensuring that requests
    complete within the specified time limit. This helps prevent gateway timeouts
    when requests take too long to process.

    This is a pure ASGI middleware to properly support streaming responses.
    """

    def __init__(
        self,
        app: ASGIApp,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT,
        exempt_paths: list[str] | None = None,
    ):
        """
        Initialize the timeout middleware.

        Args:
            app: The ASGI application
            timeout_seconds: Maximum request duration in seconds (default: 55)
            exempt_paths: List of paths exempt from timeout (e.g., streaming endpoints)
        """
        self.app = app
        self.timeout_seconds = timeout_seconds
        self.exempt_paths = exempt_paths or TIMEOUT_EXEMPT_PATHS
        logger.info(
            f"Request timeout middleware initialized with {timeout_seconds}s timeout"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check if path is exempt from timeout
        request_path = scope["path"]
        is_exempt = any(request_path.startswith(path) for path in self.exempt_paths)

        if is_exempt:
            # Streaming endpoints and other exempt paths bypass timeout
            await self.app(scope, receive, send)
            return

        # Enforce timeout for non-exempt requests
        try:
            await asyncio.wait_for(
                self.app(scope, receive, send),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            # Request exceeded timeout - log and return 504
            method = scope.get("method", "UNKNOWN")
            logger.error(
                f"Request timeout after {self.timeout_seconds}s: "
                f"{method} {request_path}"
            )

            # Send 504 Gateway Timeout response
            body = json.dumps(
                {
                    "error": {
                        "message": f"Request exceeded maximum duration of {self.timeout_seconds} seconds",
                        "type": "gateway_timeout",
                        "code": 504,
                    }
                }
            ).encode()

            await send(
                {
                    "type": "http.response.start",
                    "status": 504,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"x-request-timeout", str(self.timeout_seconds).encode()),
                        (b"retry-after", b"5"),  # Suggest retry after 5 seconds
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                }
            )
        except Exception as e:
            # Re-raise other exceptions to be handled by error handlers
            logger.exception(f"Error in request timeout middleware: {e}")
            raise
