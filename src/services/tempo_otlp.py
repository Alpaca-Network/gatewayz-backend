"""
OpenTelemetry (OTLP) integration for Grafana Tempo.

This module configures OpenTelemetry to export distributed tracing data
to Grafana Tempo via the OTLP protocol.

The Railway Grafana stack template comes with Tempo pre-configured to receive:
- OTLP/gRPC on :4317
- OTLP/HTTP on :4318
"""

import logging
import socket
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fastapi import FastAPI

from src.config import Config
from src.config.opentelemetry_config import instrument_fastapi_application

logger = logging.getLogger(__name__)


def check_tempo_endpoint_reachable(endpoint: str, timeout: float = 5.0) -> bool:
    """
    Check if the Tempo OTLP endpoint is reachable.

    For HTTPS endpoints (Railway public URLs), we do an actual HTTP POST request
    since TCP socket checks may not work properly with Railway's proxy layer.

    Args:
        endpoint: The OTLP endpoint URL (including /v1/traces path for HTTPS)
        timeout: Connection timeout in seconds

    Returns:
        bool: True if endpoint is reachable, False otherwise
    """
    try:
        import requests as req_lib

        parsed = urlparse(endpoint)
        host = parsed.hostname

        if not host:
            logger.warning(f"Invalid Tempo endpoint URL: {endpoint}")
            return False

        # For HTTPS endpoints, do an actual HTTP request
        # This works better with Railway's proxy layer
        if parsed.scheme == "https":
            try:
                # Send empty protobuf to the traces endpoint
                # Tempo returns 200 for valid (even empty) requests
                response = req_lib.post(
                    endpoint,
                    data=b"",
                    headers={"Content-Type": "application/x-protobuf"},
                    timeout=timeout,
                )
                # 200 = success, 400 = bad request (but reachable), 415 = wrong content type (but reachable)
                if response.status_code in (200, 400, 415):
                    logger.debug(
                        f"Successfully reached Tempo at {endpoint} (HTTP {response.status_code})"
                    )
                    return True
                else:
                    logger.warning(
                        f"Tempo endpoint returned unexpected status {response.status_code}: {response.text[:100]}"
                    )
                    return False
            except req_lib.exceptions.RequestException as e:
                logger.warning(f"Cannot reach Tempo endpoint {endpoint}: {e}")
                return False

        # For HTTP endpoints (internal DNS), use TCP socket check
        port = parsed.port
        if not port:
            port = 4318 if parsed.scheme == "http" else 4317

        # Try to resolve the hostname (DNS check)
        try:
            socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror as e:
            logger.warning(
                f"Cannot resolve Tempo hostname '{host}': {e}. "
                f"Tracing will be disabled. Ensure Tempo service is deployed and reachable."
            )
            return False

        # Try to establish a TCP connection
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            logger.debug(f"Successfully connected to Tempo endpoint {host}:{port}")
            return True
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            logger.warning(
                f"Tempo endpoint {host}:{port} is not accepting connections: {e}. "
                f"Tracing will be disabled."
            )
            return False
        finally:
            if sock:
                sock.close()

    except Exception as e:
        logger.warning(f"Unexpected error checking Tempo endpoint: {e}")
        return False


def init_tempo_otlp():
    """
    Initialize OpenTelemetry integration with Tempo.

    This sets up trace collection and export to Tempo using OTLP.

    NOTE: This function is DEPRECATED in favor of OpenTelemetryConfig.initialize()
    which provides better configuration, resilient span processing, and circuit breaker.

    This function now delegates to OpenTelemetryConfig.initialize() for consistent
    initialization behavior. It is typically called from the background task in
    startup.py with retry logic.
    """
    if not Config.TEMPO_ENABLED:
        logger.info("Tempo/OTLP tracing is disabled")
        return

    # Delegate to OpenTelemetryConfig for consistent initialization
    # This ensures we use the same code path with resilient span processor
    from src.config.opentelemetry_config import OpenTelemetryConfig

    if OpenTelemetryConfig._initialized:
        logger.debug("OpenTelemetry already initialized via OpenTelemetryConfig")
        return OpenTelemetryConfig._tracer_provider

    success = OpenTelemetryConfig.initialize()
    if success:
        return OpenTelemetryConfig._tracer_provider
    return None


def _should_trace_httpx_request(request) -> bool:
    """
    Filter hook for HTTPX instrumentation to skip tracing on streaming endpoints.

    The httpx instrumentor can interfere with SSE streaming responses from AI providers
    because it wraps the transport layer. We skip instrumentation for requests to
    known streaming-heavy provider endpoints to prevent breaking SSE in chat/completions.
    """
    url = str(request.url)

    # Skip tracing for provider streaming endpoints (these return SSE streams)
    streaming_patterns = [
        "/chat/completions",
        "/v1/messages",
        "/v1/completions",
        "/v1/engines/",
    ]
    return not any(pattern in url for pattern in streaming_patterns)


def init_tempo_otlp_fastapi(app: Optional["FastAPI"] = None):
    """
    Initialize OpenTelemetry auto-instrumentation for FastAPI.

    This automatically instruments FastAPI to emit traces for:
    - HTTP requests (FastAPI server-side)
    - External HTTP calls (httpx client-side, excluding streaming endpoints)
    - Requests library calls
    """
    if not Config.TEMPO_ENABLED:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: F811, F401

        # FastAPI server-side instrumentation (traces all inbound requests)
        try:
            fastapi_instrumented = instrument_fastapi_application(app)
        except Exception as fastapi_error:
            logger.error(f"Failed to initialize FastAPI instrumentation: {fastapi_error}")
        else:
            if fastapi_instrumented:
                logger.info("FastAPI instrumentation enabled for app instance")
            else:
                logger.debug(
                    "FastAPI instrumentation skipped (app missing or already instrumented)"
                )

        # Instrument HTTPX client with streaming-safe filter.
        # The request_hook checks if the URL targets a streaming endpoint;
        # if so, we skip creating a trace span to avoid interfering with SSE.
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
            logger.info("HTTPX instrumentation enabled")
        except Exception as httpx_err:
            logger.warning(f"HTTPX instrumentation failed (non-fatal): {httpx_err}")

        # Instrument requests library
        try:
            from opentelemetry.instrumentation.requests import RequestsInstrumentor

            RequestsInstrumentor().instrument()
            logger.info("Requests library instrumentation enabled")
        except Exception as req_err:
            logger.warning(f"Requests instrumentation failed (non-fatal): {req_err}")

    except ImportError:
        logger.warning(
            "OpenTelemetry instrumentation packages not installed. "
            "Install with: pip install opentelemetry-instrumentation-fastapi "
            "opentelemetry-instrumentation-httpx opentelemetry-instrumentation-requests"
        )
    except Exception as e:
        logger.error(f"Failed to initialize OTLP instrumentation helpers: {e}")


def get_tracer(name: str = __name__):
    """
    Get a tracer instance for manual span creation.

    Usage:
        from src.services.tempo_otlp import get_tracer

        tracer = get_tracer(__name__)

        with tracer.start_as_current_span("my_operation") as span:
            span.set_attribute("user.id", user_id)
            # Do work here
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception as e:
        logger.error(f"Failed to get tracer: {e}")
        return None


# Context managers for manual tracing
class trace_span:
    """
    Context manager for creating spans manually.

    Usage:
        with trace_span("operation_name", {"user_id": "123"}) as span:
            # Do work here
            span.set_attribute("result", "success")
    """

    def __init__(self, name: str, attributes: dict | None = None):
        self.name = name
        self.attributes = attributes or {}
        self.span = None
        self.tracer = get_tracer()

    def __enter__(self):
        if self.tracer:
            self.span = self.tracer.start_span(self.name)
            for key, value in self.attributes.items():
                self.span.set_attribute(key, value)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.span:
            if exc_type:
                self.span.set_attribute("error", True)
                self.span.set_attribute("error.type", exc_type.__name__)
            self.span.end()
