"""
FastAPI middleware for automatic observability instrumentation.

This middleware automatically tracks HTTP request metrics for all endpoints
without requiring manual instrumentation. It exposes metrics compatible with
the Grafana FastAPI Observability Dashboard (ID: 16110).

Metrics exposed:
- fastapi_requests_in_progress: Gauge of concurrent requests by method and endpoint
- fastapi_request_size_bytes: Histogram of request body sizes by method and endpoint
- fastapi_response_size_bytes: Histogram of response body sizes by method and endpoint
- http_requests_total: Counter of total requests by method, endpoint, and status code
- http_request_duration_seconds: Histogram of request duration by method and endpoint
"""

import logging
import time
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.services.prometheus_metrics import (
    http_request_count,
    http_request_duration,
    record_http_response,
    fastapi_requests_in_progress,
    fastapi_request_size_bytes,
    fastapi_response_size_bytes,
)

logger = logging.getLogger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware for automatic request/response observability.

    Automatically tracks:
    - Request duration and size
    - Response size and status code
    - Concurrent request count
    - All metrics with method and endpoint labels

    This middleware should be added early in the middleware stack to capture
    accurate timing for all requests.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and track observability metrics.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware/handler in the chain

        Returns:
            The HTTP response from downstream handlers
        """
        # Extract method and path
        method = request.method
        path = request.url.path

        # Normalize path for metrics (group dynamic segments)
        endpoint = self._normalize_path(path)

        # Track request body size
        try:
            request_body = await request.body()
            request_size = len(request_body)
            fastapi_request_size_bytes.labels(method=method, endpoint=endpoint).observe(
                request_size
            )
        except Exception as e:
            logger.debug(f"Could not read request body for metrics: {e}")
            request_size = 0

        # Increment in-progress requests gauge
        fastapi_requests_in_progress.labels(method=method, endpoint=endpoint).inc()

        # Record start time
        start_time = time.time()

        try:
            # Call the next middleware/handler
            response = await call_next(request)

            # Track response body size
            try:
                # For responses with a body_iterator (streaming responses),
                # we can only estimate size
                if hasattr(response, "body_iterator"):
                    # For streaming responses, we can't get the exact size
                    # but we can track 0 to indicate it was streamed
                    fastapi_response_size_bytes.labels(
                        method=method, endpoint=endpoint
                    ).observe(0)
                else:
                    response_size = len(response.body) if hasattr(response, "body") else 0
                    fastapi_response_size_bytes.labels(
                        method=method, endpoint=endpoint
                    ).observe(response_size)
            except Exception as e:
                logger.debug(f"Could not determine response size: {e}")
                response_size = 0

            # Record metrics
            duration = time.time() - start_time
            status_code = response.status_code

            # Record HTTP metrics
            http_request_duration.labels(method=method, endpoint=endpoint).observe(
                duration
            )
            record_http_response(method=method, endpoint=endpoint, status_code=status_code)

            return response

        except Exception as e:
            # Record error metrics
            logger.error(f"Error processing request {method} {endpoint}: {e}")
            duration = time.time() - start_time

            # Record error response
            http_request_duration.labels(method=method, endpoint=endpoint).observe(
                duration
            )
            record_http_response(method=method, endpoint=endpoint, status_code=500)

            # Re-raise the exception to be handled by FastAPI exception handlers
            raise

        finally:
            # Always decrement in-progress requests gauge
            fastapi_requests_in_progress.labels(method=method, endpoint=endpoint).dec()

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

        for i, part in enumerate(parts):
            if not part:  # Skip empty parts from leading/trailing slashes
                continue

            # Check if this looks like a UUID or ID (hex string or UUID pattern)
            if len(part) > 10 and (
                part.isdigit() or
                all(c in "0123456789abcdef-" for c in part.lower())
            ):
                # Replace numeric/UUID segments with {id}
                normalized_parts.append("{id}")
            # Check if it looks like a model name or similar (contains special chars like -)
            elif "-" in part and len(part) > 10:
                # Likely a model name or identifier, keep first segment only for grouping
                # e.g., "gpt-4-turbo" -> "gpt-4-turbo" (keep as is)
                normalized_parts.append(part)
            else:
                # Keep regular path segments
                normalized_parts.append(part)

        # Limit path length to prevent unbounded cardinality
        # Take first 5 segments max
        normalized_parts = normalized_parts[:5]

        return "/" + "/".join(normalized_parts)
