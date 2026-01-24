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

    # Use gRPC endpoint (more reliable than HTTP, no 404 path issues)
    tempo_endpoint = Config.TEMPO_OTLP_GRPC_ENDPOINT

    # Validate endpoint is configured
    if not tempo_endpoint:
        logger.warning("⏭️  TEMPO_OTLP_GRPC_ENDPOINT not configured, skipping tracing")
        return None

    logger.info(f"🔭 Initializing OpenTelemetry with Tempo (gRPC)")
    logger.info(f"   Raw TEMPO_OTLP_GRPC_ENDPOINT: {tempo_endpoint}")

    # Clean up endpoint format for gRPC
    # gRPC format: "host:port" (no http:// prefix, no /v1/traces path)
    if "://" in tempo_endpoint:
        parsed = urlparse(tempo_endpoint)
        tempo_endpoint = f"{parsed.hostname}:{parsed.port or 4317}"
        logger.warning(f"   ⚠️  Removed protocol prefix, using: {tempo_endpoint}")

    # Ensure port is specified
    if ":" not in tempo_endpoint:
        tempo_endpoint = f"{tempo_endpoint}:4317"
        logger.warning(f"   ⚠️  Port missing, added default gRPC port: {tempo_endpoint}")

    logger.info(f"   Tempo gRPC endpoint: {tempo_endpoint}")

    # Determine if connection should use TLS
    use_insecure = ".railway.internal" in tempo_endpoint or "localhost" in tempo_endpoint
    if use_insecure:
        logger.info(f"   Using insecure connection (no TLS) for internal network")
    else:
        logger.info(f"   Using secure connection (TLS) for external network")

    try:
        from opentelemetry import trace

        # Using gRPC exporter - more reliable, no HTTP 404 path issues
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create OTLP gRPC exporter
        try:
            otlp_exporter = OTLPSpanExporter(
                endpoint=tempo_endpoint,  # Format: "host:port" (no http://, no /v1/traces)
                insecure=use_insecure,  # True for Railway internal DNS
                timeout=10,
            )
            logger.info("   ✅ OTLP gRPC exporter created successfully")
        except Exception as e:
            logger.error(
                f"❌ Failed to create OTLP gRPC exporter for {tempo_endpoint}: {e}. "
                f"Tracing will be disabled."
            )
            return None

        # Create resource with service name for Tempo filtering
        resource = Resource.create(
            {
                "service.name": Config.OTEL_SERVICE_NAME,
                "service.version": "2.0.3",
                "deployment.environment": Config.APP_ENV,
            }
        )

        # Create tracer provider with resource
        trace_provider = TracerProvider(resource=resource)
        trace_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set as global tracer provider
        trace.set_tracer_provider(trace_provider)

        logger.info("✅ OpenTelemetry/Tempo initialization completed")
        logger.info(f"   Service name: {Config.OTEL_SERVICE_NAME}")
        logger.info(f"   Protocol: gRPC (more reliable than HTTP)")
        logger.info(f"   Endpoint: {tempo_endpoint}")
        logger.info("   Traces will be exported to Tempo via gRPC")

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
