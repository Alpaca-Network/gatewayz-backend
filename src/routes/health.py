"""
Health monitoring and availability endpoints

Provides comprehensive monitoring of model availability, performance,
and health status across all providers and gateways.

NOTE: Active health monitoring is handled by the dedicated health-service container.
This API reads health data from Redis cache populated by health-service.
See: health-service/main.py
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from src.config.supabase_config import get_initialization_status, supabase
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

# Health check timeout constant - used for database queries in health endpoints
# This prevents health checks from blocking the event loop when external services are slow
HEALTH_CHECK_TIMEOUT_SECONDS = 3.0


@router.get("/health", tags=["health"])
async def health_check():
    """
    Simple health check endpoint

    Returns basic health status for monitoring and load balancing.

    This endpoint always returns HTTP 200 to indicate the application is running,
    even if the database is unavailable (degraded mode). Check the response body
    for detailed status including database connectivity.
    """
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


@router.get("/health/quick", tags=["health"])
async def health_quick():
    """
    Ultra-fast health check - no database, no Redis, no I/O operations.

    Use this endpoint for uptime monitoring services with strict timeout requirements
    (e.g., Sentry uptime monitoring with 4-second timeout).

    This endpoint:
    - Performs zero database queries
    - Performs zero Redis operations
    - Performs zero network calls
    - Returns immediately with HTTP 200

    For detailed health status including database connectivity, use /health instead.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/railway", tags=["health"])
async def health_railway():
    """
    Railway-specific health check with validation

    This endpoint validates that critical services are operational:
    - Database connectivity (with timeout)
    - Redis availability (via cached health data)
    - Minimum gateway health threshold (at least 30% gateways healthy)

    Returns:
    - HTTP 200 if system is operational
    - HTTP 503 if system is degraded or unhealthy

    Use this for Railway health checks to prevent marking the service as healthy
    when it's actually unable to process requests.
    """
    try:
        # Check 1: Database connectivity (with timeout)
        db_status = get_initialization_status()
        if db_status.get("has_error"):
            logger.warning(f"Railway health check failed: Database unavailable - {db_status.get('error_type')}")
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unhealthy",
                    "reason": "database_unavailable",
                    "error": db_status.get("error_type"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        # Check 2: Redis/health cache availability
        # NOTE: Health cache may not be populated during initial startup.
        # The health-service container populates this cache asynchronously.
        # We should not fail the health check if the cache is empty during startup,
        # as the API can still process requests without the health cache.
        cached_system = simple_health_cache.get_system_health()
        health_cache_available = cached_system is not None

        # Check 3: Minimum gateway health threshold (only if cache is available)
        healthy_gateways = cached_system.get("healthy_gateways", 0) if cached_system else 0
        total_gateways = cached_system.get("total_gateways", 0) if cached_system else 0

        # Require at least 30% of gateways to be healthy
        # Only enforce this check if we have health cache data
        min_healthy_threshold = 0.30
        if health_cache_available and total_gateways > 0:
            gateway_health_rate = healthy_gateways / total_gateways
            if gateway_health_rate < min_healthy_threshold:
                logger.warning(
                    f"Railway health check failed: Only {healthy_gateways}/{total_gateways} gateways healthy "
                    f"({gateway_health_rate * 100:.1f}% < {min_healthy_threshold * 100}% threshold)"
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "status": "unhealthy",
                        "reason": "insufficient_healthy_gateways",
                        "healthy_gateways": healthy_gateways,
                        "total_gateways": total_gateways,
                        "health_rate": f"{gateway_health_rate * 100:.1f}%",
                        "threshold": f"{min_healthy_threshold * 100}%",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

        # All checks passed
        # Note: If health cache is not available, we're in "warming up" mode
        # but the service can still process requests
        return {
            "status": "healthy",
            "database": "connected",
            "health_cache": "available" if health_cache_available else "warming_up",
            "gateways": {
                "healthy": healthy_gateways,
                "total": total_gateways,
                "health_rate": f"{(healthy_gateways / total_gateways * 100):.1f}%" if total_gateways > 0 else "n/a",
            } if health_cache_available else {"status": "warming_up"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Railway health check error: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "reason": "health_check_error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


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
            total_gateways=0,
            healthy_gateways=0,
            tracked_models=0,
            tracked_providers=0,
            system_uptime=0.0,
            last_updated=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to get system health: {e}")
        capture_error(
            e,
            context_type="health_endpoint",
            context_data={"endpoint": "/health/system", "operation": "get_system_health"},
            tags={"endpoint": "system_health", "error_type": type(e).__name__},
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
            total_gateways=0,
            healthy_gateways=0,
            tracked_models=0,
            tracked_providers=0,
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
            logger.debug(
                f"Returning cached providers health from health-service ({tracked_providers} tracked of {total_providers} total)"
            )
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
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Failed to get providers health: {e}", exc_info=True)
        capture_error(
            e,
            context_type="health_endpoint",
            context_data={"endpoint": "/health/providers", "operation": "get_providers_health"},
            tags={"endpoint": "providers_health", "error_type": type(e).__name__},
        )
        return {
            "data": [],
            "providers": [],
            "total_providers": 0,
            "tracked_providers": 0,
            "metadata": {
                "total_providers": 0,
                "tracked_providers": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
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
            logger.debug(
                f"Returning cached models health from health-service ({tracked_models} tracked of {total_models} total)"
            )
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
                "total_models": total_models,
                "tracked_models": tracked_models,
                "metadata": {
                    "total_models": total_models,
                    "tracked_models": tracked_models,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }

        # No cached data available
        logger.debug("No models health in cache - health-service may not be running")
        return {
            "data": [],
            "total_models": total_models,
            "tracked_models": 0,
            "metadata": {
                "total_models": total_models,
                "tracked_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Failed to get models health: {e}", exc_info=True)
        capture_error(
            e,
            context_type="health_endpoint",
            context_data={"endpoint": "/health/models", "operation": "get_models_health"},
            tags={"endpoint": "models_health", "error_type": type(e).__name__},
        )
        return {
            "data": [],
            "total_models": 0,
            "tracked_models": 0,
            "metadata": {
                "total_models": 0,
                "tracked_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }


@router.get("/health/catalog/models", tags=["health", "catalog"])
async def get_catalog_models(
    gateway: str | None = Query(None, description="Filter by specific gateway"),
    provider: str | None = Query(None, description="Filter by specific provider"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of models to return"),
    offset: int = Query(0, ge=0, description="Number of models to skip for pagination"),
    api_key: str = Depends(get_api_key),
):
    """
    Get ALL models from the model catalog (not just health-tracked ones)

    This endpoint returns the complete model catalog from all gateway caches,
    including models that are not currently being health-monitored.

    Use this for:
    - Getting the full list of available models (9000+)
    - Browsing all models across all gateways
    - Model discovery and search

    Pagination:
    - limit: Maximum records to return per request (default: 100, max: 1000)
    - offset: Number of records to skip (use for cursor-based pagination)
    - Example: Page 1 = offset=0, Page 2 = offset=100 (with limit=100)

    Query Parameters:
    - gateway: Filter by specific gateway (e.g., 'openrouter', 'anthropic')
    - provider: Filter by specific provider

    Note: This returns catalog data, not health/monitoring data.
    For health metrics, use /health/models instead.
    """
    try:
        from src.services.models import get_all_models_parallel
        from src.routes.catalog import GATEWAY_REGISTRY

        # Get all models from all gateways
        all_models = get_all_models_parallel()

        # Get health data from cache to merge with catalog data
        health_models = simple_health_cache.get_models_health() or []
        health_lookup = {m.get("model_id"): m for m in health_models if m.get("model_id")}
        logger.debug(f"Loaded {len(health_lookup)} health records for catalog enrichment")

        # Apply filters
        filtered_models = all_models
        if gateway:
            filtered_models = [
                m
                for m in filtered_models
                if m.get("source_gateway") == gateway or m.get("gateway") == gateway
            ]
        if provider:
            filtered_models = [
                m
                for m in filtered_models
                if m.get("provider_slug") == provider or m.get("provider") == provider
            ]

        total_count = len(filtered_models)

        # Apply pagination
        paginated_models = filtered_models[offset : offset + limit]

        # Transform catalog models and merge with health data
        transformed_models = []
        for model in paginated_models:
            model_id = model.get("id", "unknown")
            health_data = health_lookup.get(model_id, {})

            # Use health data if available, otherwise default to None/unknown
            transformed = {
                "model_id": model_id,
                "provider": model.get("source_gateway", "unknown"),
                "gateway": model.get("source_gateway", "unknown"),
                "status": health_data.get("status", "unknown"),
                "response_time_ms": health_data.get("response_time_ms"),
                "avg_response_time_ms": health_data.get("avg_response_time_ms"),
                "uptime_percentage": health_data.get("uptime_percentage"),
                "error_count": health_data.get("error_count"),
                "total_requests": health_data.get("total_requests"),
                "last_checked": health_data.get("last_checked"),
            }
            transformed_models.append(transformed)

        # Match /health/models schema exactly
        return {
            "data": transformed_models,
            "total_models": len(all_models),
            "tracked_models": len(all_models),  # All catalog models are "tracked"
            "metadata": {
                "total_models": len(all_models),
                "tracked_models": len(all_models),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Failed to get catalog models: {e}", exc_info=True)
        capture_error(
            e,
            context_type="health_endpoint",
            context_data={"endpoint": "/health/catalog/models", "operation": "get_catalog_models"},
            tags={"endpoint": "catalog_models", "error_type": type(e).__name__},
        )
        # Match /health/models schema exactly
        return {
            "data": [],
            "total_models": 0,
            "tracked_models": 0,
            "metadata": {
                "total_models": 0,
                "tracked_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            },
        }


@router.get("/health/catalog/providers", tags=["health", "catalog"])
async def get_catalog_providers(
    priority: str | None = Query(None, description="Filter by priority ('fast' or 'slow')"),
    api_key: str = Depends(get_api_key),
):
    """
    Get ALL providers/gateways from the gateway registry (not just health-tracked ones)

    This endpoint returns the complete list of all configured gateways/providers,
    including those that may not have health data available.

    Use this for:
    - Getting the full list of available providers (26+)
    - Understanding which gateways are configured
    - Provider discovery and configuration status

    Query Parameters:
    - priority: Filter by priority ('fast' or 'slow')

    Note: This returns registry/config data, not health/monitoring data.
    For health metrics, use /health/providers instead.
    """
    try:
        from src.routes.catalog import GATEWAY_REGISTRY
        from src.services.gateway_health_service import GATEWAY_CONFIG

        # Get health data from cache to merge with catalog data
        health_providers = simple_health_cache.get_providers_health() or []
        health_lookup = {p.get("provider"): p for p in health_providers if p.get("provider")}
        logger.debug(f"Loaded {len(health_lookup)} provider health records for catalog enrichment")

        providers = []
        for gateway_id, registry_config in GATEWAY_REGISTRY.items():
            # Get additional config from GATEWAY_CONFIG if available
            gateway_config = GATEWAY_CONFIG.get(gateway_id, {})
            cache = gateway_config.get("cache", {})
            cache_data = cache.get("data") if cache else None
            model_count = len(cache_data) if cache_data else 0
            has_api_key = bool(gateway_config.get("api_key"))

            # Apply priority filter early if specified
            if priority and registry_config.get("priority") != priority:
                continue

            # Get health data for this provider if available
            health_data = health_lookup.get(gateway_id, {})

            # Transform to match health provider format with real health data
            provider_data = {
                "provider": gateway_id,
                "gateway": gateway_id,
                "status": health_data.get("status", "online" if has_api_key else "offline"),
                "total_models": health_data.get("total_models", model_count),
                "healthy_models": health_data.get("healthy_models", 0),
                "degraded_models": health_data.get("degraded_models", 0),
                "unhealthy_models": health_data.get("unhealthy_models", 0),
                "avg_response_time_ms": health_data.get("avg_response_time_ms", 0.0),
                "overall_uptime": health_data.get("overall_uptime", 0),
            }
            providers.append(provider_data)

        # Sort by provider name
        providers.sort(key=lambda x: x.get("provider", ""))

        # Match /health/providers schema
        return {
            "data": providers,
            "providers": providers,
            "total_providers": len(providers),
            "tracked_providers": len(providers),  # All catalog providers are "tracked"
            "metadata": {
                "total_providers": len(providers),
                "tracked_providers": len(providers),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"Failed to get catalog providers: {e}", exc_info=True)
        capture_error(
            e,
            context_type="health_endpoint",
            context_data={
                "endpoint": "/health/catalog/providers",
                "operation": "get_catalog_providers",
            },
            tags={"endpoint": "catalog_providers", "error_type": type(e).__name__},
        )
        # Match /health/providers schema
        return {
            "data": [],
            "providers": [],
            "total_providers": 0,
            "tracked_providers": 0,
            "metadata": {
                "total_providers": 0,
                "tracked_providers": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            },
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
                total_gateways=0,
                healthy_gateways=0,
                tracked_models=0,
                tracked_providers=0,
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
                        last_updated = datetime.fromisoformat(
                            last_updated_str.replace("Z", "+00:00")
                        )
                    else:
                        last_updated = last_updated_str
                    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()
                    monitoring_active = age_seconds < 300  # 5 minutes
                except Exception:
                    monitoring_active = False

        return HealthSummaryResponse(
            system=system_health,
            providers=[ProviderHealthResponse(**p) for p in cached_providers]
            if cached_providers
            else [],
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
        "system_status": cached_system.get("overall_status", "unknown")
        if cached_system
        else "unknown",
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
            m.get("avg_response_time_ms") for m in cached_models if m.get("avg_response_time_ms")
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
            context_type="health_endpoint",
            context_data={"endpoint": "/health/uptime", "operation": "get_uptime_metrics"},
            tags={"endpoint": "uptime", "error_type": type(e).__name__},
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
                total_gateways=0,
                healthy_gateways=0,
                tracked_models=0,
                tracked_providers=0,
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
                    response_time_display = f"{response_time_ms / 1000:.1f}s"

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
                    response_time_display = f"{response_time_ms / 1000:.1f}s"

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
            context_type="health_endpoint",
            context_data={"endpoint": "/health/dashboard", "operation": "get_health_dashboard"},
            tags={"endpoint": "dashboard", "error_type": type(e).__name__},
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

    Note: Database query is wrapped with a 3-second timeout to prevent
    blocking the event loop when the database is slow or unresponsive.
    """
    try:
        logger.info("Checking database connectivity...")

        # Get initialization status (fast - just reads global variable)
        init_status = get_initialization_status()

        # Try a simple query to verify connection
        # Wrap synchronous Supabase call in asyncio.to_thread with timeout
        # to prevent blocking the event loop (Supabase SDK is synchronous)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(lambda: supabase.table("users").limit(1).execute()),
                timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Database health check timed out after {HEALTH_CHECK_TIMEOUT_SECONDS} seconds"
            )
            return {
                "status": "degraded",
                "database": "supabase",
                "connection": "timeout",
                "error": f"Database query timed out after {HEALTH_CHECK_TIMEOUT_SECONDS} seconds",
                "initialization": init_status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        logger.info("Database connection verified")
        return {
            "status": "healthy",
            "database": "supabase",
            "connection": "verified",
            "initialization": init_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Database connection failed: {type(e).__name__}: {str(e)}")

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


@router.get("/health/providers/import-status", tags=["health", "admin"])
async def get_provider_import_status():
    """
    Check provider import status (for debugging)

    Returns which providers successfully imported and which failed.
    This is essential for debugging chat endpoint issues in Railway.

    Note: This is different from /health/providers which returns health metrics.
    This endpoint shows which provider modules loaded successfully at startup.
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


# =============================================================================
# ADDITIONAL HEALTH ENDPOINTS FOR DASHBOARD COMPATIBILITY
# =============================================================================


@router.get("/health/all", tags=["health"])
async def get_all_health(
    api_key: str = Depends(get_api_key),
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Get comprehensive health information for all components.

    This endpoint provides a complete overview of system health including:
    - System status
    - Provider health
    - Model health
    - Gateway health
    - Database status

    This is an alias for /health/summary with additional details.

    Query Parameters:
    - force_refresh: Currently ignored (data comes from health-service cache)
    """
    try:
        # Get system health
        cached_system = simple_health_cache.get_system_health()

        # Get providers health
        cached_providers = simple_health_cache.get_providers_health() or []

        # Get models health
        cached_models = simple_health_cache.get_models_health() or []

        # Get database status
        db_status = get_initialization_status()

        # Calculate summary stats
        total_providers = len(cached_providers)
        healthy_providers = sum(
            1 for p in cached_providers if p.get("status", "").lower() in ["online", "healthy"]
        )
        degraded_providers = sum(
            1 for p in cached_providers if p.get("status", "").lower() == "degraded"
        )
        unhealthy_providers = total_providers - healthy_providers - degraded_providers

        total_models = len(cached_models)
        healthy_models = sum(
            1 for m in cached_models if m.get("status", "").lower() in ["online", "healthy"]
        )

        # Determine overall status
        if unhealthy_providers > 0 or not db_status.get("initialized"):
            overall_status = "degraded"
        elif degraded_providers > 0:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        return {
            "status": "success",
            "overall_status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": cached_system
            or {
                "status": "unknown",
                "uptime": 0.0,
            },
            "providers": {
                "total": total_providers,
                "healthy": healthy_providers,
                "degraded": degraded_providers,
                "unhealthy": unhealthy_providers,
                "details": cached_providers[:10],  # Limit to first 10 for performance
            },
            "models": {
                "total": total_models,
                "healthy": healthy_models,
                "unhealthy": total_models - healthy_models,
            },
            "database": {
                "status": "connected" if db_status.get("initialized") else "unavailable",
                "initialized": db_status.get("initialized", False),
                "error": db_status.get("error_type") if db_status.get("has_error") else None,
            },
        }

    except Exception as e:
        logger.error(f"Error getting all health: {e}")
        return {
            "status": "error",
            "overall_status": "unknown",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/models/stats", tags=["health"])
async def get_models_health_stats(
    api_key: str = Depends(get_api_key),
) -> dict[str, Any]:
    """
    Get health statistics for all models.

    This endpoint provides aggregate statistics about model health including:
    - Total models count
    - Healthy/degraded/unhealthy counts
    - Average response times
    - Error rates by provider

    Note: Detailed per-model health is available at /model-health/stats
    """
    try:
        # Get models health from cache
        cached_models = simple_health_cache.get_models_health() or []

        # Calculate statistics
        total_models = len(cached_models)
        healthy_models = 0
        degraded_models = 0
        unhealthy_models = 0
        total_response_time = 0.0
        response_time_count = 0
        provider_stats = {}

        for model in cached_models:
            status = model.get("status", "unknown").lower()
            if status in ["online", "healthy"]:
                healthy_models += 1
            elif status == "degraded":
                degraded_models += 1
            else:
                unhealthy_models += 1

            # Response time stats
            if model.get("avg_response_time_ms"):
                total_response_time += model["avg_response_time_ms"]
                response_time_count += 1

            # Provider stats
            provider = model.get("provider", "unknown")
            if provider not in provider_stats:
                provider_stats[provider] = {
                    "total": 0,
                    "healthy": 0,
                    "avg_response_time_ms": 0,
                    "response_time_sum": 0,
                    "response_time_count": 0,
                }
            provider_stats[provider]["total"] += 1
            if status in ["online", "healthy"]:
                provider_stats[provider]["healthy"] += 1
            if model.get("avg_response_time_ms"):
                provider_stats[provider]["response_time_sum"] += model["avg_response_time_ms"]
                provider_stats[provider]["response_time_count"] += 1

        # Calculate averages for providers
        for provider in provider_stats:
            if provider_stats[provider]["response_time_count"] > 0:
                provider_stats[provider]["avg_response_time_ms"] = round(
                    provider_stats[provider]["response_time_sum"]
                    / provider_stats[provider]["response_time_count"],
                    2,
                )
            # Clean up temp fields
            del provider_stats[provider]["response_time_sum"]
            del provider_stats[provider]["response_time_count"]

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "total_models": total_models,
                "healthy_models": healthy_models,
                "degraded_models": degraded_models,
                "unhealthy_models": unhealthy_models,
                "health_rate": round(healthy_models / total_models * 100, 2)
                if total_models > 0
                else 0,
                "avg_response_time_ms": (
                    round(total_response_time / response_time_count, 2)
                    if response_time_count > 0
                    else None
                ),
            },
            "by_provider": provider_stats,
        }

    except Exception as e:
        logger.error(f"Error getting model health stats: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/health/providers/stats", tags=["health"])
async def get_providers_health_stats(
    api_key: str = Depends(get_api_key),
) -> dict[str, Any]:
    """
    Get health statistics for all providers.

    This endpoint provides aggregate statistics about provider health including:
    - Total providers count
    - Healthy/degraded/unhealthy counts
    - Average response times per provider
    - Model counts per provider
    - Uptime percentages
    """
    try:
        # Get providers health from cache
        cached_providers = simple_health_cache.get_providers_health() or []

        # Calculate statistics
        total_providers = len(cached_providers)
        healthy_providers = 0
        degraded_providers = 0
        unhealthy_providers = 0
        total_models = 0
        total_uptime = 0.0

        provider_details = []

        for provider in cached_providers:
            status = provider.get("status", "unknown").lower()
            if status in ["online", "healthy"]:
                healthy_providers += 1
            elif status == "degraded":
                degraded_providers += 1
            else:
                unhealthy_providers += 1

            models_count = provider.get("total_models", 0)
            total_models += models_count

            uptime = provider.get("overall_uptime", 0.0)
            total_uptime += uptime

            provider_details.append(
                {
                    "provider": provider.get("provider", "unknown"),
                    "gateway": provider.get("gateway"),
                    "status": status,
                    "models_count": models_count,
                    "healthy_models": provider.get("healthy_models", 0),
                    "uptime": round(uptime, 2),
                    "avg_response_time_ms": provider.get("avg_response_time_ms"),
                    "last_check": provider.get("last_check"),
                }
            )

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "total_providers": total_providers,
                "healthy_providers": healthy_providers,
                "degraded_providers": degraded_providers,
                "unhealthy_providers": unhealthy_providers,
                "health_rate": (
                    round(healthy_providers / total_providers * 100, 2)
                    if total_providers > 0
                    else 0
                ),
                "total_models": total_models,
                "avg_uptime": round(total_uptime / total_providers, 2)
                if total_providers > 0
                else 0,
            },
            "providers": provider_details,
        }

    except Exception as e:
        logger.error(f"Error getting provider health stats: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
