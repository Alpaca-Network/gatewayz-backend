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
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fastapi import FastAPI

from src.config import Config
from src.config.opentelemetry_config import instrument_fastapi_application

logger = logging.getLogger(__name__)


def check_tempo_endpoint_reachable(endpoint: str, timeout: float = 1.0) -> bool:
    """
    Check if the Tempo OTLP endpoint is reachable.

    This performs a basic DNS resolution and TCP connection test to verify
    that the endpoint exists and is accepting connections before attempting
    to initialize OpenTelemetry exporters.

    Args:
        endpoint: The OTLP endpoint URL (e.g., "http://tempo.railway.internal:4318")
        timeout: Connection timeout in seconds (default: 2.0)

    Returns:
        bool: True if endpoint is reachable, False otherwise
    """
    try:
        # Parse the endpoint URL
        parsed = urlparse(endpoint)
        host = parsed.hostname
        port = parsed.port

        if not host:
            logger.warning(f"Invalid Tempo endpoint URL: {endpoint}")
            return False

        # Default port based on scheme if not specified
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
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
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
                endpoint=tempo_endpoint,
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
                logger.debug("FastAPI instrumentation skipped (app missing or already instrumented)")

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
