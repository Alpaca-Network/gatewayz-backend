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
USE_INTELLIGENT_MONITOR = os.environ.get("USE_INTELLIGENT_MONITOR", "true").lower() == "true"

# Track which monitor is actually running (may differ from config if fallback occurs)
_active_monitor_type: str = "none"  # "intelligent", "simple", or "none"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    Starts and stops the health monitoring services.
    """
    logger.info("=" * 60)
    logger.info("Starting Gatewayz Health Monitoring Service")
    logger.info("=" * 60)

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

    # Start health monitoring based on configuration
    global _active_monitor_type
    if USE_INTELLIGENT_MONITOR:
        logger.info("Starting Intelligent Health Monitor (tiered, database-backed)")
        try:
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            await intelligent_health_monitor.start_monitoring()
            _active_monitor_type = "intelligent"
            logger.info("Intelligent health monitoring started successfully")
        except Exception as e:
            logger.error(f"Failed to start intelligent health monitor: {e}")
            # Fall back to simple monitor
            logger.info("Falling back to simple health monitor...")
            try:
                from src.services.model_health_monitor import health_monitor
                await health_monitor.start_monitoring()
                _active_monitor_type = "simple"
                logger.info("Simple health monitoring started (fallback)")
            except Exception as fallback_error:
                logger.error(f"Failed to start fallback health monitor: {fallback_error}")
    else:
        logger.info("Starting Simple Health Monitor")
        try:
            from src.services.model_health_monitor import health_monitor
            await health_monitor.start_monitoring()
            _active_monitor_type = "simple"
            logger.info("Simple health monitoring started successfully")
        except Exception as e:
            logger.error(f"Failed to start simple health monitor: {e}")

    # Start availability monitoring
    try:
        from src.services.model_availability import availability_service
        await availability_service.start_monitoring()
        logger.info("Availability monitoring started")
    except Exception as e:
        logger.warning(f"Availability monitoring failed to start: {e}")

    logger.info("=" * 60)
    logger.info("Health Monitoring Service is running")
    logger.info(f"Health check interval: {HEALTH_CHECK_INTERVAL}s")
    logger.info(f"Monitor type: {'Intelligent' if USE_INTELLIGENT_MONITOR else 'Simple'}")
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

    logger.info("Health Monitoring Service shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Gatewayz Health Monitoring Service",
    description="Dedicated microservice for model health monitoring and availability tracking",
    version="1.0.0",
    lifespan=lifespan,
)


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


@app.get("/status")
async def get_status():
    """
    Get detailed status of the health monitoring service.
    """
    try:
        # Use actual running monitor type, not config
        if _active_monitor_type == "intelligent":
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            monitor = intelligent_health_monitor
        else:
            from src.services.model_health_monitor import health_monitor
            monitor = health_monitor

        from src.services.model_availability import availability_service

        return {
            "status": "running",
            "service": "health-monitor",
            "monitor_type": _active_monitor_type,
            "health_monitoring_active": monitor.monitoring_active,
            "availability_monitoring_active": availability_service.monitoring_active,
            "models_tracked": len(getattr(monitor, "health_data", {})),
            "providers_tracked": len(getattr(monitor, "provider_data", {})),
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


@app.get("/metrics")
async def get_metrics():
    """
    Get current health metrics summary.
    Can be used by Prometheus or other monitoring tools.
    """
    try:
        # Use actual running monitor type, not config
        if _active_monitor_type == "intelligent":
            from src.services.intelligent_health_monitor import intelligent_health_monitor
            summary = intelligent_health_monitor.get_health_summary()
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
            # Intelligent monitor uses _monitoring_loop internally, trigger one cycle
            await intelligent_health_monitor._check_tier_models("critical")
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
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
