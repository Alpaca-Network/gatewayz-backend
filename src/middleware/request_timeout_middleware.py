"""
Request Timeout Middleware

Prevents individual requests from exceeding a maximum duration, helping to avoid 504 Gateway Timeouts.
This middleware wraps each request with an asyncio timeout to ensure requests complete within acceptable time limits.
"""

import asyncio
import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Default timeout for most requests (in seconds)
# Set to 55 seconds to stay within Vercel's 60-second limit
DEFAULT_REQUEST_TIMEOUT = 55.0

# Paths that are exempt from timeout enforcement (e.g., streaming endpoints)
TIMEOUT_EXEMPT_PATHS = [
    "/v1/chat/completions",  # OpenAI-compatible streaming
    "/v1/messages",  # Anthropic Messages API streaming
    "/ai-sdk/chat/completions",  # AI SDK streaming
]


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce request timeouts and prevent 504 Gateway Timeouts.

    This middleware wraps each request in an asyncio timeout, ensuring that requests
    complete within the specified time limit. This helps prevent gateway timeouts
    when requests take too long to process.
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
        super().__init__(app)
        self.timeout_seconds = timeout_seconds
        self.exempt_paths = exempt_paths or TIMEOUT_EXEMPT_PATHS
        logger.info(
            f"Request timeout middleware initialized with {timeout_seconds}s timeout"
        )

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Process the request with timeout enforcement.

        Args:
            request: The incoming request
            call_next: The next middleware/route handler

        Returns:
            Response from the route handler, or 504 error if timeout occurs
        """
        # Check if path is exempt from timeout
        request_path = request.url.path
        is_exempt = any(request_path.startswith(path) for path in self.exempt_paths)

        if is_exempt:
            # Streaming endpoints and other exempt paths bypass timeout
            return await call_next(request)

        # Enforce timeout for non-exempt requests
        try:
            response = await asyncio.wait_for(
                call_next(request),
                timeout=self.timeout_seconds
            )
            return response
        except asyncio.TimeoutError:
            # Request exceeded timeout - log and return 504
            logger.error(
                f"Request timeout after {self.timeout_seconds}s: "
                f"{request.method} {request_path}"
            )

            # Return 504 Gateway Timeout response
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "message": f"Request exceeded maximum duration of {self.timeout_seconds} seconds",
                        "type": "gateway_timeout",
                        "code": 504,
                    }
                },
                headers={
                    "X-Request-Timeout": str(self.timeout_seconds),
                    "Retry-After": "5",  # Suggest retry after 5 seconds
                }
            )
        except Exception as e:
            # Re-raise other exceptions to be handled by error handlers
            logger.exception(f"Error in request timeout middleware: {e}")
            raise
