"""
Gatewayz Health Monitoring Service

A dedicated microservice for model health monitoring and availability tracking.
This service runs independently from the main API to prevent heavy health checks
from affecting API response times and stability.

Key responsibilities:
- Active health monitoring of 10,000+ models across providers
- Publishing health metrics to Redis for main API consumption
- Availability tracking with circuit breaker patterns
- Model tier management and prioritization

Deployment:
- Deploy as separate Railway service
- Shares Redis with main API
- Can scale independently
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Add parent directory to path to import shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import Config
from src.config.logging_config import configure_logging

# Configure logging
configure_logging()
logger = logging.getLogger(__name__)

# Health check interval (default 5 minutes)
HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "300"))

# Whether to use intelligent (tiered) monitoring vs simple monitoring
# IMPORTANT: Use intelligent monitor for 9,000+ models to avoid memory exhaustion
# Simple monitor loads all models into memory and will crash with large datasets
USE_INTELLIGENT_MONITOR = os.environ.get("USE_INTELLIGENT_MONITOR", "true").lower() == "true"

# Memory limit safeguard (in MB)
MEMORY_LIMIT_MB = int(os.environ.get("MEMORY_LIMIT_MB", "28000"))  # 28GB for 32GB container

# Track which monitor is actually running (may differ from config if fallback occurs)
_active_monitor_type: str = "none"  # "intelligent", "simple", or "none"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    Starts and stops the health monitoring services.
    """
    try:
        logger.info("=" * 60)
        logger.info("Starting Gatewayz Health Monitoring Service")
        logger.info("=" * 60)
        
        # Log memory configuration
        logger.info(f"Memory limit: {MEMORY_LIMIT_MB}MB")
        logger.info(f"Using Intelligent Monitor: {USE_INTELLIGENT_MONITOR}")

        # Validate critical environment variables
        is_valid, missing_vars = Config.validate_critical_env_vars()
        if not is_valid:
            logger.error(f"Missing required environment variables: {missing_vars}")
            # Continue anyway - some vars might not be needed for health checks
            logger.warning("Proceeding with available configuration...")

        # Initialize Redis connection
        try:
            from src.config.redis_config import get_redis_client
            redis_client = get_redis_client()
            if redis_client:
                logger.info("Redis connection established")
            else:
                logger.warning("Redis not available - health data will not be shared")
        except Exception as e:
            logger.warning(f"Redis initialization warning: {e}")
    except Exception as startup_error:
        logger.error(f"CRITICAL: Startup error in lifespan: {startup_error}", exc_info=True)
        raise

    # Start health monitoring based on configuration
    try:
        global _active_monitor_type
        if USE_INTELLIGENT_MONITOR:
            logger.info("Starting Intelligent Health Monitor (tiered, database-backed)")
            try:
                from src.services.intelligent_health_monitor import intelligent_health_monitor
                await intelligent_health_monitor.start_monitoring()
                _active_monitor_type = "intelligent"
                logger.info("Intelligent health monitoring started successfully")
            except Exception as e:
                logger.error(f"Failed to start intelligent health monitor: {e}", exc_info=True)
                # Fall back to simple monitor
                logger.info("Falling back to simple health monitor...")
                try:
                    from src.services.model_health_monitor import health_monitor
                    # Apply configured interval before starting
                    health_monitor.check_interval = HEALTH_CHECK_INTERVAL
                    await health_monitor.start_monitoring()
                    _active_monitor_type = "simple"
                    logger.info("Simple health monitoring started (fallback)")
                except Exception as fallback_error:
                    logger.error(f"Failed to start fallback health monitor: {fallback_error}", exc_info=True)
        else:
            logger.info("Starting Simple Health Monitor")
            try:
                from src.services.model_health_monitor import health_monitor
                # Apply configured interval before starting
                health_monitor.check_interval = HEALTH_CHECK_INTERVAL
                await health_monitor.start_monitoring()
                _active_monitor_type = "simple"
                logger.info("Simple health monitoring started successfully")
            except Exception as e:
                logger.error(f"Failed to start simple health monitor: {e}", exc_info=True)

        # Start availability monitoring
        try:
            from src.services.model_availability import availability_service
            await availability_service.start_monitoring()
            logger.info("Availability monitoring started")
        except Exception as e:
            logger.warning(f"Availability monitoring failed to start: {e}")
    except Exception as monitor_error:
        logger.error(f"CRITICAL: Monitor startup error: {monitor_error}", exc_info=True)
        # Don't raise - allow app to start even if monitoring fails

    logger.info("=" * 60)
    logger.info("Health Monitoring Service is running")
    logger.info(f"Health check interval: {HEALTH_CHECK_INTERVAL}s")
    logger.info(f"Monitor type: {_active_monitor_type}")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("Shutting down Health Monitoring Service...")

    # Stop availability monitoring
    try:
        from src.services.model_availability import availability_service
        await availability_service.stop_monitoring()
        logger.info("Availability monitoring stopped")
    except Exception as e:
        logger.warning(f"Availability monitoring shutdown warning: {e}")

    # Stop health monitoring based on which monitor is actually running
    if _active_monitor_type == "intelligent":
        try:
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            await intelligent_health_monitor.stop_monitoring()
            logger.info("Intelligent health monitoring stopped")
        except Exception as e:
            logger.warning(f"Intelligent health monitoring shutdown warning: {e}")
    elif _active_monitor_type == "simple":
        try:
            from src.services.model_health_monitor import health_monitor
            await health_monitor.stop_monitoring()
            logger.info("Simple health monitoring stopped")
        except Exception as e:
            logger.warning(f"Simple health monitoring shutdown warning: {e}")

    # Cleanup Supabase client and close httpx connections
    # This prevents connection leaks since the health-service makes continuous
    # database queries via the intelligent monitor and helper functions
    try:
        from src.config.supabase_config import cleanup_supabase_client
        cleanup_supabase_client()
    except Exception as e:
        logger.warning(f"Supabase cleanup warning: {e}")

    logger.info("Health Monitoring Service shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Gatewayz Health Monitoring Service",
    description="Dedicated microservice for model health monitoring and availability tracking",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint for health-service."""
    return {
        "service": "health-monitor",
        "status": "running",
        "version": "1.0.0",
        "endpoints": ["/health", "/status", "/metrics", "/cache/stats"],
    }


@app.get("/health")
async def health_check():
    """
    Simple health check for the monitoring service itself.
    Used by Railway/load balancer to verify the service is running.
    """
    return {
        "status": "healthy",
        "service": "health-monitor",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _get_intelligent_monitor_counts() -> tuple[int, int]:
    """Get model and provider counts from database for intelligent monitor."""
    try:
        from src.config.supabase_config import supabase

        # Get count of tracked models
        models_response = (
            supabase.table("model_health_tracking")
            .select("id", count="exact")
            .eq("is_enabled", True)
            .execute()
        )
        models_count = models_response.count or 0

        # Get distinct provider count
        providers_response = (
            supabase.table("model_health_tracking")
            .select("provider")
            .eq("is_enabled", True)
            .execute()
        )
        providers = set(row.get("provider") for row in (providers_response.data or []))
        providers_count = len(providers)

        return models_count, providers_count
    except Exception as e:
        logger.warning(f"Failed to get intelligent monitor counts: {e}")
        return 0, 0


@app.get("/status")
async def get_status():
    """
    Get detailed status of the health monitoring service.
    """
    try:
        # Handle case where no monitor started successfully
        if _active_monitor_type == "none":
            from src.services.model_availability import availability_service
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "service": "health-monitor",
                    "monitor_type": "none",
                    "health_monitoring_active": False,
                    "availability_monitoring_active": availability_service.monitoring_active,
                    "models_tracked": 0,
                    "providers_tracked": 0,
                    "message": "Health monitoring failed to start",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Use actual running monitor type
        if _active_monitor_type == "intelligent":
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            monitor = intelligent_health_monitor
            # Intelligent monitor stores data in database, not memory
            models_count, providers_count = await _get_intelligent_monitor_counts()
        else:
            from src.services.model_health_monitor import health_monitor
            monitor = health_monitor
            # Simple monitor stores data in memory
            models_count = len(getattr(monitor, "health_data", {}))
            providers_count = len(getattr(monitor, "provider_data", {}))

        from src.services.model_availability import availability_service

        return {
            "status": "running",
            "service": "health-monitor",
            "monitor_type": _active_monitor_type,
            "health_monitoring_active": monitor.monitoring_active,
            "availability_monitoring_active": availability_service.monitoring_active,
            "models_tracked": models_count,
            "providers_tracked": providers_count,
            "availability_cache_size": len(availability_service.availability_cache),
            "health_check_interval": HEALTH_CHECK_INTERVAL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


async def _get_intelligent_monitor_summary() -> dict:
    """Get health summary from database for intelligent monitor."""
    try:
        from src.config.supabase_config import supabase

        # Get accurate total count of tracked models
        count_response = (
            supabase.table("model_health_tracking")
            .select("id", count="exact")
            .eq("is_enabled", True)
            .execute()
        )
        total_models = count_response.count or 0

        # Get recent health data sample from database
        models_response = (
            supabase.table("model_health_tracking")
            .select("model, provider, gateway, current_status, last_check_at, response_time_ms")
            .eq("is_enabled", True)
            .order("last_check_at", desc=True)
            .limit(100)
            .execute()
        )

        models_data = models_response.data or []

        # Get active incidents
        incidents_response = (
            supabase.table("model_health_incidents")
            .select("*")
            .eq("resolved", False)
            .execute()
        )
        incidents = incidents_response.data or []

        # Calculate summary stats from sample (note: this is from the 100 most recent)
        status_counts = {}
        for model in models_data:
            status = model.get("current_status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "monitoring_active": True,
            "monitor_type": "intelligent",
            "models_sample": models_data[:20],  # Return sample of recent models
            "total_models_tracked": total_models,  # Accurate count from database
            "status_distribution": status_counts,  # Distribution from recent 100 models
            "active_incidents": len(incidents),
            "last_check": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.warning(f"Failed to get intelligent monitor summary: {e}")
        return {
            "monitoring_active": False,
            "error": str(e),
            "last_check": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/metrics")
async def get_metrics():
    """
    Get current health metrics summary.
    Can be used by Prometheus or other monitoring tools.
    """
    try:
        # Handle case where no monitor started successfully
        if _active_monitor_type == "none":
            return JSONResponse(
                status_code=503,
                content={
                    "monitoring_active": False,
                    "monitor_type": "none",
                    "message": "Health monitoring failed to start",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Use actual running monitor type
        if _active_monitor_type == "intelligent":
            # Intelligent monitor doesn't have get_health_summary(), query database
            summary = await _get_intelligent_monitor_summary()
        else:
            from src.services.model_health_monitor import health_monitor
            summary = health_monitor.get_health_summary()

        return summary
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.post("/check/trigger")
async def trigger_health_check():
    """
    Manually trigger a health check cycle.
    Useful for testing or forcing an immediate update.
    """
    try:
        # Use actual running monitor type, not config
        # Note: Using internal method as monitors don't expose public trigger interface
        if _active_monitor_type == "intelligent":
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            # Get models due for checking and check them
            models = await intelligent_health_monitor._get_models_for_checking()
            for model in models[:10]:  # Limit to 10 for manual trigger
                await intelligent_health_monitor._check_model_health(model)
        elif _active_monitor_type == "simple":
            from src.services.model_health_monitor import health_monitor
            await health_monitor._perform_health_checks()
        else:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "message": "No health monitor is currently active",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        return {
            "status": "success",
            "message": "Health check triggered",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error triggering health check: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


@app.get("/cache/stats")
async def get_cache_stats():
    """
    Get Redis cache statistics for health data.
    """
    try:
        from src.services.simple_health_cache import simple_health_cache

        return {
            "cache_available": simple_health_cache.redis_client is not None,
            "system_health_cached": simple_health_cache.get_system_health() is not None,
            "providers_health_cached": simple_health_cache.get_providers_health() is not None,
            "models_health_cached": simple_health_cache.get_models_health() is not None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8001"))
    logger.info(f"Starting Health Monitoring Service on port {port}")
    logger.info("=" * 60)
    logger.info("Service is now listening for requests")
    logger.info("=" * 60)
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
    except Exception as e:
        logger.error(f"FATAL: Failed to start uvicorn: {e}", exc_info=True)
        raise
