"""
OpenTelemetry configuration for distributed tracing and observability.

This module configures OpenTelemetry to send traces to Tempo and integrates
with the existing Prometheus metrics and logging infrastructure.

Features:
- Automatic FastAPI request tracing
- HTTPX and Requests library instrumentation
- Redis cache operation tracing (shows cache.get, cache.set operations)
- Context propagation for distributed tracing
- Integration with Railway/Grafana observability stack

Note: OpenTelemetry is optional. If not installed, tracing will be gracefully disabled.
"""

import logging
import socket
from typing import Optional
from urllib.parse import urlparse

# Try to import OpenTelemetry core - it's optional for deployments like Vercel
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import (
        DEPLOYMENT_ENVIRONMENT,
        SERVICE_NAME,
        SERVICE_VERSION,
        Resource,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    OPENTELEMETRY_AVAILABLE = True

except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    TracerProvider = None  # type: ignore

# Instrumentation packages are independent of core OTel availability.
# Each is optional — a missing package should NOT disable all tracing.
FASTAPI_INSTRUMENTATION_AVAILABLE = False
HTTPX_INSTRUMENTATION_AVAILABLE = False
REQUESTS_INSTRUMENTATION_AVAILABLE = False
REDIS_INSTRUMENTATION_AVAILABLE = False

FastAPIInstrumentor = None  # type: ignore
HTTPXClientInstrumentor = None  # type: ignore
RequestsInstrumentor = None  # type: ignore
RedisInstrumentor = None  # type: ignore

if OPENTELEMETRY_AVAILABLE:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[no-redef]

        FASTAPI_INSTRUMENTATION_AVAILABLE = True
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # type: ignore[no-redef]

        HTTPX_INSTRUMENTATION_AVAILABLE = True
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor  # type: ignore[no-redef]

        REQUESTS_INSTRUMENTATION_AVAILABLE = True
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor  # type: ignore[no-redef]

        REDIS_INSTRUMENTATION_AVAILABLE = True
    except ImportError:
        pass

from src.config.config import Config
from src.utils.resilient_span_processor import ResilientSpanProcessor

logger = logging.getLogger(__name__)


def _check_endpoint_reachable(endpoint: str, timeout: float = 2.0) -> bool:
    """
    Check if the OTLP endpoint is reachable.

    Args:
        endpoint: The OTLP endpoint URL
        timeout: Connection timeout in seconds

    Returns:
        bool: True if endpoint is reachable, False otherwise
    """
    try:
        # Parse the endpoint URL
        parsed = urlparse(endpoint)
        host = parsed.hostname
        port = parsed.port

        if not host:
            logger.warning(f"Invalid endpoint URL: {endpoint}")
            return False

        # Default port if not specified
        if not port:
            # For HTTPS URLs (Railway public endpoints), use 443 (standard HTTPS port)
            # Railway proxies HTTPS (443) to internal service ports
            if parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 4318
            else:
                port = 4317  # gRPC default

        # Try to resolve the hostname (DNS check)
        try:
            socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror as e:
            logger.warning(f"Cannot resolve hostname '{host}': {e}. Tracing will be disabled.")
            return False

        # Try to establish a TCP connection
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            logger.debug(f"Successfully connected to {host}:{port}")
            return True
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            logger.warning(
                f"Endpoint {host}:{port} is not accepting connections: {e}. "
                f"Tracing will be disabled."
            )
            return False
        finally:
            if sock:
                sock.close()

    except Exception as e:
        logger.warning(f"Unexpected error checking endpoint: {e}")
        return False


class OpenTelemetryConfig:
    """
    OpenTelemetry configuration and setup for the Gatewayz API.

    This class handles initialization of:
    - Trace provider with OTLP export to Tempo
    - FastAPI automatic instrumentation
    - HTTP client instrumentation (httpx, requests)
    - Redis cache instrumentation (cache.get, cache.set operations)
    - Resource attributes (service name, version, environment)
    """

    _initialized = False
    _tracer_provider: Optional[TracerProvider] = None

    @classmethod
    def initialize(cls) -> bool:
        """
        Initialize OpenTelemetry tracing if enabled.

        Returns:
            bool: True if initialization succeeded, False if disabled or failed
        """
        if cls._initialized:
            logger.debug("OpenTelemetry already initialized")
            return True

        if not OPENTELEMETRY_AVAILABLE:
            logger.info("⏭️  OpenTelemetry not available (package not installed)")
            return False

        if not Config.TEMPO_ENABLED:
            logger.info("⏭️  OpenTelemetry tracing disabled (TEMPO_ENABLED=false)")
            return False

        try:
            logger.info("🔭 Initializing OpenTelemetry tracing...")

            # Configure OTLP exporter to Tempo
            tempo_endpoint = Config.TEMPO_OTLP_HTTP_ENDPOINT

            # DEBUG: Log the original value
            logger.info(f"   [DEBUG] Raw TEMPO_OTLP_HTTP_ENDPOINT: {tempo_endpoint}")

            # Railway internal DNS detection (same project) - check FIRST
            if ".railway.internal" in tempo_endpoint:
                # Using Railway internal DNS - keep as-is with port
                logger.info(f"   Railway internal DNS detected - using private network")
                # Ensure http:// for internal (no SSL)
                if not tempo_endpoint.startswith("http://") and not tempo_endpoint.startswith(
                    "https://"
                ):
                    tempo_endpoint = f"http://{tempo_endpoint}"

                # CRITICAL FIX: Ensure port 4318 is explicitly set for Railway internal DNS
                # Without this, OTLPSpanExporter defaults to port 80, causing connection refused errors
                parsed = urlparse(tempo_endpoint)
                if not parsed.port:
                    # Port missing - add :4318 for OTLP HTTP
                    if parsed.hostname:
                        tempo_endpoint = f"{parsed.scheme}://{parsed.hostname}:4318{parsed.path}"
                        logger.warning(
                            f"   ⚠️  Port missing in Railway internal endpoint - "
                            f"auto-corrected to: {tempo_endpoint}"
                        )
                        logger.warning(
                            f"   💡 TIP: Set TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.railway.internal:4318 "
                            f"in Railway environment variables to avoid this warning"
                        )

                logger.info(f"   [DEBUG] After internal DNS processing: {tempo_endpoint}")
            # Railway public URL detection (cross-project)
            elif ".railway.app" in tempo_endpoint or ".up.railway.app" in tempo_endpoint:
                # Remove :4318 or :4317 port suffixes for Railway public deployments
                tempo_endpoint = tempo_endpoint.replace(":4318", "").replace(":4317", "")
                # Ensure it uses https:// for Railway public
                if tempo_endpoint.startswith("http://"):
                    tempo_endpoint = tempo_endpoint.replace("http://", "https://")
                elif not tempo_endpoint.startswith("https://"):
                    tempo_endpoint = f"https://{tempo_endpoint}"
                logger.info(f"   Railway public deployment detected - using HTTPS proxy")
                logger.info(f"   [DEBUG] After public URL processing: {tempo_endpoint}")

            logger.info(f"   Tempo endpoint (base URL): {tempo_endpoint}")
            logger.info(f"   [DEBUG] Full OTLP path will be: {tempo_endpoint}/v1/traces")

            # Check if Tempo endpoint is reachable before attempting to create exporter
            # This check can be skipped with TEMPO_SKIP_REACHABILITY_CHECK=true for async/lazy connections
            if Config.TEMPO_SKIP_REACHABILITY_CHECK:
                logger.info(
                    "   Skipping reachability check (TEMPO_SKIP_REACHABILITY_CHECK=true) - "
                    "traces will be buffered and sent asynchronously"
                )
            elif not _check_endpoint_reachable(tempo_endpoint):
                logger.warning(
                    f"⏭️  Skipping OpenTelemetry initialization - Tempo endpoint {tempo_endpoint} is not reachable. "
                    f"Ensure the Tempo service is deployed and accessible. "
                    f"The application will continue without distributed tracing."
                )
                return False

            # Create resource with service metadata
            resource = Resource.create(
                {
                    SERVICE_NAME: Config.OTEL_SERVICE_NAME,
                    SERVICE_VERSION: "2.0.3",
                    DEPLOYMENT_ENVIRONMENT: Config.APP_ENV,
                    "service.namespace": "gatewayz",
                    "telemetry.sdk.language": "python",
                }
            )

            # Create tracer provider
            cls._tracer_provider = TracerProvider(resource=resource)

            # Create OTLP exporter with error handling for connection issues
            try:
                # Increased timeout for Railway cross-project connections
                # Railway internal DNS is fast, but cross-project public URLs need more time
                timeout_seconds = 30 if ".railway.app" in tempo_endpoint else 10

                # IMPORTANT: The OTLPSpanExporter does NOT auto-append /v1/traces when
                # you pass the `endpoint` parameter directly. We must append it manually.
                # Verified via testing: exporter._endpoint shows exactly what you pass in.
                # Without /v1/traces, Tempo returns 404 (POST / instead of POST /v1/traces).

                # Ensure endpoint has /v1/traces path
                traces_endpoint = tempo_endpoint.rstrip("/")
                if not traces_endpoint.endswith("/v1/traces"):
                    traces_endpoint = f"{traces_endpoint}/v1/traces"

                logger.info(
                    f"   [DEBUG] Creating OTLP HTTP exporter with endpoint: {traces_endpoint}"
                )
                logger.info(f"   [DEBUG] Timeout: {timeout_seconds}s")

                otlp_exporter = OTLPSpanExporter(
                    endpoint=traces_endpoint,  # Full path including /v1/traces
                    headers={},  # Add authentication headers if needed
                    timeout=timeout_seconds,
                )

                # Log the actual endpoint the exporter is using
                logger.info(f"   [DEBUG] OTLP exporter created successfully")
                logger.info(f"   [DEBUG] Traces will be sent to: {traces_endpoint}")
                logger.info(f"   OTLP exporter configured with {timeout_seconds}s timeout")
            except Exception as e:
                logger.error(
                    f"❌ Failed to create OTLP exporter for {tempo_endpoint}: {e}. "
                    f"Tracing will be disabled.",
                    exc_info=True,
                )
                return False

            # Add span processor for exporting traces with error handling
            try:
                # Use BatchSpanProcessor to batch spans before sending
                # This reduces network overhead and improves performance
                batch_processor = BatchSpanProcessor(
                    otlp_exporter,
                    max_queue_size=2048,  # Increase queue size for high-traffic
                    schedule_delay_millis=5000,  # Send every 5 seconds
                    max_export_batch_size=512,  # Send up to 512 spans per batch
                )

                # Wrap with resilient processor to handle connection errors gracefully
                resilient_processor = ResilientSpanProcessor(batch_processor)

                # ProviderSpanEnricher: runs BEFORE the batch exporter so that
                # peer.service is present on spans when Tempo indexes them.
                # This is what makes the Service Graph & Topology section draw
                # edges between gatewayz-backend and each AI provider.
                try:
                    from src.services.provider_span_enricher import ProviderSpanEnricher

                    cls._tracer_provider.add_span_processor(ProviderSpanEnricher())
                    logger.info(
                        "   ProviderSpanEnricher registered "
                        "(peer.service will be added to AI provider HTTP spans)"
                    )
                except Exception as enricher_e:
                    logger.warning(
                        f"   ProviderSpanEnricher unavailable (non-fatal): {enricher_e}"
                    )

                cls._tracer_provider.add_span_processor(resilient_processor)
                logger.info(
                    "   Resilient batch span processor configured (queue: 2048, batch: 512)"
                )
                logger.info("   Circuit breaker enabled to handle connection failures")
            except Exception as e:
                logger.error(
                    f"❌ Failed to add span processor: {e}. Tracing will be disabled.",
                    exc_info=True,
                )
                return False

            # In development, also log traces to console
            if Config.IS_DEVELOPMENT:
                cls._tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
                logger.info("   Console trace export enabled (development mode)")

            # Set global tracer provider
            trace.set_tracer_provider(cls._tracer_provider)

            # Instrument HTTP clients for automatic tracing (each is optional)
            if HTTPX_INSTRUMENTATION_AVAILABLE:
                HTTPXClientInstrumentor().instrument()
                logger.info("   HTTPX client instrumentation enabled")
            if REQUESTS_INSTRUMENTATION_AVAILABLE:
                RequestsInstrumentor().instrument()
                logger.info("   Requests library instrumentation enabled")

            # Instrument Redis for cache operation tracing
            if REDIS_INSTRUMENTATION_AVAILABLE and RedisInstrumentor is not None:
                try:
                    RedisInstrumentor().instrument()
                    logger.info(
                        "   Redis instrumentation enabled (cache operations will appear in traces)"
                    )
                except Exception as redis_e:
                    logger.warning(f"   Redis instrumentation failed (non-fatal): {redis_e}")
            else:
                logger.debug("   Redis instrumentation not available (package not installed)")

            cls._initialized = True
            logger.info("✅ OpenTelemetry tracing initialized successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize OpenTelemetry: {e}", exc_info=True)
            return False

    @classmethod
    def instrument_fastapi(cls, app) -> None:
        """
        Instrument a FastAPI application with OpenTelemetry.

        This adds automatic tracing for all FastAPI routes and includes:
        - Request/response tracing
        - Route matching information
        - HTTP method and status code
        - Exception tracking

        Args:
            app: FastAPI application instance to instrument
        """
        if not OPENTELEMETRY_AVAILABLE:
            logger.debug("Skipping FastAPI instrumentation (OpenTelemetry not available)")
            return

        if not cls._initialized or not Config.TEMPO_ENABLED:
            logger.debug("Skipping FastAPI instrumentation (tracing not enabled)")
            return

        try:
            instrumented = instrument_fastapi_application(app)
            if instrumented:
                logger.info("✅ FastAPI application instrumented with OpenTelemetry")
            else:
                logger.debug(
                    "FastAPI instrumentation skipped (already instrumented or unavailable)"
                )
        except Exception as e:
            logger.error(f"❌ Failed to instrument FastAPI: {e}", exc_info=True)

    @classmethod
    def shutdown(cls) -> None:
        """
        Gracefully shutdown OpenTelemetry and flush any pending spans.

        Should be called during application shutdown to ensure all traces
        are exported before the application exits.
        """
        if not cls._initialized:
            return

        try:
            logger.info("🛑 Shutting down OpenTelemetry...")
            if cls._tracer_provider:
                cls._tracer_provider.shutdown()
            logger.info("✅ OpenTelemetry shutdown complete")
        except Exception as e:
            logger.error(f"❌ Error during OpenTelemetry shutdown: {e}", exc_info=True)
        finally:
            cls._initialized = False
            cls._tracer_provider = None

    @classmethod
    def get_tracer(cls, name: str):
        """
        Get a tracer for creating custom spans.

        Args:
            name: Name of the tracer (typically __name__ of the calling module)

        Returns:
            OpenTelemetry Tracer instance, or None if OpenTelemetry is not available
        """
        if not OPENTELEMETRY_AVAILABLE:
            return None
        return trace.get_tracer(name)


def instrument_fastapi_application(app) -> bool:
    """
    Instrument a FastAPI application while remaining compatible with different
    opentelemetry-instrumentation-fastapi versions.

    Returns:
        bool: True if instrumentation was applied, False if it was skipped.
    """
    if not OPENTELEMETRY_AVAILABLE or not FASTAPI_INSTRUMENTATION_AVAILABLE:
        logger.debug("OpenTelemetry or FastAPI instrumentation not available; skipping")
        return False

    if app is None:
        logger.debug("FastAPI app instance not provided; skipping instrumentation")
        return False

    instrumentor = FastAPIInstrumentor()

    try:
        instrumentor.instrument_app(app=app)
        return True
    except TypeError as exc:
        message = str(exc)
        if "instrument()" in message and "app" in message:
            logger.debug(
                "FastAPIInstrumentor.instrument_app requires explicit app argument; retrying with instrument(app=app)"
            )
            instrumentor.instrument(app=app)
            return True
        raise
    except RuntimeError as exc:
        if "already instrumented" in str(exc).lower():
            logger.debug("FastAPI already instrumented; skipping re-instrumentation")
            return False
        raise


# Helper function to get current trace context
def get_current_trace_id() -> str | None:
    """
    Get the current trace ID as a hex string.

    Returns:
        str: Trace ID in hex format (32 characters), or None if no active span
    """
    if not OPENTELEMETRY_AVAILABLE:
        return None

    try:
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context.is_valid:
            return format(span_context.trace_id, "032x")
    except Exception:
        pass
    return None


def get_current_span_id() -> str | None:
    """
    Get the current span ID as a hex string.

    Returns:
        str: Span ID in hex format (16 characters), or None if no active span
    """
    if not OPENTELEMETRY_AVAILABLE:
        return None

    try:
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context.is_valid:
            return format(span_context.span_id, "016x")
    except Exception:
        pass
    return None
