"""
Health monitoring and availability endpoints

Provides comprehensive monitoring of model availability, performance,
and health status across all providers and gateways.

NOTE: Active health monitoring is handled by the dedicated health-service container.
This API reads health data from Redis cache populated by health-service.
See: health-service/main.py
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from src.models.health_models import (
    HealthCheckRequest,
    HealthDashboardResponse,
    HealthStatus,
    HealthSummaryResponse,
    ModelHealthResponse,
    ModelStatusResponse,
    ProviderHealthResponse,
    ProviderStatusResponse,
    SystemHealthResponse,
    UptimeMetricsResponse,
)
from src.security.deps import get_api_key
from src.services.simple_health_cache import (
    simple_health_cache,
)
from src.utils.sentry_context import capture_error

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check():
    """
    Simple health check endpoint

    Returns basic health status for monitoring and load balancing.

    This endpoint always returns HTTP 200 to indicate the application is running,
    even if the database is unavailable (degraded mode). Check the response body
    for detailed status including database connectivity.
    """
    from src.config.supabase_config import get_initialization_status

    # Get database initialization status
    db_status = get_initialization_status()

    # Application is always "healthy" if it's running (responds to requests)
    # Degraded mode means DB is unavailable but app is still serving traffic
    response = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Add database status if there are issues
    if db_status["has_error"]:
        response["database"] = "unavailable"
        response["mode"] = "degraded"
        response["database_error"] = db_status["error_type"]
    elif db_status["initialized"]:
        response["database"] = "connected"
    else:
        response["database"] = "not_initialized"

    return response


@router.get("/health/system", response_model=SystemHealthResponse, tags=["health"])
async def get_system_health(
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
):
    """
    Get overall system health metrics

    Returns comprehensive system health information including:
    - Overall system status
    - Provider counts and statuses
    - Model counts and statuses
    - System uptime percentage

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, default values are returned.

    Query Parameters:
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Get health data from Redis cache (populated by health-service)
        cached = simple_health_cache.get_system_health()
        if cached:
            logger.debug("Returning cached system health from health-service")
            return SystemHealthResponse(**cached)

        # No cached data available - health-service may not have populated cache yet
        logger.warning("System health not in cache - health-service may not be running")
        return SystemHealthResponse(
            overall_status=HealthStatus.UNKNOWN,
            total_providers=0,
            healthy_providers=0,
            degraded_providers=0,
            unhealthy_providers=0,
            total_models=0,
            healthy_models=0,
            degraded_models=0,
            unhealthy_models=0,
            system_uptime=0.0,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to get system health: {e}")
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/system', 'operation': 'get_system_health'},
            tags={'endpoint': 'system_health', 'error_type': type(e).__name__}
        )
        return SystemHealthResponse(
            overall_status=HealthStatus.UNKNOWN,
            total_providers=0,
            healthy_providers=0,
            degraded_providers=0,
            unhealthy_providers=0,
            total_models=0,
            healthy_models=0,
            degraded_models=0,
            unhealthy_models=0,
            system_uptime=0.0,
            last_updated=datetime.now(timezone.utc),
        )


@router.get("/health/providers", tags=["health"])
async def get_providers_health(
    gateway: str | None = Query(None, description="Filter by specific gateway"),
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
):
    """
    Get health metrics for all providers

    Returns health information for all providers including:
    - Provider status and availability
    - Model counts per provider
    - Response times and uptime
    - Error information

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, an empty list is returned.

    Query Parameters:
    - gateway: Filter by specific gateway
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Get system health to get total provider count
        system_health = simple_health_cache.get_system_health() or {}
        total_providers = system_health.get("total_providers", 0)
        
        # Get health data from Redis cache (populated by health-service)
        cached = simple_health_cache.get_providers_health()
        tracked_providers = len(cached) if cached else 0
        
        if cached:
            logger.debug(f"Returning cached providers health from health-service ({tracked_providers} tracked of {total_providers} total)")
            # Apply gateway filter if specified
            if gateway:
                cached = [p for p in cached if p.get("gateway") == gateway]
            
            # Return format that frontend expects
            return {
                "data": cached,
                "providers": cached,  # Also include as 'providers' for compatibility
                "total_providers": total_providers,
                "tracked_providers": tracked_providers,
                "metadata": {
                    "total_providers": total_providers,
                    "tracked_providers": tracked_providers,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }

        # No cached data available
        logger.debug("No providers health in cache - health-service may not be running")
        return {
            "data": [],
            "providers": [],
            "total_providers": total_providers,
            "tracked_providers": 0,
            "metadata": {
                "total_providers": total_providers,
                "tracked_providers": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Failed to get providers health: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/providers', 'operation': 'get_providers_health'},
            tags={'endpoint': 'providers_health', 'error_type': type(e).__name__}
        )
        return {
            "data": [],
            "providers": [],
            "total_providers": 0,
            "tracked_providers": 0,
            "metadata": {
                "total_providers": 0,
                "tracked_providers": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }


@router.get("/health/models", tags=["health"])
async def get_models_health(
    gateway: str | None = Query(None, description="Filter by specific gateway"),
    provider: str | None = Query(None, description="Filter by specific provider"),
    status: str | None = Query(None, description="Filter by health status"),
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
):
    """
    Get health metrics for all models

    Returns health information for all models including:
    - Model status and availability
    - Response times and success rates
    - Error counts and uptime
    - Last check timestamps

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, an empty list is returned.

    Query Parameters:
    - gateway: Filter by specific gateway
    - provider: Filter by specific provider
    - status: Filter by health status
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Get system health to get total model count
        system_health = simple_health_cache.get_system_health() or {}
        total_models = system_health.get("total_models", 0)
        
        # Get health data from Redis cache (populated by health-service)
        cached = simple_health_cache.get_models_health()
        tracked_models = len(cached) if cached else 0
        
        if cached:
            logger.debug(f"Returning cached models health from health-service ({tracked_models} tracked of {total_models} total)")
            # Apply filters
            if gateway:
                cached = [m for m in cached if m.get("gateway") == gateway]
            if provider:
                cached = [m for m in cached if m.get("provider") == provider]
            if status:
                cached = [m for m in cached if m.get("status") == status]
            
            # Return format that frontend expects
            return {
                "data": cached,
                "models": cached,  # Also include as 'models' for compatibility
                "total_models": total_models,
                "tracked_models": tracked_models,
                "metadata": {
                    "total_models": total_models,
                    "tracked_models": tracked_models,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }

        # No cached data available
        logger.debug("No models health in cache - health-service may not be running")
        return {
            "data": [],
            "models": [],
            "total_models": total_models,
            "tracked_models": 0,
            "metadata": {
                "total_models": total_models,
                "tracked_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
    except Exception as e:
        logger.error(f"Failed to get models health: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/models', 'operation': 'get_models_health'},
            tags={'endpoint': 'models_health', 'error_type': type(e).__name__}
        )
        return {
            "data": [],
            "models": [],
            "total_models": 0,
            "tracked_models": 0,
            "metadata": {
                "total_models": 0,
                "tracked_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }


@router.get("/health/model/{model_id}", response_model=ModelHealthResponse, tags=["health"])
async def get_model_health(
    model_id: str,
    gateway: str | None = Query(None, description="Specific gateway to check"),
    api_key: str = Depends(get_api_key),
):
    """
    Get health metrics for a specific model

    Returns detailed health information for the specified model including:
    - Current status and availability
    - Response time metrics
    - Success rate and uptime
    - Error information and timestamps

    Note: Health data is provided by the dedicated health-service container
    via Redis cache.
    """
    try:
        # Get all models from cache and find the specific one
        cached = simple_health_cache.get_models_health()
        if cached:
            for model in cached:
                if model.get("model_id") == model_id:
                    if gateway is None or model.get("gateway") == gateway:
                        return model

        raise HTTPException(
            status_code=404, detail=f"Model {model_id} not found or no health data available"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get model health for {model_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve model health") from e


@router.get("/health/provider/{provider}", response_model=ProviderHealthResponse, tags=["health"])
async def get_provider_health(
    provider: str,
    gateway: str | None = Query(None, description="Specific gateway to check"),
    api_key: str = Depends(get_api_key),
):
    """
    Get health metrics for a specific provider

    Returns detailed health information for the specified provider including:
    - Provider status and availability
    - Model counts and health distribution
    - Response time metrics
    - Overall uptime and error information

    Note: Health data is provided by the dedicated health-service container
    via Redis cache.
    """
    try:
        # Get all providers from cache and find the specific one
        cached = simple_health_cache.get_providers_health()
        if cached:
            for prov in cached:
                if prov.get("provider") == provider:
                    if gateway is None or prov.get("gateway") == gateway:
                        return prov

        raise HTTPException(
            status_code=404, detail=f"Provider {provider} not found or no health data available"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get provider health for {provider}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve provider health") from e


@router.get("/health/summary", response_model=HealthSummaryResponse, tags=["health"])
async def get_health_summary(
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
):
    """
    Get comprehensive health summary

    Returns a complete health overview including:
    - System health metrics
    - All provider health data
    - All model health data
    - Monitoring status

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, a default summary is returned.

    Query Parameters:
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Get health summary from Redis cache (populated by health-service)
        cached = simple_health_cache.get_health_summary()
        if cached:
            logger.debug("Returning cached health summary from health-service")
            return HealthSummaryResponse(**cached)

        # No cached summary, build from individual cache entries (same as dashboard)
        logger.debug("Building health summary from cached components")
        cached_system = simple_health_cache.get_system_health()
        cached_providers = simple_health_cache.get_providers_health() or []
        cached_models = simple_health_cache.get_models_health() or []

        # Build system health response
        if cached_system:
            system_health = SystemHealthResponse(**cached_system)
        else:
            system_health = SystemHealthResponse(
                overall_status=HealthStatus.UNKNOWN,
                total_providers=0,
                healthy_providers=0,
                degraded_providers=0,
                unhealthy_providers=0,
                total_models=0,
                healthy_models=0,
                degraded_models=0,
                unhealthy_models=0,
                system_uptime=0.0,
                last_updated=datetime.now(timezone.utc),
            )

        # Check if cache is stale (no update in last 5 minutes)
        monitoring_active = False
        if cached_system:
            last_updated_str = cached_system.get("last_updated")
            if last_updated_str:
                try:
                    if isinstance(last_updated_str, str):
                        last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                    else:
                        last_updated = last_updated_str
                    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()
                    monitoring_active = age_seconds < 300  # 5 minutes
                except Exception:
                    monitoring_active = False

        return HealthSummaryResponse(
            system=system_health,
            providers=[ProviderHealthResponse(**p) for p in cached_providers] if cached_providers else [],
            models=[ModelHealthResponse(**m) for m in cached_models] if cached_models else [],
            monitoring_active=monitoring_active,
            last_check=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to get health summary: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve health summary") from e


@router.post("/health/check", response_model=dict[str, Any], tags=["health"])
async def perform_health_check(
    request: HealthCheckRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    """
    Trigger health check (deprecated)

    Note: Health monitoring is now handled by the dedicated health-service container.
    This endpoint is kept for backwards compatibility but does not trigger checks.
    Use the health-service /check/trigger endpoint instead.
    """
    return {
        "message": "Health checks are handled by health-service container",
        "note": "Use health-service /check/trigger endpoint to trigger manual checks",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "force_refresh": request.force_refresh,
    }


@router.post("/health/check/now", response_model=dict[str, Any], tags=["health", "admin"])
async def perform_immediate_health_check(api_key: str = Depends(get_api_key)):
    """
    Perform immediate health check (deprecated)

    Note: Health monitoring is now handled by the dedicated health-service container.
    This endpoint is kept for backwards compatibility but does not perform checks.
    Use the health-service /check/trigger endpoint instead.
    """
    # Get current cached data to return something useful
    cached_system = simple_health_cache.get_system_health()
    cached_models = simple_health_cache.get_models_health() or []
    cached_providers = simple_health_cache.get_providers_health() or []

    return {
        "message": "Health checks are handled by health-service container",
        "note": "Use health-service /check/trigger endpoint to trigger manual checks",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models_in_cache": len(cached_models),
        "providers_in_cache": len(cached_providers),
        "system_status": cached_system.get("overall_status", "unknown") if cached_system else "unknown",
        "cache_available": cached_system is not None,
    }


@router.get("/health/uptime", response_model=UptimeMetricsResponse, tags=["health", "uptime"])
async def get_uptime_metrics(api_key: str = Depends(get_api_key)):
    """
    Get uptime metrics for frontend integration

    Returns uptime metrics suitable for frontend status pages including:
    - Current status and uptime percentage
    - Response time averages
    - Request counts and error rates
    - Last incident information

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, default values are returned.
    """
    try:
        # Get system health from cache
        cached_system = simple_health_cache.get_system_health()
        cached_models = simple_health_cache.get_models_health() or []

        if not cached_system:
            # Return default metrics instead of failing
            logger.warning("Uptime metrics not available - health-service may not be running")
            return UptimeMetricsResponse(
                status="unknown",
                uptime_percentage=0.0,
                response_time_avg=None,
                last_incident=None,
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                error_rate=0.0,
                last_updated=datetime.now(timezone.utc),
            )

        # Calculate uptime metrics from cached models
        total_requests = sum(m.get("total_requests", 0) for m in cached_models)
        failed_requests = sum(m.get("error_count", 0) for m in cached_models)
        successful_requests = total_requests - failed_requests

        error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0.0

        # Get average response time
        response_times = [
            m.get("avg_response_time_ms")
            for m in cached_models
            if m.get("avg_response_time_ms")
        ]
        avg_response_time = sum(response_times) / len(response_times) if response_times else None

        # Determine status from cached system health
        # Handle both lowercase and uppercase enum values
        overall_status = cached_system.get("overall_status", "unknown")
        if isinstance(overall_status, str):
            overall_status = overall_status.lower()
        if overall_status == "healthy":
            status = "operational"
        elif overall_status == "degraded":
            status = "degraded"
        elif overall_status in ("unknown", "maintenance"):
            status = "unknown"
        else:
            status = "outage"

        return UptimeMetricsResponse(
            status=status,
            uptime_percentage=cached_system.get("system_uptime", 0.0),
            response_time_avg=avg_response_time,
            last_incident=None,  # Could be enhanced to track incidents
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            error_rate=error_rate,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to get uptime metrics: {e}")
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/uptime', 'operation': 'get_uptime_metrics'},
            tags={'endpoint': 'uptime', 'error_type': type(e).__name__}
        )
        # Return default metrics instead of failing (graceful degradation)
        return UptimeMetricsResponse(
            status="unknown",
            uptime_percentage=0.0,
            response_time_avg=None,
            last_incident=None,
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            error_rate=0.0,
            last_updated=datetime.now(timezone.utc),
        )


@router.get(
    "/health/dashboard", response_model=HealthDashboardResponse, tags=["health", "dashboard"]
)
async def get_health_dashboard(
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
):
    """
    Get complete health dashboard data for frontend

    Returns comprehensive health data formatted for frontend dashboard including:
    - System status with color indicators
    - Provider statuses with counts and metrics
    - Model statuses with response times and uptime
    - Uptime metrics for status page integration

    Note: Health data is provided by the dedicated health-service container
    via Redis cache. If cache is empty, default values are returned.

    Query Parameters:
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Try to get cached dashboard first
        cached = simple_health_cache.get_health_dashboard()
        if cached:
            logger.debug("Returning cached health dashboard from health-service")
            return HealthDashboardResponse(**cached)

        # No cached dashboard, build from individual cache entries
        logger.info("Building health dashboard from cached components")

        # Get system health from cache
        cached_system = simple_health_cache.get_system_health()
        if cached_system:
            system_health = SystemHealthResponse(**cached_system)
        else:
            system_health = SystemHealthResponse(
                overall_status=HealthStatus.UNKNOWN,
                total_providers=0,
                healthy_providers=0,
                degraded_providers=0,
                unhealthy_providers=0,
                total_models=0,
                healthy_models=0,
                degraded_models=0,
                unhealthy_models=0,
                system_uptime=0.0,
                last_updated=datetime.now(timezone.utc),
            )

        # Get providers health from cache
        cached_providers = simple_health_cache.get_providers_health() or []
        providers_status = []

        for provider in cached_providers:
            # Determine status color
            status = provider.get("status", "unknown")
            if status in ["online", "ONLINE"]:
                status_color = "green"
                status_text = "Online"
            elif status in ["degraded", "DEGRADED"]:
                status_color = "yellow"
                status_text = "Degraded"
            else:
                status_color = "red"
                status_text = "Offline"

            # Format response time
            response_time_ms = provider.get("avg_response_time_ms")
            response_time_display = None
            if response_time_ms:
                if response_time_ms < 1000:
                    response_time_display = f"{response_time_ms:.0f}ms"
                else:
                    response_time_display = f"{response_time_ms/1000:.1f}s"

            providers_status.append(
                ProviderStatusResponse(
                    provider=provider.get("provider", "unknown"),
                    gateway=provider.get("gateway") or "unknown",
                    status=status_text,
                    status_color=status_color,
                    models_count=provider.get("total_models", 0),
                    healthy_count=provider.get("healthy_models", 0),
                    uptime=f"{provider.get('overall_uptime', 0.0):.1f}%",
                    avg_response_time=response_time_display,
                )
            )

        # Get models health from cache
        cached_models = simple_health_cache.get_models_health() or []
        models_status = []

        for model in cached_models:
            # Determine status color
            status = model.get("status", "unknown")
            if status in ["healthy", "HEALTHY"]:
                status_color = "green"
                status_text = "Healthy"
            elif status in ["degraded", "DEGRADED"]:
                status_color = "yellow"
                status_text = "Degraded"
            else:
                status_color = "red"
                status_text = "Unhealthy"

            # Format response time
            response_time_ms = model.get("response_time_ms")
            response_time_display = None
            if response_time_ms:
                if response_time_ms < 1000:
                    response_time_display = f"{response_time_ms:.0f}ms"
                else:
                    response_time_display = f"{response_time_ms/1000:.1f}s"

            # Format last checked
            last_checked_display = None
            last_checked = model.get("last_checked")
            if last_checked:
                try:
                    from datetime import datetime as dt
                    if isinstance(last_checked, str):
                        parsed = dt.fromisoformat(last_checked.replace("Z", "+00:00"))
                        last_checked_display = parsed.strftime("%H:%M:%S")
                    elif hasattr(last_checked, "strftime"):
                        last_checked_display = last_checked.strftime("%H:%M:%S")
                except Exception as e:
                    logger.debug(f"Failed to parse last_checked '{last_checked}': {e}")

            model_id = model.get("model_id", "unknown")
            models_status.append(
                ModelStatusResponse(
                    model_id=model_id,
                    name=model_id.split("/")[-1] if "/" in model_id else model_id,
                    provider=model.get("provider", "unknown"),
                    status=status_text,
                    status_color=status_color,
                    response_time=response_time_display,
                    uptime=f"{model.get('uptime_percentage', 0.0):.1f}%",
                    last_checked=last_checked_display,
                )
            )

        # Get uptime metrics
        try:
            uptime_metrics = await get_uptime_metrics(api_key)
        except Exception as e:
            logger.warning(f"Failed to get uptime metrics, using defaults: {e}")
            uptime_metrics = UptimeMetricsResponse(
                status="unknown",
                uptime_percentage=0.0,
                response_time_avg=None,
                last_incident=None,
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                error_rate=0.0,
                last_updated=datetime.now(timezone.utc),
            )

        # Determine if monitoring is active based on cache availability
        monitoring_active = cached_system is not None

        response = HealthDashboardResponse(
            system_status=system_health,
            providers=providers_status,
            models=models_status,
            uptime_metrics=uptime_metrics,
            last_updated=system_health.last_updated or datetime.now(timezone.utc),
            monitoring_active=monitoring_active,
        )

        return response
    except Exception as e:
        logger.error(f"Failed to get health dashboard: {e}")
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/dashboard', 'operation': 'get_health_dashboard'},
            tags={'endpoint': 'dashboard', 'error_type': type(e).__name__}
        )
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve health dashboard: {str(e)}"
        ) from e


@router.get("/health/status", response_model=dict[str, Any], tags=["health", "status"])
async def get_health_status(api_key: str = Depends(get_api_key)):
    """
    Get simple health status for quick checks

    Returns a simple status response suitable for health checks and monitoring tools.

    Note: Health data is provided by the dedicated health-service container
    via Redis cache.
    """
    try:
        cached_system = simple_health_cache.get_system_health()
        if not cached_system:
            return {
                "status": "unknown",
                "message": "Health data not available - health-service may not be running",
                "monitoring_active": False,
                "data_source": "health-service (via Redis cache)",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Check if cache is stale (no update in last 5 minutes)
        monitoring_active = False
        last_updated_str = cached_system.get("last_updated")
        if last_updated_str:
            try:
                if isinstance(last_updated_str, str):
                    last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                else:
                    last_updated = last_updated_str
                age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()
                monitoring_active = age_seconds < 300  # 5 minutes
            except Exception:
                monitoring_active = False

        return {
            "status": cached_system.get("overall_status", "unknown"),
            "uptime": cached_system.get("system_uptime", 0.0),
            "healthy_models": cached_system.get("healthy_models", 0),
            "total_models": cached_system.get("total_models", 0),
            "monitoring_active": monitoring_active,
            "data_source": "health-service (via Redis cache)",
            "timestamp": cached_system.get("last_updated", datetime.now(timezone.utc).isoformat()),
        }
    except Exception as e:
        logger.error(f"Failed to get health status: {e}")
        return {
            "status": "error",
            "message": "Failed to retrieve health status",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/monitoring/status", response_model=dict[str, Any], tags=["health", "admin"])
async def get_monitoring_status(api_key: str = Depends(get_api_key)):
    """
    Get monitoring service status

    Returns the status of health and availability monitoring services.

    Note: Health monitoring is handled by the dedicated health-service container.
    This endpoint shows the status of cached data from that service.
    """
    try:
        cached_system = simple_health_cache.get_system_health()
        cached_providers = simple_health_cache.get_providers_health() or []
        cached_models = simple_health_cache.get_models_health() or []

        return {
            "health_monitoring_source": "health-service container",
            "health_data_available": cached_system is not None,
            "health_models_count": len(cached_models),
            "health_providers_count": len(cached_providers),
            "cache_status": "populated" if cached_system else "empty",
            "note": "Health monitoring is handled by dedicated health-service container",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get monitoring status: {e}")
        return {
            "error": "Failed to retrieve monitoring status",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.post("/health/monitoring/start", response_model=dict[str, Any], tags=["health", "admin"])
async def start_health_monitoring(api_key: str = Depends(get_api_key)):
    """
    Start health monitoring service (deprecated)

    Note: Health monitoring is now handled by the dedicated health-service container.
    This endpoint is kept for backwards compatibility but does not start monitoring.
    """
    return {
        "message": "Health monitoring is handled by health-service container",
        "note": "To start monitoring, ensure the health-service container is running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/health/monitoring/stop", response_model=dict[str, Any], tags=["health", "admin"])
async def stop_health_monitoring(api_key: str = Depends(get_api_key)):
    """
    Stop health monitoring service (deprecated)

    Note: Health monitoring is now handled by the dedicated health-service container.
    This endpoint is kept for backwards compatibility but does not stop monitoring.
    """
    return {
        "message": "Health monitoring is handled by health-service container",
        "note": "To stop monitoring, stop the health-service container",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/google-vertex", tags=["health"])
async def check_google_vertex_health():
    """
    Check Google Vertex AI provider health

    Returns detailed diagnostics about Google Vertex AI configuration and credentials:
    - Configuration (project ID, region)
    - Credential loading status
    - Access token generation status
    - Overall health status

    This endpoint does not require authentication and provides diagnostic information
    that can help troubleshoot Google Vertex AI integration issues.

    Example response:
    ```json
    {
        "provider": "google-vertex",
        "health_status": "healthy",
        "status": "healthy",
        "diagnosis": {
            "credentials_available": true,
            "credential_source": "env_json",
            "project_id": "my-project",
            "location": "us-central1",
            "token_available": true,
            "token_valid": true,
            "error": null,
            "steps": [...]
        },
        "timestamp": "2025-01-01T12:00:00Z"
    }
    ```
    """
    try:
        from src.services.google_vertex_client import diagnose_google_vertex_credentials

        diagnosis = diagnose_google_vertex_credentials()

        return {
            "provider": "google-vertex",
            "health_status": diagnosis.get("health_status", "unhealthy"),
            "status": diagnosis.get("health_status", "unhealthy"),
            "diagnosis": diagnosis,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to check Google Vertex AI health: {e}", exc_info=True)
        return {
            "provider": "google-vertex",
            "health_status": "unhealthy",
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/database", tags=["health"])
async def database_health():
    """
    Check database connectivity and health

    Returns database connection status and any errors.
    This is critical for startup diagnostics in Railway.
    """
    try:
        from src.config.supabase_config import get_initialization_status, supabase

        logger.info("Checking database connectivity...")

        # Get initialization status
        init_status = get_initialization_status()

        # Try a simple query to verify connection
        supabase.table("users").limit(1).execute()

        logger.info("✅ Database connection verified")
        return {
            "status": "healthy",
            "database": "supabase",
            "connection": "verified",
            "initialization": init_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        from src.config.supabase_config import get_initialization_status

        logger.error(f"❌ Database connection failed: {type(e).__name__}: {str(e)}")

        # Capture to Sentry
        try:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
        except (ImportError, Exception):
            pass

        return {
            "status": "unhealthy",
            "database": "supabase",
            "connection": "failed",
            "error": str(e),
            "error_type": type(e).__name__,
            "initialization": get_initialization_status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/providers", tags=["health"])
async def provider_health():
    """
    Check provider import status

    Returns which providers successfully imported and which failed.
    This is essential for debugging chat endpoint issues in Railway.
    """
    try:
        from src.routes.chat import _provider_import_errors

        # Count total providers and failed
        total_providers = 16  # Based on the code, there are 16 providers
        failed_count = len(_provider_import_errors)
        loaded_count = total_providers - failed_count

        logger.info(
            f"Provider status: {loaded_count}/{total_providers} loaded, {failed_count} failed"
        )

        return {
            "status": "healthy" if failed_count == 0 else "degraded",
            "total_providers": total_providers,
            "loaded_providers": loaded_count,
            "failed_providers": failed_count,
            "failures": _provider_import_errors if _provider_import_errors else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error checking provider health: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
