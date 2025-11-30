"""
Startup service for initializing health monitoring, availability services, and connection pools
"""

import logging
import os
from contextlib import asynccontextmanager

from src.cache import initialize_fal_cache_from_catalog
from src.services.autonomous_monitor import get_autonomous_monitor, initialize_autonomous_monitor
from src.services.connection_pool import clear_connection_pools, get_pool_stats
from src.services.model_availability import availability_service
from src.services.model_health_monitor import health_monitor
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
    from src.config.supabase_config import get_supabase_client
    import time

    max_retries = 3
    retry_delay = 2.0  # seconds
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 Initializing Supabase database client (attempt {attempt}/{max_retries})...")
            get_supabase_client()  # Forces initialization, leverages lazy proxy for flexibility
            logger.info("✅ Supabase client initialized and connection verified")
            break  # Success, exit retry loop
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️  Supabase initialization attempt {attempt}/{max_retries} failed: {e}")

            if attempt < max_retries:
                # Exponential backoff
                wait_time = retry_delay * (2 ** (attempt - 1))
                logger.info(f"⏳ Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                # Final attempt failed
                logger.error(f"❌ CRITICAL: Supabase client initialization failed after {max_retries} attempts")
                # Capture to Sentry with startup context
                try:
                    import sentry_sdk
                    with sentry_sdk.push_scope() as scope:
                        scope.set_context("startup", {
                            "phase": "supabase_initialization",
                            "error_type": type(e).__name__,
                            "attempts": max_retries,
                        })
                        scope.set_tag("component", "startup")
                        scope.level = "fatal"
                        sentry_sdk.capture_exception(e)
                except (ImportError, Exception):
                    pass
                # Fail fast: Don't start the application if database is unavailable
                raise RuntimeError(
                    f"Cannot start application: Database initialization failed after {max_retries} attempts: {last_error}"
                ) from last_error

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

        # Start active health monitoring (proactive periodic checks)
        # This complements passive monitoring from real API calls in chat.py and messages.py
        # Active monitoring detects issues before users encounter them
        try:
            await health_monitor.start_monitoring()
            logger.info("✅ Active health monitoring started (periodic checks)")
        except Exception as e:
            logger.warning(f"Active health monitoring failed to start: {e}")
        logger.info("✅ Passive health monitoring active (from real API calls)")

        # Start availability monitoring
        await availability_service.start_monitoring()
        logger.info("Availability monitoring service started")

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

        # Stop availability monitoring
        await availability_service.stop_monitoring()
        logger.info("Availability monitoring service stopped")

        # Stop active health monitoring
        try:
            await health_monitor.stop_monitoring()
            logger.info("Active health monitoring stopped")
        except Exception as e:
            logger.warning(f"Health monitoring shutdown warning: {e}")
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
    Initialize all monitoring services
    """
    try:
        logger.info("Initializing monitoring services...")

        # Start health monitoring
        await health_monitor.start_monitoring()

        # Start availability monitoring
        await availability_service.start_monitoring()

        logger.info("All services initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        raise


async def shutdown_services():
    """
    Shutdown all monitoring services
    """
    try:
        logger.info("Shutting down services...")

        # Stop availability monitoring
        await availability_service.stop_monitoring()

        # Stop health monitoring
        await health_monitor.stop_monitoring()

        logger.info("All services shut down successfully")

    except Exception as e:
        logger.error(f"Error shutting down services: {e}")
