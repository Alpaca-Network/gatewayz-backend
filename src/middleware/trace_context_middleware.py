"""
Middleware for adding trace context to request logs.

This middleware enriches all request logs with OpenTelemetry trace and span IDs,
enabling seamless navigation from logs to traces in Grafana.

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.

NOTE — potential duplication with OTel auto-instrumentation:
When FastAPIInstrumentor (opentelemetry-instrumentation-fastapi) is active it
already creates a server span per request that records the HTTP method, matched
route, and response status code.  The request/response logger.info() calls
below therefore duplicate that information in the log stream.

Set OTEL_AUTO_INSTRUMENTED=true to suppress those log lines while keeping the
x-trace-id / x-span-id response headers, which remain valuable regardless of
how spans are created (they let callers correlate their own logs with server
traces without querying the OTel backend directly).
"""

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config.config import Config
from src.config.opentelemetry_config import get_current_span_id, get_current_trace_id

logger = logging.getLogger(__name__)


class TraceContextMiddleware:
    """
    Middleware that adds trace context to request logs.

    For each incoming request:
    1. Extracts the current trace ID and span ID from OpenTelemetry
    2. Logs the request with trace context for correlation (skipped when
       OTEL_AUTO_INSTRUMENTED=true to avoid duplicating auto-instrumentation logs)
    3. Adds trace headers to the response (always performed)

    This enables:
    - Clicking from logs to traces in Grafana
    - Distributed tracing across services
    - Request correlation in observability tools

    This is a pure ASGI middleware to properly support streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Cache the flag once at construction time so we don't re-read it per request.
        self._skip_manual_logging: bool = Config.OTEL_AUTO_INSTRUMENTED

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # Skip tracing for high-frequency non-critical endpoints (performance optimization)
        # This saves ~3-5ms per request for these endpoints
        if path in ("/health", "/metrics", "/"):
            await self.app(scope, receive, send)
            return

        method = scope["method"]

        # Get trace context from the active OTel span (created either by
        # FastAPIInstrumentor or by an upstream propagation header).
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()

        # Create log context with trace IDs
        # Safely extract client host - client is a (host, port) tuple or None
        client = scope.get("client")
        try:
            client_host = client[0] if client and len(client) > 0 else None
        except (TypeError, IndexError):
            client_host = None

        log_extra = {
            "path": path,
            "method": method,
            "client_host": client_host,
        }

        if trace_id:
            log_extra["trace_id"] = trace_id
        if span_id:
            log_extra["span_id"] = span_id

        # DUPLICATION NOTE: when OTEL_AUTO_INSTRUMENTED=true, FastAPIInstrumentor
        # already records method + route + status in the server span, so these
        # logger.info() calls would produce redundant log lines.  Skip them to
        # avoid double-counting in log aggregators (Loki, etc.).
        if not self._skip_manual_logging:
            logger.info(f"{method} {path}", extra=log_extra)

        # Track status code from response
        status_code = None

        async def send_with_trace_headers(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)

                # Get existing headers and convert to mutable list
                headers = list(message.get("headers", []))

                # Inject trace headers into the response so callers can correlate
                # their own logs with server-side traces without querying the OTel
                # backend.  This is always done regardless of OTEL_AUTO_INSTRUMENTED.
                if trace_id:
                    headers.append((b"x-trace-id", trace_id.encode()))
                if span_id:
                    headers.append((b"x-span-id", span_id.encode()))

                # Create new message with updated headers
                message = {**message, "headers": headers}

            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_headers)

            # Log response with trace context (skipped under auto-instrumentation)
            if not self._skip_manual_logging and status_code is not None:
                logger.info(
                    f"{method} {path} - {status_code}",
                    extra={
                        **log_extra,
                        "status_code": status_code,
                    },
                )

        except Exception as e:
            # Log error with trace context
            logger.error(
                f"{method} {path} - Error: {str(e)}",
                extra=log_extra,
                exc_info=True,
            )
            raise
