import asyncio
import logging
import os
import secrets

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import REGISTRY

from src.config import Config

# Initialize logging with Loki integration
from src.config.logging_config import configure_logging
from src.constants import (
    FRONTEND_BETA_URL,
    FRONTEND_STAGING_URL,
    TAURI_DESKTOP_URL,
    TAURI_DESKTOP_PROTOCOL_URL,
)
from src.middleware.selective_gzip_middleware import SelectiveGZipMiddleware
from src.middleware.security_middleware import SecurityMiddleware
from src.services.startup import lifespan
from src.utils.validators import ensure_api_key_like, ensure_non_empty_string

configure_logging()
logger = logging.getLogger(__name__)

# Initialize Sentry for error monitoring with adaptive sampling
if Config.SENTRY_ENABLED and Config.SENTRY_DSN:
    import sentry_sdk

    def sentry_traces_sampler(sampling_context):
        """
        Adaptive sampling to control Sentry costs while maintaining visibility.

        Sampling strategy:
        - Development: 100% (all requests)
        - Health/metrics endpoints: 0% (skip monitoring endpoints)
        - Critical endpoints: 20% (chat, messages)
        - Other endpoints: 10%
        - Errors: Always sampled (parent_sampled)
        """
        # Always sample errors
        if sampling_context.get("parent_sampled") is not None:
            return 1.0

        # 100% sampling in development
        if Config.SENTRY_ENVIRONMENT == "development":
            return 1.0

        # Get endpoint path
        endpoint = ""
        if "wsgi_environ" in sampling_context:
            endpoint = sampling_context["wsgi_environ"].get("PATH_INFO", "")
        elif "asgi_scope" in sampling_context:
            endpoint = sampling_context["asgi_scope"].get("path", "")

        # Skip health check and monitoring endpoints (0%)
        if endpoint in ["/health", "/metrics", "/api/health", "/api/monitoring/health"]:
            return 0.0

        # Critical inference endpoints: 20% sampling
        if endpoint in ["/v1/chat/completions", "/v1/messages", "/v1/images/generations"]:
            return 0.2

        # Admin endpoints: 50% sampling (important but lower volume)
        if endpoint.startswith("/api/admin"):
            return 0.5

        # All other endpoints: 10% sampling
        return 0.1

    sentry_sdk.init(
        dsn=Config.SENTRY_DSN,
        # Add data like request headers and IP for users
        send_default_pii=True,
        # Set environment (development, staging, production)
        environment=Config.SENTRY_ENVIRONMENT,
        # Release tracking for Sentry release management
        release=Config.SENTRY_RELEASE,
        # Adaptive sampling function (replaces static traces_sample_rate)
        traces_sampler=sentry_traces_sampler,
        # Reduced profiling: 5% (down from default)
        profiles_sample_rate=0.05,
        # Set profile_lifecycle to "trace" to run profiler during transactions
        profile_lifecycle="trace",
    )
    logger.info(
        f"✅ Sentry initialized with adaptive sampling "
        f"(environment: {Config.SENTRY_ENVIRONMENT}, release: {Config.SENTRY_RELEASE})"
    )
else:
    logger.info("⏭️  Sentry disabled (SENTRY_ENABLED=false or SENTRY_DSN not set)")

# Constants
ERROR_INVALID_ADMIN_API_KEY = "Invalid admin API key"

# Cache dictionaries for models and providers
_models_cache = {"data": None, "timestamp": None, "ttl": 3600}  # 1 hour TTL

_huggingface_cache = {"data": {}, "timestamp": None, "ttl": 3600}  # 1 hour TTL

_provider_cache = {"data": None, "timestamp": None, "ttl": 3600}  # 1 hour TTL


# Admin key validation
def get_admin_key(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    """Validate admin API key with security improvements"""
    admin_key = credentials.credentials

    # Input validation
    try:
        ensure_non_empty_string(admin_key, "admin API key")
        ensure_api_key_like(admin_key, field_name="admin API key", min_length=10)
    except ValueError:
        # Do not leak details; preserve current response contract
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY) from None

    # Get expected key from environment
    expected_key = os.environ.get("ADMIN_API_KEY")

    # Ensure admin key is configured
    if not expected_key:
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY)

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(admin_key, expected_key):
        raise HTTPException(status_code=401, detail=ERROR_INVALID_ADMIN_API_KEY)

    return admin_key


def create_app() -> FastAPI:
    app = FastAPI(
        title="Gatewayz Universal Inference API",
        description="Gateway for AI model access powered by Gatewayz",
        version="2.0.3",  # Multi-sort strategy for 1204 HuggingFace models + auto :hf-inference suffix
        lifespan=lifespan,
    )

    # Create v1 router for OpenAI-compatible endpoints
    # This allows users to use base_url="https://api.gatewayz.ai" (SDK appends /v1)
    # or base_url="https://api.gatewayz.ai/v1" directly
    v1_router = APIRouter(prefix="/v1")

    # Add CORS middleware
    # Note: When allow_credentials=True, allow_origins cannot be ["*"]
    # Must specify exact origins for security

    # Environment-aware CORS origins
    # Always include beta.gatewayz.ai for frontend access
    base_origins = [
        FRONTEND_BETA_URL,
        FRONTEND_STAGING_URL,
        TAURI_DESKTOP_URL,  # Tauri desktop app origin (http://tauri.localhost)
        TAURI_DESKTOP_PROTOCOL_URL,  # Tauri desktop app origin (tauri://localhost)
        "https://api.gatewayz.ai",  # Added for chat API access from frontend
        "https://docs.gatewayz.ai",  # Added for documentation site access
    ]

    if Config.IS_PRODUCTION:
        allowed_origins = [
            "https://gatewayz.ai",
            "https://www.gatewayz.ai",
        ] + base_origins
    elif Config.IS_STAGING:
        allowed_origins = [
            "http://localhost:3000",  # For testing against staging
            "http://localhost:3001",
        ] + base_origins
    else:  # development
        allowed_origins = [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ] + base_origins

    # Explicitly allow modern tracing/debug headers to prevent CORS failures on mobile
    allowed_headers = [
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Client-Version",
        "X-CSRF-Token",
        "X-Supabase-Key",
        "X-Supabase-Project",
        "sentry-trace",
        "baggage",
    ]

    logger.info(f"   Allowed Headers: {allowed_headers}")

    # OPTIMIZED: Add security middleware first to block bots before heavy logic
    from src.config.redis_config import get_redis_client

    try:
        redis_client = get_redis_client()
        if redis_client:
            app.add_middleware(SecurityMiddleware, redis_client=redis_client)
            logger.info("  🛡️  Security middleware enabled (IP tiering & fingerprinting)")
        else:
            app.add_middleware(SecurityMiddleware)
            logger.warning(
                "  🛡️  Security middleware enabled with LOCAL fallback (Redis not available)"
            )
    except Exception as e:
        # Fallback to in-memory limiting if redis is unavailable
        app.add_middleware(SecurityMiddleware)
        logger.warning(f"  🛡️  Security middleware enabled with LOCAL fallback (Redis error: {e})")

    # OPTIMIZED: Add request timeout middleware first to prevent 504 Gateway Timeouts
    # Middleware order matters! Last added = first executed
    from src.middleware.request_timeout_middleware import RequestTimeoutMiddleware

    app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=55.0)
    logger.info("  ⏱️  Request timeout middleware enabled (55s timeout to prevent 504 errors)")

    # Add concurrency control middleware (global admission gate)
    from src.middleware.concurrency_middleware import ConcurrencyMiddleware

    app.add_middleware(
        ConcurrencyMiddleware,
        limit=Config.CONCURRENCY_LIMIT,
        queue_size=Config.CONCURRENCY_QUEUE_SIZE,
        queue_timeout=Config.CONCURRENCY_QUEUE_TIMEOUT,
    )
    logger.info(
        f"  🚦 Concurrency middleware enabled "
        f"(limit={Config.CONCURRENCY_LIMIT}, queue={Config.CONCURRENCY_QUEUE_SIZE})"
    )

    # Add request ID middleware for error tracking and correlation
    from src.middleware.request_id_middleware import RequestIDMiddleware

    app.add_middleware(RequestIDMiddleware)
    logger.info("  🆔 Request ID middleware enabled (unique ID for all requests)")

    # Add trace context middleware for distributed tracing
    from src.middleware.trace_context_middleware import TraceContextMiddleware

    app.add_middleware(TraceContextMiddleware)
    logger.info("  🔗 Trace context middleware enabled (log-to-trace correlation)")

    # Add automatic Sentry error capture middleware (captures ALL route errors)
    if Config.SENTRY_ENABLED and Config.SENTRY_DSN:
        from src.middleware.auto_sentry_middleware import AutoSentryMiddleware

        app.add_middleware(AutoSentryMiddleware)
        logger.info("  🎯 Auto-Sentry middleware enabled (automatic error capture for all routes)")

    # Add CORS middleware second (must be early for OPTIONS requests)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=allowed_headers,
    )

    # Add observability middleware for automatic metrics collection
    from src.middleware.observability_middleware import ObservabilityMiddleware

    app.add_middleware(ObservabilityMiddleware)
    logger.info("  📊 Observability middleware enabled (automatic metrics tracking)")

    # OPTIMIZED: Add GZip compression last (larger threshold = 10KB for better CPU efficiency)
    # Only compress large responses (model catalogs, large JSON payloads)
    # Uses SelectiveGZipMiddleware to skip compression for SSE streaming responses
    # This prevents buffering issues where SSE chunks get bundled together
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=10000)
    logger.info("  🗜  Selective GZip compression middleware enabled (threshold: 10KB, skips SSE)")

    # Add staging security middleware (protects staging environment from unauthorized access)
    from src.middleware.staging_security import StagingSecurityMiddleware

    app.add_middleware(StagingSecurityMiddleware)
    logger.info("  🔒 Staging security middleware enabled")

    # Add deprecation middleware to warn users about legacy endpoints
    from src.middleware.deprecation import DeprecationMiddleware

    app.add_middleware(DeprecationMiddleware)
    logger.info("  ⚠️  Deprecation middleware enabled (legacy endpoint warnings)")

    # Security
    HTTPBearer()

    # ==================== Prometheus Metrics ====================
    logger.info("Setting up Prometheus metrics...")

    # Import metrics module to initialize all metrics
    from fastapi.responses import Response

    # Add Prometheus metrics endpoint
    from prometheus_client import generate_latest

    from src.services import prometheus_metrics  # noqa: F401

    @app.get("/metrics", tags=["monitoring"], include_in_schema=False)
    async def metrics():
        """
        Prometheus metrics endpoint for monitoring.

        Exposes metrics in Prometheus text format including:
        - HTTP request counts and durations
        - Model inference metrics (requests, latency, tokens)
        - Database query metrics
        - Cache hit/miss rates
        - Rate limiting metrics
        - Provider health metrics
        - Business metrics (credits, tokens, subscriptions)
        - Redis INFO metrics (memory, keyspace, clients, commands)
        """
        # Refresh Redis INFO gauges on each scrape (run in threadpool to avoid blocking)
        import asyncio

        await asyncio.to_thread(prometheus_metrics.collect_redis_info)
        return Response(generate_latest(REGISTRY), media_type="text/plain; charset=utf-8")

    logger.info("  [OK] Prometheus metrics endpoint at /metrics")

    # Add structured metrics endpoint (parses Prometheus metrics)
    @app.get("/api/metrics/parsed", tags=["monitoring"], include_in_schema=False)
    async def get_parsed_metrics():
        """
        Get parsed Prometheus metrics in structured JSON format.

        Returns metrics in the following structure:
        {
            "latency": {
                "/endpoint": {
                    "avg": 0.123,
                    "p50": 0.1,
                    "p95": 0.25,
                    "p99": 0.5
                }
            },
            "requests": {
                "/endpoint": {
                    "GET": 123,
                    "POST": 10
                }
            },
            "errors": {
                "/endpoint": {
                    "GET": 2,
                    "POST": 0
                }
            }
        }

        This endpoint reads from the /metrics endpoint and extracts:
        - Latency metrics (p50, p95, p99, average) from http_request_latency_seconds_*
        - Request counts by endpoint/method from http_requests_total
        - Error counts by endpoint/method from http_request_errors_total
        """
        from src.services.metrics_parser import get_metrics_parser

        # Get metrics from the local /metrics endpoint
        parser = get_metrics_parser("http://localhost:8000/metrics")
        metrics = await parser.get_metrics()
        return metrics

    logger.info("  [OK] Parsed metrics endpoint at /api/metrics/parsed")

    # ==================== Sentry Debug Endpoint ====================
    if Config.SENTRY_ENABLED and Config.SENTRY_DSN:

        @app.get("/sentry-debug", tags=["monitoring"], include_in_schema=False)
        async def trigger_sentry_error(raise_exception: bool = False):
            """
            Test endpoint to verify Sentry error tracking is working.
            When raise_exception is False (default) it will capture the error with Sentry but return HTTP 200.
            Pass raise_exception=true to surface the exception for full end-to-end testing.
            """
            import sentry_sdk

            # Send test logs to Sentry
            sentry_sdk.logger.info("Testing Sentry logging integration")
            sentry_sdk.logger.warning("This is a test warning message")
            sentry_sdk.logger.error("This is a test error message")

            def _trigger_zero_division() -> None:
                # Helper to ensure we get a real stack trace for Sentry
                _ = 1 / 0

            if raise_exception:
                # Preserve legacy behaviour for explicit testing
                _trigger_zero_division()

            try:
                _trigger_zero_division()
            except ZeroDivisionError as exc:
                sentry_sdk.capture_exception(exc)
                event_id = sentry_sdk.last_event_id()
                return {
                    "status": "Sentry exception captured",
                    "event_id": event_id,
                    "raised_exception": False,
                }

        logger.info("  [OK] Sentry debug endpoint at /sentry-debug")

    # ==================== Load All Routes ====================
    logger.info("Loading application routes...")

    # Write to file for debugging in CI
    try:
        with open("/tmp/route_loading_debug.txt", "w") as f:
            f.write("Starting route loading...\n")
            f.flush()
    except Exception:
        pass

    # Define v1 routes (OpenAI-compatible API endpoints)
    # These routes are mounted under /v1 prefix via v1_router
    # IMPORTANT: chat & messages must be before catalog to avoid /* being caught by /model/{provider}/{model}
    v1_routes_to_load = [
        ("chat", "Chat Completions"),
        ("detailed_status", "System Detailed Status"),  # Real-time monitoring metrics
        ("messages", "Anthropic Messages API"),  # Claude-compatible endpoint
        ("images", "Image Generation"),  # Image generation endpoints
        ("audio", "Audio Transcription"),  # Whisper audio transcription endpoints
        ("tools", "Server-Side Tools"),  # TTS, calculator, code executor, etc.
        ("catalog", "Model Catalog"),
        ("model_health", "Model Health Tracking"),  # Model health monitoring and metrics
        ("status_page", "Public Status Page"),  # Public status page (no auth required)
        ("butter_analytics", "Butter.dev Cache Analytics"),  # LLM response cache analytics
    ]

    # Define non-v1 routes (loaded directly on app without prefix)
    non_v1_routes_to_load = [
        ("api_models", "API Models Detail"),  # /api/models/detail endpoint for frontend
        ("health", "Health Check"),
        ("availability", "Model Availability"),
        ("ping", "Ping Service"),
        ("monitoring", "Monitoring API"),  # Real-time metrics, health, analytics API
        ("diagnostics", "Diagnostics API"),  # Real-time bottleneck diagnostics
        ("instrumentation", "Instrumentation & Observability"),  # Loki and Tempo endpoints
        ("grafana_metrics", "Grafana Metrics"),  # Prometheus/Loki/Tempo metrics endpoints
        ("ai_sdk", "Vercel AI SDK"),  # AI SDK compatibility endpoint
        ("providers_management", "Providers Management"),  # Provider CRUD operations
        ("models_catalog_management", "Models Catalog Management"),  # Model CRUD operations
        ("model_sync", "Model Sync Service"),  # Dynamic model catalog synchronization
        ("system", "System & Health"),  # Cache management and health monitoring
        (
            "optimization_monitor",
            "Optimization Monitoring",
        ),  # Connection pool, cache, and priority stats
        (
            "health_timeline",
            "System Health Timeline",
        ),  # Provider and model uptime timeline tracking
        ("error_monitor", "Error Monitoring"),  # Error detection and auto-fix system
        ("root", "Root/Home"),
        ("auth", "Authentication"),
        ("users", "User Management"),
        ("api_keys", "API Key Management"),
        ("admin", "Admin Operations"),
        # ("admin_pricing_analytics", "Admin Pricing Analytics"),  # REMOVED - Phase 2 deprecation
        ("api_key_monitoring", "API Key Tracking Monitoring"),  # API key tracking quality metrics
        ("credits", "Credits Management"),  # Credit operations (add, adjust, bulk-add, refund)
        ("audit", "Audit Logs"),
        ("notifications", "Notifications"),
        ("plans", "Subscription Plans"),
        ("rate_limits", "Rate Limiting"),
        ("payments", "Stripe Payments"),
        ("chat_history", "Chat History"),
        ("share", "Chat Share Links"),  # Shareable chat links
        ("ranking", "Model Ranking"),
        ("activity", "Activity Tracking"),
        ("coupons", "Coupon Management"),
        ("referral", "Referral System"),
        ("roles", "Role Management"),
        ("transaction_analytics", "Transaction Analytics"),
        ("analytics", "Analytics Events"),  # Server-side Statsig integration
        # Pricing audit/sync routes removed - deprecated 2026-02 (Phase 3, Issue #1063)
        ("trial_analytics", "Trial Analytics"),  # Trial monitoring and abuse detection
        ("partner_trials", "Partner Trials"),  # Partner-specific trials (Redbeard 14-day Pro)
        ("prometheus_data", "Prometheus Data API"),  # Grafana stack telemetry endpoints
        ("nosana", "Nosana GPU Computing"),  # Nosana deployments, jobs, and GPU marketplace
        ("provider_credits", "Provider Credit Monitoring"),  # Monitor provider account balances
        ("code_router", "Code Router Settings"),  # Code-optimized routing configuration
    ]

    loaded_count = 0
    failed_count = 0

    def load_route(module_name: str, display_name: str, target_router):
        """Load a route module and attach it to the target router."""
        nonlocal loaded_count, failed_count
        try:
            # Import the route module
            logger.debug(f"  [LOADING] Importing src.routes.{module_name}...")
            module = __import__(f"src.routes.{module_name}", fromlist=["router"])

            if not hasattr(module, "router"):
                raise AttributeError(f"Module 'src.routes.{module_name}' has no 'router' attribute")

            router = module.router
            logger.debug(f"  [LOADING] Router found for {module_name}")

            # Include the router
            target_router.include_router(router)
            logger.debug(f"  [LOADING] Router included for {module_name}")

            # Log success
            success_msg = f"  [OK] {display_name} ({module_name})"
            logger.info(success_msg)
            loaded_count += 1

        except ImportError as e:
            error_msg = f"  [FAIL] {display_name} ({module_name}) - Import failed"
            logger.error(error_msg)
            logger.error(f"       Error: {str(e)}")
            logger.error(f"       Type: {type(e).__name__}")
            import traceback

            tb = traceback.format_exc()
            logger.error(f"       Traceback:\n{tb}")
            failed_count += 1

            # For critical routes, log more details
            if module_name in ["chat", "messages", "catalog", "health"]:
                logger.error(f"       [CRITICAL] Failed to load critical route: {module_name}")

        except AttributeError as e:
            error_msg = f"  [FAIL] {display_name} ({module_name}) - No router found"
            logger.error(error_msg)
            logger.error(f"       Error: {str(e)}")
            import traceback

            logger.error(f"       Traceback:\n{traceback.format_exc()}")
            failed_count += 1

        except Exception as e:
            error_msg = f"  [FAIL] {display_name} ({module_name}) - Unexpected error"
            logger.error(error_msg)
            logger.error(f"       Error: {str(e)}")
            logger.error(f"       Type: {type(e).__name__}")
            import traceback

            logger.error(f"       Traceback:\n{traceback.format_exc()}")
            failed_count += 1

    # Load v1 routes (mounted under /v1 prefix)
    logger.info("Loading v1 API routes (OpenAI-compatible)...")
    for module_name, display_name in v1_routes_to_load:
        load_route(module_name, display_name, v1_router)

    # Mount v1 router on app
    app.include_router(v1_router)
    logger.info("  [OK] v1 router mounted at /v1")

    # Add /models route at root for backwards compatibility
    # Only mount the specific /models endpoint, not the entire catalog router
    try:
        from src.routes.catalog import get_all_models

        app.get("/models", tags=["models"])(get_all_models)
        logger.info("  [OK] /models route added at root (backwards compatibility)")
    except ImportError as e:
        logger.warning(f"  [WARN] Could not add /models route for backwards compatibility: {e}")

    # Load non-v1 routes (mounted directly on app)
    logger.info("Loading non-v1 routes...")
    for module_name, display_name in non_v1_routes_to_load:
        load_route(module_name, display_name, app)

    # Log summary
    logger.info("\nRoute Loading Summary:")
    logger.info(f"   [OK] Loaded: {loaded_count}")
    if failed_count > 0:
        logger.warning(f"   [FAIL] Failed: {failed_count}")
    logger.info(f"   Total: {loaded_count + failed_count}")

    # ==================== Sentry Tunnel Router ====================
    # Load Sentry tunnel router separately (at root /monitoring path)
    try:
        from src.routes.monitoring import sentry_tunnel_router

        app.include_router(sentry_tunnel_router)
        logger.info("  [OK] Sentry Tunnel (POST /monitoring)")
    except ImportError as e:
        logger.warning(f"  [SKIP] Sentry tunnel router not loaded: {e}")

    # ==================== Prometheus/Grafana SimpleJSON Datasource Router ====================
    # Load Prometheus/Grafana datasource router for dashboard compatibility
    try:
        from src.routes.prometheus_grafana import router as prometheus_grafana_router

        app.include_router(prometheus_grafana_router)
        logger.info("  [OK] Prometheus/Grafana SimpleJSON Datasource (/prometheus/datasource/*)")
    except ImportError as e:
        logger.warning(f"  [SKIP] Prometheus/Grafana datasource router not loaded: {e}")

    # ==================== General Router API Router ====================
    # Load general router API endpoints for NotDiamond-powered routing
    try:
        from src.routes.general_router import router as general_router_router

        app.include_router(general_router_router)
        logger.info("  [OK] General Router API (/general-router/*)")
    except ImportError as e:
        logger.warning(f"  [SKIP] General router API not loaded: {e}")

    # ==================== Circuit Breaker Status Router ====================
    # Load circuit breaker monitoring and management endpoints
    try:
        from src.routes.circuit_breaker_status import router as circuit_breaker_router

        app.include_router(circuit_breaker_router)
        logger.info("  [OK] Circuit Breaker Status API (/circuit-breakers/*)")
    except ImportError as e:
        logger.warning(f"  [SKIP] Circuit breaker status router not loaded: {e}")

    # ==================== Exception Handlers ====================

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """
        Handle HTTPException with detailed error responses.

        Converts basic HTTPExceptions to detailed error responses with
        suggestions, context, and documentation links.
        """
        from src.utils.error_handlers import detailed_http_exception_handler

        return await detailed_http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """
        Handle unexpected exceptions with detailed error responses.

        Captures exceptions in monitoring tools (PostHog, Sentry) and returns
        a detailed internal error response to the client.
        """
        logger.error(f"Unhandled exception: {exc}", exc_info=True)

        # Get request ID from request state
        request_id = getattr(request.state, "request_id", None)

        # Capture exception in PostHog for error tracking
        try:
            from src.services.posthog_service import posthog_service

            # Extract user info from request if available
            distinct_id = "system"
            properties = {
                "path": request.url.path,
                "method": request.method,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "request_id": request_id,
            }

            # Try to get user ID from request state or headers
            if hasattr(request.state, "user_id"):
                distinct_id = request.state.user_id
            elif "authorization" in request.headers:
                # Use a hash of the auth header as distinct_id if no user_id available
                import hashlib

                auth_hash = hashlib.sha256(request.headers["authorization"].encode()).hexdigest()[
                    :16
                ]
                distinct_id = f"user_{auth_hash}"

            posthog_service.capture_exception(
                exception=exc, distinct_id=distinct_id, properties=properties
            )
        except Exception as posthog_error:
            logger.warning(f"Failed to capture exception in PostHog: {posthog_error}")

        # Return detailed internal error response
        from src.utils.error_factory import DetailedErrorFactory

        error_response = DetailedErrorFactory.internal_error(
            operation="request_processing",
            error=exc,
            request_id=request_id,
        )

        return JSONResponse(
            status_code=500,
            content=error_response.dict(exclude_none=True),
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    # ==================== Startup Event ====================

    # In the on_startup event, add this after database initialization:

    @app.on_event("startup")
    async def on_startup():
        logger.info("\n🔧 Initializing application...")

        try:
            # Initialize OpenTelemetry tracing
            try:
                from src.config.opentelemetry_config import OpenTelemetryConfig

                init_result = OpenTelemetryConfig.initialize()
                if init_result:
                    OpenTelemetryConfig.instrument_fastapi(app)
                    logger.info("  [OK] OpenTelemetry tracing initialized")
                else:
                    logger.warning(
                        "  [WARN] OpenTelemetry initialization returned False - tracing disabled"
                    )
            except Exception as otel_e:
                logger.warning(f"    OpenTelemetry initialization warning: {otel_e}", exc_info=True)

            # Initialize Traceloop SDK (OpenLLMetry) for LLM auto-instrumentation
            # Must run after OTel but before any LLM SDK calls
            try:
                from src.config.traceloop_config import initialize_traceloop

                if initialize_traceloop():
                    logger.info("  [OK] Traceloop SDK (OpenLLMetry) initialized")
                else:
                    logger.info("  [SKIP] Traceloop SDK not enabled or not available")
            except ImportError:
                logger.debug("  [SKIP] Traceloop SDK not installed")
            except Exception as tl_e:
                logger.warning(f"    Traceloop initialization warning: {tl_e}")

            # Validate configuration
            logger.info("    Validating configuration...")
            Config.validate()
            logger.info("  [OK] Configuration validated")

            # Warn if admin key is missing in production (don't fail startup)
            if Config.IS_PRODUCTION and not os.environ.get("ADMIN_API_KEY"):
                logger.warning(
                    "  [WARN] ADMIN_API_KEY is not set in production. Admin endpoints will be inaccessible."
                )
                logger.warning(
                    "        Set ADMIN_API_KEY environment variable to enable admin functionality."
                )

            # Initialize database
            try:
                logger.info("    Initializing database...")
                from src.config.supabase_config import init_db

                init_db()
                logger.info("   Database initialized")

            except Exception as db_e:
                logger.warning(f"    Database initialization warning: {db_e}")

            # Set default admin user in background (don't block startup)
            async def setup_admin_user_background():
                try:
                    from src.config.supabase_config import get_supabase_client
                    from src.db.roles import UserRole, update_user_role

                    ADMIN_EMAIL = Config.ADMIN_EMAIL

                    if not ADMIN_EMAIL:
                        logger.debug("ADMIN_EMAIL not configured - skipping admin setup")
                        return

                    client = get_supabase_client()
                    result = (
                        client.table("users").select("id, role").eq("email", ADMIN_EMAIL).execute()
                    )

                    if result.data:
                        user = result.data[0]
                        current_role = user.get("role", "user")

                        if current_role != UserRole.ADMIN:
                            update_user_role(
                                user_id=user["id"],
                                new_role=UserRole.ADMIN,
                                reason="Default admin setup on startup",
                            )
                            logger.info(f"   Set {ADMIN_EMAIL} as admin")
                        else:
                            logger.debug(f"{ADMIN_EMAIL} is already admin")

                except Exception as admin_e:
                    logger.warning(f"Admin setup warning: {admin_e}")

            asyncio.create_task(setup_admin_user_background())

            # Initialize analytics services (Statsig, PostHog, and Braintrust)
            try:
                logger.info("   Initializing analytics services...")

                # Initialize Statsig
                from src.services.statsig_service import statsig_service

                await statsig_service.initialize()
                logger.info("   Statsig analytics initialized")

                # Initialize PostHog
                from src.services.posthog_service import posthog_service

                posthog_service.initialize()
                logger.info("   PostHog analytics initialized")

                # Initialize Braintrust tracing service
                # Uses centralized service to ensure spans are properly associated with project
                try:
                    from src.services.braintrust_service import initialize_braintrust

                    if initialize_braintrust(project="Gatewayz Backend"):
                        logger.info("   Braintrust tracing initialized (async_flush=False)")
                    else:
                        logger.warning(
                            "   Braintrust tracing not available (check BRAINTRUST_API_KEY)"
                        )
                except Exception as bt_e:
                    logger.warning(f"    Braintrust initialization warning: {bt_e}")

            except Exception as analytics_e:
                logger.warning(f"    Analytics initialization warning: {analytics_e}")

            # Cache warming is handled by preload_hot_models_cache() in lifespan
            # and update_full_model_catalog_loop() background task.
            # No additional cache warming needed here to avoid thread pool contention.
            logger.info("  [OK] Cache warming delegated to lifespan background tasks")

            # NOTE: Health monitoring is handled by the dedicated health-service container
            # The main API reads health data from Redis cache populated by health-service
            # See: health-service/main.py and DISABLE_ACTIVE_HEALTH_MONITORING env var
            logger.info("   Health monitoring handled by dedicated health-service container")

        except Exception as e:
            logger.error(f"   Startup initialization failed: {e}")

        logger.info("\n🎉 Application startup complete!")
        logger.info(" API Documentation: http://localhost:8000/docs")
        logger.info(" Health Check: http://localhost:8000/health")
        logger.info(" Public Status Page: http://localhost:8000/v1/status\n")

    # ==================== Shutdown Event ====================

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("🛑 Shutting down application...")

        # NOTE: Health monitoring is handled by the dedicated health-service container
        # No health monitor shutdown needed in main API
        logger.info("   Health monitoring handled by health-service (no shutdown needed)")

        # Shutdown OpenTelemetry
        try:
            from src.config.opentelemetry_config import OpenTelemetryConfig

            OpenTelemetryConfig.shutdown()
        except Exception as e:
            logger.warning(f"    OpenTelemetry shutdown warning: {e}")

        # Shutdown Traceloop SDK (OpenLLMetry) - only if available
        try:
            from src.config.traceloop_config import shutdown as traceloop_shutdown, is_initialized

            if is_initialized():
                traceloop_shutdown()
        except ImportError:
            pass  # Traceloop not installed
        except Exception as e:
            logger.warning(f"    Traceloop shutdown warning: {e}")

        # Shutdown analytics services gracefully
        try:
            from src.services.statsig_service import statsig_service

            await statsig_service.shutdown()
            logger.info("   Statsig shutdown complete")
        except Exception as e:
            logger.warning(f"    Statsig shutdown warning: {e}")

        try:
            from src.services.posthog_service import posthog_service

            posthog_service.shutdown()
            logger.info("   PostHog shutdown complete")
        except Exception as e:
            logger.warning(f"    PostHog shutdown warning: {e}")

    return app


# Export a default app instance for environments that import `app`
app = create_app()

# Vercel/CLI entry point
if __name__ == "__main__":
    import uvicorn

    logger.info(" Starting Gatewayz API server...")
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
