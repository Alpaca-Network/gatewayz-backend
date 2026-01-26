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
    Includes health check to prevent initialization if Tempo is unreachable.
    """
    if not Config.TEMPO_ENABLED:
        logger.info("Tempo/OTLP tracing is disabled")
        return

    # Check if Tempo endpoint is reachable before initializing
    tempo_endpoint = Config.TEMPO_OTLP_HTTP_ENDPOINT

    # Railway fix: Remove port numbers from Railway URLs
    if ".railway.app" in tempo_endpoint or ".up.railway.app" in tempo_endpoint:
        tempo_endpoint = tempo_endpoint.replace(":4318", "").replace(":4317", "")
        if tempo_endpoint.startswith("http://"):
            tempo_endpoint = tempo_endpoint.replace("http://", "https://")
        elif not tempo_endpoint.startswith("https://"):
            tempo_endpoint = f"https://{tempo_endpoint}"

    # CRITICAL: OTLPSpanExporter does NOT auto-append /v1/traces when using
    # the endpoint parameter. We must append it manually.
    if not tempo_endpoint.rstrip("/").endswith("/v1/traces"):
        tempo_endpoint = f"{tempo_endpoint.rstrip('/')}/v1/traces"

    logger.info(f"Checking Tempo endpoint availability: {tempo_endpoint}")

    if not check_tempo_endpoint_reachable(tempo_endpoint):
        logger.warning(
            f"⏭️  Skipping OpenTelemetry initialization - Tempo endpoint {tempo_endpoint} is not reachable. "
            f"Ensure the Tempo service is deployed and accessible from this service. "
            f"The application will continue without distributed tracing."
        )
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create OTLP exporter pointing to Tempo
        # Wrap in try-except to catch any connection errors during initialization
        try:
            otlp_exporter = OTLPSpanExporter(
                endpoint=tempo_endpoint,  # Full path including /v1/traces
            )
        except Exception as e:
            logger.error(
                f"Failed to create OTLP exporter for {tempo_endpoint}: {e}. "
                f"Tracing will be disabled."
            )
            return None

        # Create resource with service name for Tempo filtering
        resource = Resource.create(
            {
                "service.name": Config.OTEL_SERVICE_NAME,
            }
        )

        # Create tracer provider with resource
        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set as global tracer provider
        trace.set_tracer_provider(trace_provider)

        logger.info("✅ OpenTelemetry/Tempo initialization completed")
        logger.info(f"   Service name: {Config.OTEL_SERVICE_NAME}")
        logger.info(f"   Tempo endpoint: {tempo_endpoint}")
        logger.info("   Traces will be exported to Tempo")

        return trace_provider

    except ImportError:
        logger.warning(
            "⏭️  OpenTelemetry packages not installed. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp"
        )
        return None
    except Exception as e:
        logger.error(f"❌ Failed to initialize Tempo/OTLP: {e}", exc_info=True)
        return None


def init_tempo_otlp_fastapi(app: Optional["FastAPI"] = None):
    """
    Initialize OpenTelemetry auto-instrumentation for FastAPI.

    This automatically instruments FastAPI to emit traces for:
    - HTTP requests
    - Database operations (via instrumentation)
    - External HTTP calls
    """
    if not Config.TEMPO_ENABLED:
        return

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

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

        # Instrument HTTP clients
        HTTPXClientInstrumentor().instrument()
        logger.info("HTTPX instrumentation enabled")

        RequestsInstrumentor().instrument()
        logger.info("Requests library instrumentation enabled")

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
