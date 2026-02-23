"""
Optimized health endpoints for large scale system health data.

These endpoints are designed to handle:
- 9,000+ models
- 37 providers
- 28 gateways

Key features:
- Fast cached data by default
- Optional live tests for gateways
- Efficient pagination for models
- Parallel processing
- Better error handling
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import get_api_key
from .optimized_health import (
    get_optimized_gateway_data,
    get_optimized_models_data,
    get_optimized_providers_data,
)
from .utils.error_tracking import capture_error
from .utils.logger import logger

router = APIRouter()


@router.get("/health/gateways/optimized", tags=["health"])
async def get_optimized_gateways_health(
    include_live_tests: bool = Query(
        False,
        description="Include live latency tests (slower but more accurate). Default: False (cached data only)"
    ),
    auto_fix: bool = Query(
        False,
        description="Attempt to auto-fix failing gateways when doing live tests."
    ),
    api_key: str = Depends(get_api_key)
):
    """
    Get optimized gateway health data for large scale deployment.
    
    **Performance Strategy:**
    - Default: Uses cached data (sub-second response)
    - With live tests: Does comprehensive checks (slower, ~10-30 seconds)
    
    **Returns:**
    - All 28 gateways with health status
    - Model counts and metadata
    - Latency data (cached or live)
    - Summary statistics
    
    **Use Cases:**
    - Dashboard loading: `include_live_tests=false` (fast)
    - Detailed diagnostics: `include_live_tests=true` (accurate)
    """
    try:
        logger.info(f"Gateway health request - live_tests: {include_live_tests}, auto_fix: {auto_fix}")
        
        result = await get_optimized_gateway_data(
            include_live_tests=include_live_tests,
            auto_fix=auto_fix
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=503,
                detail="Gateway health service temporarily unavailable"
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Optimized gateway health endpoint failed: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={
                'endpoint': '/health/gateways/optimized',
                'include_live_tests': include_live_tests,
                'auto_fix': auto_fix
            },
            tags={'endpoint': 'gateways_health_optimized', 'error_type': type(e).__name__}
        )
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching gateway health"
        )


@router.get("/health/providers/optimized", tags=["health"])
async def get_optimized_providers_health(
    api_key: str = Depends(get_api_key)
):
    """
    Get optimized provider health data for large scale deployment.
    
    **Performance:**
    - Uses cached data from health-service (sub-second response)
    - Handles 37+ providers efficiently
    
    **Returns:**
    - All providers with health status
    - Model counts and health metrics
    - Summary statistics
    """
    try:
        logger.info("Provider health request - optimized endpoint")
        
        result = await get_optimized_providers_data()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=503,
                detail="Provider health service temporarily unavailable"
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Optimized provider health endpoint failed: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={'endpoint': '/health/providers/optimized'},
            tags={'endpoint': 'providers_health_optimized', 'error_type': type(e).__name__}
        )
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching provider health"
        )


@router.get("/health/models/optimized", tags=["health"])
async def get_optimized_models_health(
    gateway: str | None = Query(None, description="Filter by specific gateway"),
    provider: str | None = Query(None, description="Filter by specific provider"),
    status: str | None = Query(None, description="Filter by health status"),
    limit: int = Query(1000, ge=1, le=5000, description="Number of models to return"),
    offset: int = Query(0, ge=0, description="Number of models to skip"),
    api_key: str = Depends(get_api_key)
):
    """
    Get optimized model health data for large scale deployment.
    
    **Performance:**
    - Uses cached data from health-service (sub-second response)
    - Efficient pagination for 9,000+ models
    - Fast filtering capabilities
    
    **Parameters:**
    - gateway: Filter by gateway name
    - provider: Filter by provider name  # noqa: W291
    - status: Filter by health status
    - limit: Max 5000 models per request
    - offset: For pagination
    
    **Returns:**
    - Paginated model health data
    - Filtering and pagination metadata
    - Summary statistics
    """
    try:
        logger.info(f"Model health request - gateway: {gateway}, provider: {provider}, status: {status}, limit: {limit}, offset: {offset}")
        
        result = await get_optimized_models_data(
            gateway=gateway,
            provider=provider,
            status=status,
            limit=limit,
            offset=offset
        )
        
        if not result.get("success"):
            raise HTTPException(
                status_code=503,
                detail="Model health service temporarily unavailable"
            )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Optimized model health endpoint failed: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={
                'endpoint': '/health/models/optimized',
                'gateway': gateway,
                'provider': provider,
                'status': status,
                'limit': limit,
                'offset': offset
            },
            tags={'endpoint': 'models_health_optimized', 'error_type': type(e).__name__}
        )
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching model health"
        )


@router.get("/health/dashboard/optimized", tags=["health"])
async def get_optimized_dashboard_data(
    include_live_gateway_tests: bool = Query(
        False,
        description="Include live gateway latency tests (slower but more accurate)"
    ),
    models_limit: int = Query(100, ge=1, le=1000, description="Number of models to include in dashboard"),
    api_key: str = Depends(get_api_key)
):
    """
    Get optimized dashboard data combining all health metrics.
    
    **Performance Strategy:**
    - Parallel fetching of all data types
    - Cached data by default for maximum speed
    - Optional live tests for accuracy
    
    **Use Cases:**
    - Dashboard initial load: Fast cached data
    - Detailed health check: Include live tests
    
    **Returns:**
    - Combined system health overview
    - Gateways, providers, and models data
    - Performance metrics and timing
    """
    try:
        logger.info(f"Dashboard request - live_tests: {include_live_gateway_tests}, models_limit: {models_limit}")
        start_time = datetime.now()
        
        # Parallel fetching of all data types
        tasks = [
            get_optimized_gateway_data(include_live_tests=include_live_gateway_tests),
            get_optimized_providers_data(),
            get_optimized_models_data(limit=models_limit)
        ]
        
        gateways_result, providers_result, models_result = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions from individual tasks
        if isinstance(gateways_result, Exception):
            logger.error(f"Gateway data fetch failed: {gateways_result}")
            gateways_result = {"success": False, "data": {}, "error": str(gateways_result)}
        
        if isinstance(providers_result, Exception):
            logger.error(f"Providers data fetch failed: {providers_result}")
            providers_result = {"success": False, "data": [], "error": str(providers_result)}
        
        if isinstance(models_result, Exception):
            logger.error(f"Models data fetch failed: {models_result}")
            models_result = {"success": False, "data": [], "error": str(models_result)}
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # Build combined dashboard response
        dashboard_data = {
            "success": True,
            "timestamp": end_time.isoformat(),
            "processing_time_seconds": processing_time,
            "data_sources": {
                "gateways": "live_tests" if include_live_gateway_tests else "cached",
                "providers": "cached",
                "models": "cached"
            },
            "gateways": gateways_result.get("data", {}),
            "providers": providers_result.get("data", []),
            "models": models_result.get("data", []),
            "summary": {
                "gateways": gateways_result.get("summary", {}),
                "providers": providers_result.get("summary", {}),
                "models": {
                    "total_models": models_result.get("total_models", 0),
                    "tracked_models": models_result.get("tracked_models", 0),
                    "returned_models": len(models_result.get("data", [])),
                    "filtered_models": models_result.get("filtered_models", 0)
                }
            },
            "metadata": {
                "live_gateway_tests_performed": include_live_gateway_tests,
                "models_limit_applied": models_limit,
                "all_data_sources_healthy": all([
                    gateways_result.get("success", False),
                    providers_result.get("success", False),
                    models_result.get("success", False)
                ])
            }
        }
        
        logger.info(f"Dashboard data assembled in {processing_time:.2f}s")
        return dashboard_data
        
    except Exception as e:
        logger.error(f"Optimized dashboard endpoint failed: {e}", exc_info=True)
        capture_error(
            e,
            context_type='health_endpoint',
            context_data={
                'endpoint': '/health/dashboard/optimized',
                'include_live_gateway_tests': include_live_gateway_tests,
                'models_limit': models_limit
            },
            tags={'endpoint': 'dashboard_optimized', 'error_type': type(e).__name__}
        )
        
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching dashboard data"
        )
