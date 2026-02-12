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

from src.config.arize_config import init_arize_otel, shutdown_arize_otel
from src.services.autonomous_monitor import get_autonomous_monitor, initialize_autonomous_monitor
from src.services.connection_pool import (
    clear_connection_pools,
    get_pool_stats,
    warmup_provider_connections_async,
)
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

    # PERF: Set larger default thread pool to prevent starvation on low-vCPU containers.
    # Default asyncio executor is min(32, cpu_count+4) which is ~6 on 1-2 vCPU Railway.
    # Under 20 concurrent requests each doing 3-5 to_thread() calls, the pool saturates.
    import concurrent.futures
    from src.config import Config

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=Config.THREAD_POOL_MAX_WORKERS,
        thread_name_prefix="gw-async-worker",
    )
    asyncio.get_event_loop().set_default_executor(executor)
    logger.info(f"  Set default thread pool executor (max_workers={Config.THREAD_POOL_MAX_WORKERS})")

    # Validate critical environment variables at runtime startup

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
        # NOTE: Fal.ai cache now uses Redis and initializes automatically on first access
        # No manual initialization needed - removed legacy initialize_fal_cache_from_catalog() call
        logger.debug("Fal.ai cache will initialize on-demand via Redis")

        # Initialize FastAPI instrumentation synchronously (must complete before serving)
        # This instruments middleware which cannot be safely modified after app starts
        try:
            init_tempo_otlp_fastapi(app)
        except Exception as e:
            logger.warning(f"FastAPI instrumentation warning: {e}")

        # Initialize Tempo/OpenTelemetry OTLP exporter in background with retry
        # Uses exponential backoff to handle Railway timing issues where Tempo
        # may not be ready when the backend starts
        async def init_tempo_exporter_background():
            from src.config.opentelemetry_config import OpenTelemetryConfig

            max_retries = 5
            base_delay = 2.0  # Start with 2 seconds

            for attempt in range(1, max_retries + 1):
                try:
                    # Check if already initialized (e.g., via on_startup event)
                    if OpenTelemetryConfig._initialized:
                        logger.info("Tempo/OTLP tracing already initialized")
                        return

                    success = OpenTelemetryConfig.initialize()
                    if success:
                        logger.info(f"Tempo/OTLP tracing initialized (attempt {attempt}/{max_retries})")
                        return
                    else:
                        # Initialization returned False (endpoint not reachable, etc.)
                        if attempt < max_retries:
                            delay = base_delay * (2 ** (attempt - 1))  # Exponential backoff
                            logger.info(
                                f"Tempo/OTLP initialization attempt {attempt}/{max_retries} failed, "
                                f"retrying in {delay:.1f}s..."
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.warning(
                                f"Tempo/OTLP initialization failed after {max_retries} attempts. "
                                f"Tracing will be disabled. Use POST /api/instrumentation/otel/initialize "
                                f"to manually retry."
                            )
                except Exception as e:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"Tempo/OTLP initialization attempt {attempt}/{max_retries} error: {e}, "
                            f"retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            f"Tempo/OTLP initialization failed after {max_retries} attempts: {e}. "
                            f"Use POST /api/instrumentation/otel/initialize to manually retry."
                        )

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

        # PERF: Pre-warm database connections in background
        # This ensures the HTTP/2 connection pool is ready and eliminates cold-start latency
        async def warmup_database_connections():
            try:
                if db_initialized:
                    logger.info("🔥 Pre-warming database connections...")
                    # Execute a simple query to warm up the connection pool
                    from src.config.supabase_config import get_supabase_client
                    client = get_supabase_client()
                    # Ping the database with a lightweight query
                    await asyncio.to_thread(
                        lambda: client.table("plans").select("id").limit(1).execute()
                    )
                    logger.info("✅ Database connection pool warmed")
            except Exception as e:
                logger.warning(f"Database connection warmup warning: {e}")

        _create_background_task(warmup_database_connections(), name="warmup_database")

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

        # PERF: Preload full catalog cache in background (single DB fetch)
        # NOTE: Only loads the full catalog once. Individual gateway caches are
        # populated lazily on first request (stale-while-revalidate pattern).
        # The background refresh task (update_full_model_catalog_loop) keeps
        # the full catalog warm every 14 minutes after the initial 60s delay.
        async def preload_hot_models_cache():
            try:
                # Wait a few seconds for DB connection pool to stabilize
                await asyncio.sleep(5)

                logger.info("🔥 Preloading full model catalog cache...")
                from src.services.model_catalog_cache import get_cached_full_catalog

                # Single fetch: full catalog (Redis → local memory → DB fallback)
                # This is the most important cache to warm since all other lookups
                # can derive from it. Individual gateway caches warm lazily.
                full_catalog = await asyncio.to_thread(get_cached_full_catalog)

                catalog_count = len(full_catalog) if full_catalog else 0
                logger.info(f"✅ Catalog cache warming complete: {catalog_count} models loaded")
            except Exception as e:
                logger.warning(f"Model cache preload warning: {e}")

        _create_background_task(preload_hot_models_cache(), name="preload_models")

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

        # Clean up any stuck pricing syncs from previous runs
        async def cleanup_stuck_syncs_startup():
            try:
                logger.info("🧹 Running startup cleanup for stuck pricing syncs...")
                from src.services.pricing_sync_cleanup import cleanup_stuck_syncs
                result = await cleanup_stuck_syncs(timeout_minutes=5)
                logger.info(
                    f"✅ Startup cleanup complete: "
                    f"found {result['stuck_syncs_found']}, "
                    f"cleaned {result['syncs_cleaned']}"
                )
            except Exception as e:
                logger.warning(f"Stuck sync cleanup warning: {e}")

        _create_background_task(cleanup_stuck_syncs_startup(), name="cleanup_stuck_syncs")

        # Pricing sync scheduler removed - pricing updates via model sync (Phase 3, Issue #1063)

        # Sync providers from GATEWAY_REGISTRY on startup (ensures DB matches code)
        async def sync_providers_background():
            try:
                from src.services.provider_model_sync_service import sync_providers_on_startup

                result = await sync_providers_on_startup()
                if result["success"]:
                    logger.info(f"✓ Synced {result['providers_synced']} providers from GATEWAY_REGISTRY")
                else:
                    logger.warning(f"Provider sync warning: {result.get('error')}")
            except Exception as e:
                logger.warning(f"Provider sync warning: {e}")

        _create_background_task(sync_providers_background(), name="sync_providers")

        # Optionally sync high-priority models on startup (can be disabled for faster startup)
        sync_models_on_startup = os.environ.get("SYNC_MODELS_ON_STARTUP", "false").lower() == "true"
        if sync_models_on_startup:
            async def sync_initial_models_background():
                try:
                    from src.services.provider_model_sync_service import sync_initial_models_on_startup

                    result = await sync_initial_models_on_startup()
                    if result["success"]:
                        logger.info(f"✓ Initial model sync: {result['total_models_synced']} models")
                except Exception as e:
                    logger.warning(f"Initial model sync warning: {e}")

            _create_background_task(sync_initial_models_background(), name="sync_initial_models")

        # Start background model sync task (runs every N hours)
        model_sync_interval = int(os.environ.get("MODEL_SYNC_INTERVAL_HOURS", "6"))
        async def start_model_sync_background():
            try:
                from src.services.provider_model_sync_service import start_background_model_sync

                await start_background_model_sync(interval_hours=model_sync_interval)
                logger.info(f"✓ Background model sync started (every {model_sync_interval}h)")
            except Exception as e:
                logger.warning(f"Background model sync warning: {e}")

        _create_background_task(start_model_sync_background(), name="start_model_sync")

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

        # Initialize router health snapshot background task
        # This updates pre-computed healthy model lists for the prompt router
        # Critical for meeting < 2ms router latency (single Redis read vs N awaits)
        async def init_router_health_snapshots_background():
            try:
                router_enabled = os.environ.get("ROUTER_ENABLED", "true").lower() == "true"
                if router_enabled:
                    from src.services.background_tasks import start_router_health_snapshot_task

                    start_router_health_snapshot_task()
                    logger.info("✓ Router health snapshot background task started")
                else:
                    logger.info("⏭️  Router disabled via ROUTER_ENABLED env var")
            except Exception as e:
                logger.warning(f"Router health snapshot initialization warning: {e}")

        _create_background_task(init_router_health_snapshots_background(), name="init_router_health_snapshots")

        # Initialize model catalog background refresh task (Prevent 499 Deadlocks)
        async def init_model_catalog_refresh_background():
            try:
                from src.services.background_tasks import start_model_catalog_refresh_task
                start_model_catalog_refresh_task()
            except Exception as e:
                logger.warning(f"Model catalog refresh initialization warning: {e}")

        _create_background_task(init_model_catalog_refresh_background(), name="init_model_catalog_refresh")

        logger.info("All monitoring and health services started successfully")

    except Exception as e:
        logger.error(f"Failed to start monitoring services: {e}")
        # Don't fail startup if monitoring fails

    # Start scheduled model sync (Phase 3 - Issue #996)
    try:
        from src.services.scheduled_sync import start_scheduler

        start_scheduler()
        logger.info("Scheduled model sync service initialized")
    except Exception as e:
        logger.warning(f"Failed to start scheduled model sync: {e}")
        # Don't fail startup if scheduled sync fails to start

    yield

    # Shutdown
    logger.info("Shutting down monitoring and observability services...")

    # Shutdown custom thread pool executor
    executor.shutdown(wait=False)
    logger.info("Thread pool executor shutdown initiated")

    # Stop scheduled model sync (Phase 3 - Issue #996)
    try:
        from src.services.scheduled_sync import stop_scheduler

        stop_scheduler()
        logger.info("Scheduled model sync service stopped")
    except Exception as e:
        logger.warning(f"Scheduled model sync shutdown warning: {e}")

    # Cancel any pending background tasks
    if _background_tasks:
        logger.info(f"Cancelling {len(_background_tasks)} pending background tasks...")
        for task in _background_tasks:
            task.cancel()
        await asyncio.gather(*_background_tasks, return_exceptions=True)
        _background_tasks.clear()

    try:
        # Pricing sync scheduler shutdown removed (Phase 3, Issue #1063)

        # Stop background model sync
        try:
            from src.services.provider_model_sync_service import stop_background_model_sync

            await stop_background_model_sync()
            logger.info("Background model sync stopped")
        except Exception as e:
            logger.warning(f"Model sync shutdown warning: {e}")

        # Stop autonomous error monitoring
        try:
            autonomous_monitor = get_autonomous_monitor()
            await autonomous_monitor.stop()
            logger.info("Autonomous error monitoring stopped")
        except Exception as e:
            logger.warning(f"Error monitoring shutdown warning: {e}")

        # Stop router health snapshot background task
        try:
            from src.services.background_tasks import stop_router_health_snapshot_task

            stop_router_health_snapshot_task()
            logger.info("Router health snapshot task stopped")
        except Exception as e:
            logger.warning(f"Router health snapshot shutdown warning: {e}")

        # Stop model catalog refresh task
        try:
            from src.services.background_tasks import stop_model_catalog_refresh_task
            stop_model_catalog_refresh_task()
        except Exception as e:
            logger.warning(f"Model catalog refresh shutdown warning: {e}")

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

        # Shutdown OpenTelemetry (Tempo tracing)
        try:
            from src.config.opentelemetry_config import OpenTelemetryConfig

            OpenTelemetryConfig.shutdown()
            logger.info("OpenTelemetry (Tempo) shutdown complete")
        except Exception as e:
            logger.warning(f"OpenTelemetry shutdown warning: {e}")

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
