"""
Request Timeout Middleware

Prevents individual requests from exceeding a maximum duration, helping to avoid 504 Gateway Timeouts.
This middleware wraps each request with an asyncio timeout to ensure requests complete within acceptable time limits.

Supports tiered timeouts:
- Standard requests: 55s (below Vercel's 60s limit)
- Streaming requests: 300s (bounded but generous for reasoning models)
- Exempt paths: No timeout (health, metrics, admin)

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

# Default streaming timeout (in seconds)
# Bounded but generous — prevents zombie connections while allowing reasoning models
DEFAULT_STREAMING_TIMEOUT = 300.0

# Streaming paths get a longer but bounded timeout (prevents zombie connections)
STREAMING_TIMEOUT_PATHS = [
    "/v1/chat/completions",  # OpenAI-compatible streaming
    "/v1/messages",  # Anthropic Messages API streaming
    "/ai-sdk/chat/completions",  # AI SDK streaming
]

# Truly exempt paths (no timeout at all — monitoring and admin operations)
TIMEOUT_EXEMPT_PATHS = [
    "/admin/",  # Admin operations (model sync, background jobs)
    "/api/catalog",  # Model catalog fetches (can be slow)
    "/health",  # Health checks
    "/metrics",  # Prometheus metrics
]


class RequestTimeoutMiddleware:
    """
    Middleware to enforce request timeouts and prevent 504 Gateway Timeouts.

    This middleware wraps each request in an asyncio timeout, ensuring that requests
    complete within the specified time limit. Streaming endpoints get a longer but
    still bounded timeout to prevent zombie connections from consuming resources.

    This is a pure ASGI middleware to properly support streaming responses.
    """

    def __init__(
        self,
        app: ASGIApp,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT,
        streaming_timeout: float = DEFAULT_STREAMING_TIMEOUT,
        exempt_paths: list[str] | None = None,
        streaming_paths: list[str] | None = None,
    ):
        """
        Initialize the timeout middleware.

        Args:
            app: The ASGI application
            timeout_seconds: Maximum request duration in seconds (default: 55)
            streaming_timeout: Maximum streaming request duration in seconds (default: 300)
            exempt_paths: List of paths fully exempt from timeout
            streaming_paths: List of paths that get the longer streaming timeout
        """
        self.app = app
        self.timeout_seconds = timeout_seconds
        self.streaming_timeout = streaming_timeout
        self.exempt_paths = exempt_paths or TIMEOUT_EXEMPT_PATHS
        self.streaming_paths = streaming_paths or STREAMING_TIMEOUT_PATHS
        logger.info(
            f"Request timeout middleware initialized "
            f"(standard={timeout_seconds}s, streaming={streaming_timeout}s)"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_path = scope["path"]

        # Truly exempt paths bypass all timeouts
        if any(request_path.startswith(path) for path in self.exempt_paths):
            await self.app(scope, receive, send)
            return

        # Streaming paths get a longer but bounded timeout
        is_streaming = any(request_path.startswith(path) for path in self.streaming_paths)
        timeout = self.streaming_timeout if is_streaming else self.timeout_seconds

        # Track whether response headers have already been sent
        # If they have, we cannot send a 504 response (stream already started)
        response_started = False
        original_send = send

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await original_send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, tracked_send),
                timeout=timeout,
            )
        except TimeoutError:
            method = scope.get("method", "UNKNOWN")
            timeout_type = "streaming" if is_streaming else "standard"
            logger.error(
                f"Request timeout ({timeout_type}) after {timeout}s: "
                f"{method} {request_path}"
            )

            # Only send 504 if response headers haven't been sent yet
            # If streaming already started, the connection will just be closed
            if not response_started:
                body = json.dumps(
                    {
                        "error": {
                            "message": f"Request exceeded maximum duration of {timeout} seconds",
                            "type": "gateway_timeout",
                            "code": 504,
                        }
                    }
                ).encode()

                await original_send(
                    {
                        "type": "http.response.start",
                        "status": 504,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"x-request-timeout", str(timeout).encode()),
                            (b"retry-after", b"5"),
                        ],
                    }
                )
                await original_send(
                    {
                        "type": "http.response.body",
                        "body": body,
                    }
                )
            else:
                # Response already started (streaming) — just log, connection will be closed
                logger.warning(
                    f"Streaming timeout after {timeout}s but response already started, "
                    f"closing connection: {method} {request_path}"
                )
        except Exception as e:
            # Re-raise other exceptions to be handled by error handlers
            logger.exception(f"Error in request timeout middleware: {e}")
            raise
