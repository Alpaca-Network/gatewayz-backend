"""
FastAPI middleware for automatic observability instrumentation.

This middleware automatically tracks HTTP request metrics for all endpoints
without requiring manual instrumentation. It exposes metrics compatible with
the Grafana FastAPI Observability Dashboard (ID: 16110).

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.

Metrics exposed:
- fastapi_requests_in_progress: Gauge of concurrent requests by method and endpoint
- fastapi_request_size_bytes: Histogram of request body sizes by method and endpoint
- fastapi_response_size_bytes: Histogram of response body sizes by method and endpoint
- http_requests_total: Counter of total requests by method, endpoint, and status code
- http_request_duration_seconds: Histogram of request duration by method and endpoint
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.services.prometheus_metrics import (
    APP_NAME,
    fastapi_request_size_bytes,
    fastapi_requests_duration_seconds,
    fastapi_requests_in_progress,
    fastapi_response_size_bytes,
    get_trace_exemplar,
    http_request_duration,
    record_http_response,
)

# Pyroscope tag_wrapper is imported lazily inside __call__ via pyroscope_config
# so the middleware can be loaded even when pyroscope-io is not installed.
from src.services.pyroscope_config import tag_wrapper as _pyroscope_tag_wrapper

logger = logging.getLogger(__name__)


class ObservabilityMiddleware:
    """
    Middleware for automatic request/response observability.

    Automatically tracks:
    - Request duration and size
    - Response size and status code
    - Concurrent request count
    - All metrics with method and endpoint labels

    This middleware should be added early in the middleware stack to capture
    accurate timing for all requests.

    This is a pure ASGI middleware to properly support streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope["path"]

        # Skip metrics collection for metrics endpoint to avoid recursion
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        # Normalize path for metrics (group dynamic segments)
        endpoint = self._normalize_path(path)

        # Track request body size using Content-Length header
        headers = dict(scope.get("headers", []))
        try:
            request_content_length = headers.get(b"content-length", b"").decode()
            request_size = int(request_content_length) if request_content_length else 0
            fastapi_request_size_bytes.labels(
                app_name=APP_NAME, method=method, path=endpoint
            ).observe(request_size)
        except (ValueError, TypeError) as e:
            logger.debug(f"Could not determine request size from headers: {e}")
            request_size = 0

        # Increment in-progress requests gauge
        fastapi_requests_in_progress.labels(app_name=APP_NAME, method=method, path=endpoint).inc()

        # Record start time
        start_time = time.time()

        # Track status code and response size from response
        status_code = 500  # Default to 500 if we don't get a response
        response_size = 0
        is_streaming = False

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code, response_size, is_streaming

            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)

                # Try to get content length from response headers
                # Use safe decoding to handle potentially malformed headers
                response_headers = dict(message.get("headers", []))
                try:
                    response_content_length = response_headers.get(b"content-length", b"").decode(
                        "utf-8", errors="replace"
                    )
                    content_type = (
                        response_headers.get(b"content-type", b"")
                        .decode("utf-8", errors="replace")
                        .lower()
                    )
                except (AttributeError, UnicodeDecodeError):
                    response_content_length = ""
                    content_type = ""

                if response_content_length:
                    try:
                        response_size = int(response_content_length)
                    except ValueError:
                        response_size = 0
                else:
                    # Check if this is a streaming response
                    try:
                        x_accel = (
                            response_headers.get(b"x-accel-buffering", b"")
                            .decode("utf-8", errors="replace")
                            .lower()
                        )
                    except (AttributeError, UnicodeDecodeError):
                        x_accel = ""
                    is_streaming = "text/event-stream" in content_type or x_accel == "no"

            elif message["type"] == "http.response.body":
                # Accumulate body size for non-streaming responses
                if not is_streaming:
                    body = message.get("body", b"")
                    if body:
                        response_size += len(body)

            await send(message)

        try:
            # Tag every Pyroscope sample taken during this request with the
            # normalised endpoint and HTTP method.  This lets you filter the
            # Grafana flamegraph to a specific route and ask:
            #   "Which Python functions consume the most CPU on POST /v1/chat/completions?"
            # For streaming responses the context stays open for the full SSE
            # window, so slow streaming code shows up correctly in the profile.
            # Falls back to nullcontext() when Pyroscope is disabled or not installed.
            with _pyroscope_tag_wrapper({"endpoint": endpoint, "method": method}):
                await self.app(scope, receive, send_with_metrics)

            # Record response size metric
            fastapi_response_size_bytes.labels(
                app_name=APP_NAME, method=method, path=endpoint
            ).observe(response_size)

        except Exception as e:
            # Record error metrics for any unhandled exception
            logger.error(f"Error processing request {method} {endpoint}: {e}")
            status_code = 500
            raise

        finally:
            # Always record duration and decrement in-progress gauge
            duration = time.time() - start_time
            exemplar = get_trace_exemplar()

            # Record HTTP metrics (both new and legacy) with exemplars for
            # metrics→traces correlation (click a datapoint in Grafana → Tempo trace)
            fastapi_requests_duration_seconds.labels(
                app_name=APP_NAME, method=method, path=endpoint
            ).observe(duration, exemplar=exemplar)
            http_request_duration.labels(method=method, endpoint=endpoint).observe(
                duration, exemplar=exemplar
            )
            record_http_response(
                method=method, endpoint=endpoint, status_code=status_code, app_name=APP_NAME
            )

            # Slow request logging for performance diagnostics
            # 10s is a conservative threshold for "unusually slow" non-streaming requests
            # We skip this for streaming to avoid noise
            if duration > 10.0 and not is_streaming and not path == "/metrics":
                logger.warning(
                    f"🐢 SLOW REQUEST: {method} {path} took {duration:.2f}s "
                    f"(status={status_code}, size={response_size}b)"
                )

            # Decrement in-progress requests gauge
            fastapi_requests_in_progress.labels(
                app_name=APP_NAME, method=method, path=endpoint
            ).dec()

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Normalize URL path for metrics labeling.

        This prevents high cardinality metrics by grouping dynamic path segments.
        For example:
        - /v1/chat/completions -> /v1/chat/completions
        - /users/123 -> /users/{id}
        - /api/models/gpt-4 -> /api/models/{name}

        Args:
            path: The URL path to normalize

        Returns:
            Normalized path suitable for metric labels
        """
        if not path:
            return "/"

        parts = path.split("/")
        normalized_parts = []

        for part in parts:
            if not part:  # Skip empty parts from leading/trailing slashes
                continue

            # Remove query string from path segment if present
            # (middleware receives path without query string, but just in case)
            part = part.split("?")[0] if "?" in part else part

            # Check if this looks like a numeric ID (all digits)
            if part.isdigit():
                # Replace numeric segments with {id}
                normalized_parts.append("{id}")
            # Check if this looks like a UUID (36 chars: 8-4-4-4-12 hex with hyphens)
            elif len(part) == 36 and all(c in "0123456789abcdef-" for c in part.lower()):
                # UUID format detected, replace with {id}
                normalized_parts.append("{id}")
            # Check if this looks like a hex string ID (hex characters without hyphens)
            # This includes hash-like IDs but must be reasonably long
            elif len(part) > 8 and all(c in "0123456789abcdef" for c in part.lower()):
                # Replace hex ID segments with {id}
                normalized_parts.append("{id}")
            # Check if it looks like a model name or similar (contains hyphens but not all digits)
            elif "-" in part and not part.isdigit():
                # Likely a model name or identifier, keep as is
                # e.g., "gpt-4-turbo" -> "gpt-4-turbo"
                normalized_parts.append(part)
            else:
                # Keep regular path segments
                normalized_parts.append(part)

        # Limit path length to prevent unbounded cardinality
        # Take first 6 segments max (typical API paths won't exceed this)
        # For extremely deep paths (>6 segments), this provides cardinality protection
        normalized_parts = normalized_parts[:6]

        return "/" + "/".join(normalized_parts)
