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
from src.services.connection_pool import clear_connection_pools, get_pool_stats
from src.services.prometheus_remote_write import (
    init_prometheus_remote_write,
    shutdown_prometheus_remote_write,
)
from src.services.response_cache import get_cache
from src.services.tempo_otlp import init_tempo_otlp, init_tempo_otlp_fastapi

logger = logging.getLogger(__name__)


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
    import time

    max_retries = 3
    retry_delay = 2.0  # seconds
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

        # Initialize Tempo/OpenTelemetry OTLP tracing
        try:
            init_tempo_otlp()
            init_tempo_otlp_fastapi(app)
            logger.info("Tempo/OTLP tracing initialized")
        except Exception as e:
            logger.warning(f"Tempo/OTLP initialization warning: {e}")

        # Initialize Prometheus remote write
        try:
            await init_prometheus_remote_write()
            logger.info("Prometheus remote write initialized")
        except Exception as e:
            logger.warning(f"Prometheus remote write initialization warning: {e}")

        # Health monitoring is handled by the dedicated health-service container
        # The main API reads health data from Redis cache populated by health-service
        # This prevents heavy health checks from affecting API response times
        logger.info("⏭️  Health monitoring handled by dedicated health-service container")
        logger.info("   Main API reads health data from Redis cache")
        logger.info("✅ Passive health monitoring active (from real API calls in chat.py/messages.py)")

        # Initialize connection pools (they're lazy-loaded, but log readiness)
        pool_stats = get_pool_stats()
        logger.info(f"Connection pool manager ready: {pool_stats}")

        # Initialize response cache
        get_cache()
        logger.info("Response cache initialized")

        # Initialize autonomous error monitoring
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

        logger.info("All monitoring and health services started successfully")

    except Exception as e:
        logger.error(f"Failed to start monitoring services: {e}")
        # Don't fail startup if monitoring fails

    yield

    # Shutdown
    logger.info("Shutting down monitoring and observability services...")

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
