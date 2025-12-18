"""
Startup service for initializing observability, connection pools, and other services.

NOTE: Active health monitoring is handled by the dedicated health-service container.
The main API reads health data from Redis cache populated by health-service.
See: health-service/main.py
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from src.cache import initialize_fal_cache_from_catalog
from src.services.autonomous_monitor import get_autonomous_monitor, initialize_autonomous_monitor
from src.services.connection_pool import (
    clear_connection_pools,
    get_pool_stats,
    warmup_provider_connections_async,
)
from src.config.arize_config import init_arize_otel, shutdown_arize_otel
from src.services.prometheus_remote_write import (
    init_prometheus_remote_write,
    shutdown_prometheus_remote_write,
)
from src.services.response_cache import get_cache
from src.services.tempo_otlp import init_tempo_otlp, init_tempo_otlp_fastapi

logger = logging.getLogger(__name__)

# Track background tasks to prevent GC and enable cleanup
_background_tasks: set[asyncio.Task] = set()


def _create_background_task(coro, name: str = None) -> asyncio.Task:
    """Create a background task and track it to prevent garbage collection."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _init_google_models_sync() -> None:
    """Synchronous helper to initialize Google models (runs in executor thread)."""
    from src.services.google_models_config import initialize_google_models

    initialize_google_models()


@asynccontextmanager
async def lifespan(app):
    """
    Application lifespan manager for startup and shutdown events
    """
    # Startup
    logger.info("Starting health monitoring and observability services...")

    # Validate critical environment variables at runtime startup
    from src.config import Config

    is_valid, missing_vars = Config.validate_critical_env_vars()
    if not is_valid:
        logger.error(f"❌ CRITICAL: Missing required environment variables: {missing_vars}")
        logger.error("Application cannot start without these variables")
        raise RuntimeError(f"Missing required environment variables: {missing_vars}")
    else:
        logger.info("✅ All critical environment variables validated")

    # Eagerly initialize Supabase client during startup with retry logic
    # This ensures the database is ready before accepting requests, preventing
    # initialization from happening during critical user requests
    # Retry logic handles transient network issues during deployment
    # However, we allow the app to start in degraded mode if DB is unavailable after retries
    from src.config.supabase_config import get_supabase_client

    max_retries = 2
    retry_delay = 1.0  # seconds - reduced for faster startup
    last_error = None
    db_initialized = False

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 Initializing Supabase database client (attempt {attempt}/{max_retries})...")
            get_supabase_client()  # Forces initialization, leverages lazy proxy for flexibility
            logger.info("✅ Supabase client initialized and connection verified")
            db_initialized = True
            break  # Success, exit retry loop
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️  Supabase initialization attempt {attempt}/{max_retries} failed: {e}")

            if attempt < max_retries:
                # Exponential backoff
                wait_time = retry_delay * (2 ** (attempt - 1))
                logger.info(f"⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

    # If all retries failed, log warning and start in degraded mode
    if not db_initialized and last_error:
        logger.warning(f"⚠️  Supabase client initialization failed after {max_retries} attempts: {last_error}")
        logger.warning("Application will start in DEGRADED MODE - database-dependent endpoints may fail")
        # Capture to Sentry with startup context
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_context("startup", {
                    "phase": "supabase_initialization",
                    "error_type": type(last_error).__name__,
                    "attempts": max_retries,
                    "degraded_mode": True,  # Flag that app is running in degraded mode
                })
                scope.set_tag("component", "startup")
                scope.set_tag("degraded_mode", "true")
                scope.level = "warning"  # Warning, not fatal - app can still start
                sentry_sdk.capture_exception(last_error)
        except (ImportError, Exception):
            pass
        # Allow app to start in degraded mode - health endpoints will report DB status
        # The lazy proxy will retry connection on first actual database access

    try:
        # Initialize Fal.ai model cache from static catalog
        try:
            initialize_fal_cache_from_catalog()
            logger.info("Fal.ai model cache initialized from catalog")
        except Exception as e:
            logger.warning(f"Fal.ai cache initialization warning: {e}")

        # Initialize FastAPI instrumentation synchronously (must complete before serving)
        # This instruments middleware which cannot be safely modified after app starts
        try:
            init_tempo_otlp_fastapi(app)
        except Exception as e:
            logger.warning(f"FastAPI instrumentation warning: {e}")

        # Initialize Tempo/OpenTelemetry OTLP exporter in background
        # The endpoint check has 1s timeout, so defer to not block healthcheck
        async def init_tempo_exporter_background():
            try:
                init_tempo_otlp()
                logger.info("Tempo/OTLP tracing initialized")
            except Exception as e:
                logger.warning(f"Tempo/OTLP initialization warning: {e}")

        _create_background_task(init_tempo_exporter_background(), name="init_tempo_exporter")

        # Initialize Arize OTEL for LLM observability in background
        async def init_arize_background():
            try:
                if init_arize_otel():
                    logger.info("Arize OTEL tracing initialized")
                else:
                    logger.debug("Arize OTEL tracing not enabled or not configured")
            except Exception as e:
                logger.warning(f"Arize OTEL initialization warning: {e}")

        _create_background_task(init_arize_background(), name="init_arize_otel")

        # Initialize Prometheus remote write in background
        async def init_prometheus_background():
            try:
                await init_prometheus_remote_write()
                logger.info("Prometheus remote write initialized")
            except Exception as e:
                logger.warning(f"Prometheus remote write initialization warning: {e}")

        _create_background_task(init_prometheus_background(), name="init_prometheus")

        # Health monitoring is handled by the dedicated health-service container
        # The main API reads health data from Redis cache populated by health-service
        # This prevents heavy health checks from affecting API response times
        logger.info("⏭️  Health monitoring handled by dedicated health-service container")
        logger.info("   Main API reads health data from Redis cache")
        logger.info("✅ Passive health monitoring active (from real API calls in chat.py/messages.py)")

        # Initialize connection pools (they're lazy-loaded, but log readiness)
        pool_stats = get_pool_stats()
        logger.info(f"Connection pool manager ready: {pool_stats}")

        # PERF: Pre-warm connections to frequently used AI providers in background
        # This eliminates cold-start penalty (~100-200ms) for first requests
        # Moved to background to not block healthcheck
        async def warmup_connections_background():
            try:
                logger.info("🔥 Pre-warming provider connections (background)...")
                warmup_results = await warmup_provider_connections_async()
                warmed_count = sum(1 for v in warmup_results.values() if v == "ok")
                logger.info(f"✅ Warmed {warmed_count}/{len(warmup_results)} provider connections")
                pool_stats = get_pool_stats()
                logger.info(f"Connection pool after warmup: {pool_stats}")
            except Exception as e:
                logger.warning(f"Provider connection warmup warning: {e}")

        _create_background_task(warmup_connections_background(), name="warmup_connections")

        # Initialize response cache
        get_cache()
        logger.info("Response cache initialized")

        # Initialize Google Vertex AI models catalog in background
        async def init_google_models_background():
            try:
                # Run synchronous initialization in executor to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _init_google_models_sync)
                logger.info("✓ Google Vertex AI models initialized")
            except Exception as e:
                logger.warning(f"Google models initialization warning: {e}", exc_info=True)

        _create_background_task(init_google_models_background(), name="init_google_models")

        # Initialize autonomous error monitoring in background
        async def init_error_monitoring_background():
            try:
                error_monitoring_enabled = (
                    os.environ.get("ERROR_MONITORING_ENABLED", "true").lower() == "true"
                )
                auto_fix_enabled = os.environ.get("AUTO_FIX_ENABLED", "true").lower() == "true"
                scan_interval = int(os.environ.get("ERROR_MONITOR_INTERVAL", "300"))

                if error_monitoring_enabled:
                    await initialize_autonomous_monitor(
                        enabled=True,
                        scan_interval=scan_interval,
                        auto_fix_enabled=auto_fix_enabled,
                    )
                    logger.info("✓ Autonomous error monitoring started")
            except Exception as e:
                logger.warning(f"Error monitoring initialization warning: {e}")

        _create_background_task(init_error_monitoring_background(), name="init_error_monitoring")

        logger.info("All monitoring and health services started successfully")

    except Exception as e:
        logger.error(f"Failed to start monitoring services: {e}")
        # Don't fail startup if monitoring fails

    yield

    # Shutdown
    logger.info("Shutting down monitoring and observability services...")

    # Cancel any pending background tasks
    if _background_tasks:
        logger.info(f"Cancelling {len(_background_tasks)} pending background tasks...")
        for task in _background_tasks:
            task.cancel()
        await asyncio.gather(*_background_tasks, return_exceptions=True)
        _background_tasks.clear()

    try:
        # Stop autonomous error monitoring
        try:
            autonomous_monitor = get_autonomous_monitor()
            await autonomous_monitor.stop()
            logger.info("Autonomous error monitoring stopped")
        except Exception as e:
            logger.warning(f"Error monitoring shutdown warning: {e}")

        # Health monitoring is handled by the dedicated health-service container
        # No health monitor shutdown needed in main API
        logger.info("Health monitoring: handled by health-service (no shutdown needed)")
        logger.info("Passive health monitoring: no shutdown needed (captures real API calls)")

        # Shutdown Prometheus remote write
        try:
            await shutdown_prometheus_remote_write()
            logger.info("Prometheus remote write shutdown complete")
        except Exception as e:
            logger.warning(f"Prometheus shutdown warning: {e}")

        # Shutdown Arize OTEL
        try:
            shutdown_arize_otel()
            logger.info("Arize OTEL shutdown complete")
        except Exception as e:
            logger.warning(f"Arize OTEL shutdown warning: {e}")

        # Clear connection pools
        clear_connection_pools()
        logger.info("Connection pools cleared")

        # Cleanup Supabase client and close httpx connections
        try:
            from src.config.supabase_config import cleanup_supabase_client
            cleanup_supabase_client()
        except Exception as e:
            logger.warning(f"Supabase cleanup warning: {e}")

        logger.info("All monitoring and health services stopped successfully")

    except Exception as e:
        logger.error(f"Error stopping monitoring services: {e}")


async def initialize_services():
    """
    Initialize services for the main API.

    NOTE: Health monitoring is handled by the dedicated health-service container.
    The main API reads health data from Redis cache populated by health-service.
    """
    try:
        logger.info("Initializing services...")
        logger.info("Health monitoring: handled by dedicated health-service container")
        logger.info("All services initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise


async def shutdown_services():
    """
    Shutdown services for the main API.

    NOTE: Health monitoring is handled by the dedicated health-service container.
    No health monitor shutdown needed in main API.
    """
    try:
        logger.info("Shutting down services...")
        logger.info("Health monitoring: handled by health-service (no shutdown needed)")
        logger.info("All services shut down successfully")

    except Exception as e:
        logger.error(f"Error shutting down services: {e}")
