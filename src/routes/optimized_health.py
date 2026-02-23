"""
Optimized system health endpoints for large scale data.

This module provides fast endpoints that can handle:
- 9,000+ models
- 37 providers  # noqa: W291
- 28 gateways

Key optimizations:
1. Use cached data from health-service (Redis)
2. Only do live tests when explicitly requested
3. Parallel data fetching
4. Efficient data structures
"""

from datetime import datetime, UTC
from typing import Any

from ..cache import get_models_cache
from ..routes.system import _run_gateway_check
from ..services.simple_health_cache import simple_health_cache
from ..utils.error_tracking import capture_error
from ..utils.logger import logger


def _normalize_timestamp(timestamp):
    """Normalize timestamp to datetime object."""
    if timestamp is None:
        return None
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            return None
    return None


async def get_optimized_gateway_data(
    include_live_tests: bool = False,
    auto_fix: bool = False
) -> dict[str, Any]:
    """
    Get optimized gateway data that can handle large scale.
    
    Strategy:
    1. Use cached data from health-service for basic info
    2. Only do live tests if explicitly requested
    3. Enrich with model counts from cache
    4. Parallel processing where possible
    """
    
    start_time = datetime.now()
    
    try:
        # Step 1: Get basic gateway health from cache (fast)
        cached_gateways = simple_health_cache.get_gateways_health() or {}
        
        # Step 2: Get system health for summary
        system_health = simple_health_cache.get_system_health() or {}  # noqa: F841
        
        # Step 3: Build gateway data structure
        gateways_data = {}
        total_gateways = 0
        healthy_count = 0
        unhealthy_count = 0
        unconfigured_count = 0
        
        # Process cached gateway data
        for gateway_name, gateway_info in cached_gateways.items():
            total_gateways += 1
            
            # Determine status
            status = gateway_info.get('status', 'unknown').lower()
            if status in ['healthy', 'online']:
                healthy_count += 1
                final_status = 'healthy'
            elif status in ['unhealthy', 'offline', 'degraded']:
                unhealthy_count += 1
                final_status = 'unhealthy'
            else:
                unconfigured_count += 1
                final_status = 'unconfigured'
            
            # Get model count from cache (fast)
            models_cache = get_models_cache(gateway_name)
            models_count = 0
            models_metadata = {}
            
            if models_cache and models_cache.get("data"):
                models = models_cache.get("data", [])
                models_count = len(models)
                models_metadata = {
                    "count": models_count,
                    "last_updated": _normalize_timestamp(models_cache.get("timestamp")).isoformat()
                    if _normalize_timestamp(models_cache.get("timestamp")) else None
                }
            else:
                models_metadata = {
                    "count": 0,
                    "last_updated": None
                }
            
            # Build gateway data
            gateways_data[gateway_name] = {
                "name": gateway_info.get('name', gateway_name),
                "final_status": final_status,
                "configured": final_status != 'unconfigured',
                "models": models_cache.get("data", []) if models_cache else [],
                "models_metadata": models_metadata,
                "latency_ms": gateway_info.get('latency_ms', 0),
                "available": gateway_info.get('available', final_status == 'healthy'),
                "last_check": gateway_info.get('last_check'),
                "error": gateway_info.get('error'),
                # Include minimal endpoint_test data
                "endpoint_test": {
                    "available": gateway_info.get('available', final_status == 'healthy'),
                    "latency_ms": gateway_info.get('latency_ms', 0),
                    "last_check": gateway_info.get('last_check'),
                    "error": gateway_info.get('error')
                }
            }
        
        # Step 4: Only do live tests if requested (expensive)
        if include_live_tests:
            logger.info("Performing live gateway tests...")
            try:
                live_results, _ = await _run_gateway_check(auto_fix=auto_fix)
                live_gateways = live_results.get("gateways", {})
                
                # Merge live data with cached data
                for gateway_name, live_data in live_gateways.items():
                    if gateway_name in gateways_data:
                        # Update with live test results
                        endpoint_test = live_data.get('endpoint_test', {})
                        gateways_data[gateway_name].update({
                            "endpoint_test": endpoint_test,
                            "latency_ms": endpoint_test.get('latency_ms', 0),
                            "available": endpoint_test.get('available'),
                            "last_check": endpoint_test.get('last_check'),
                            "error": endpoint_test.get('error')
                        })
            except Exception as e:
                logger.warning(f"Live tests failed, using cached data: {e}")
                capture_error(
                    e,
                    context_type='gateway_live_tests',
                    context_data={'gateways_count': total_gateways},
                    tags={'endpoint': 'gateway_health', 'error_type': 'live_tests_failed'}
                )
        
        # Step 5: Build final response
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        response = {
            "success": True,
            "data": gateways_data,
            "summary": {
                "total_gateways": total_gateways,
                "healthy": healthy_count,
                "unhealthy": unhealthy_count,
                "unconfigured": unconfigured_count,
                "overall_health_percentage": round((healthy_count / total_gateways) * 100, 1) if total_gateways > 0 else 0,
            },
            "timestamp": end_time.isoformat(),
            "metadata": {
                "processing_time_seconds": processing_time,
                "live_tests_performed": include_live_tests,
                "data_source": "cached" if not include_live_tests else "hybrid"
            }
        }
        
        logger.info(f"Gateway data processed in {processing_time:.2f}s for {total_gateways} gateways")
        return response
        
    except Exception as e:
        logger.error(f"Failed to get optimized gateway data: {e}", exc_info=True)
        capture_error(
            e,
            context_type='gateway_data_optimization',
            context_data={'include_live_tests': include_live_tests},
            tags={'endpoint': 'gateway_health', 'error_type': 'data_processing_failed'}
        )
        
        # Return fallback data
        return {
            "success": False,
            "data": {},
            "summary": {
                "total_gateways": 0,
                "healthy": 0,
                "unhealthy": 0,
                "unconfigured": 0,
                "overall_health_percentage": 0,
            },
            "timestamp": datetime.now(UTC).isoformat(),
            "error": str(e),
            "metadata": {
                "fallback_mode": True
            }
        }


async def get_optimized_providers_data() -> dict[str, Any]:
    """Get optimized providers data using cache."""
    try:
        # Get providers from cache (fast)
        cached_providers = simple_health_cache.get_providers_health() or []
        system_health = simple_health_cache.get_system_health() or {}
        
        total_providers = system_health.get("total_providers", 0)
        tracked_providers = len(cached_providers)
        
        # Process provider data
        providers_data = []
        healthy_count = 0
        unhealthy_count = 0
        
        for provider in cached_providers:
            status = provider.get('status', 'unknown').lower()
            if status in ['healthy', 'online']:
                healthy_count += 1
            elif status in ['unhealthy', 'offline', 'degraded']:
                unhealthy_count += 1
            
            providers_data.append({
                "provider": provider.get('provider', 'Unknown'),
                "gateway": provider.get('gateway', 'Unknown'),
                "status": provider.get('status', 'Unknown'),
                "status_color": provider.get('status_color', 'gray'),
                "models_count": provider.get('total_models', 0),
                "healthy_count": provider.get('healthy_models', 0),
                "uptime": provider.get('overall_uptime', '0%'),
                "avg_response_time": provider.get('avg_response_time_ms', '0ms'),
                "last_checked": provider.get('last_checked', datetime.now(UTC).isoformat()),
            })
        
        return {
            "success": True,
            "data": providers_data,
            "providers": providers_data,
            "total_providers": total_providers,
            "tracked_providers": tracked_providers,
            "summary": {
                "total_providers": total_providers,
                "healthy": healthy_count,
                "unhealthy": unhealthy_count,
                "overall_health_percentage": round((healthy_count / tracked_providers) * 100, 1) if tracked_providers > 0 else 0,
            },
            "timestamp": datetime.now(UTC).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to get optimized providers data: {e}", exc_info=True)
        capture_error(
            e,
            context_type='providers_data_optimization',
            tags={'endpoint': 'providers_health', 'error_type': 'data_processing_failed'}
        )
        
        return {
            "success": False,
            "data": [],
            "providers": [],
            "total_providers": 0,
            "tracked_providers": 0,
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }


async def get_optimized_models_data(
    gateway: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 1000,
    offset: int = 0
) -> dict[str, Any]:
    """Get optimized models data using cache with pagination."""
    try:
        # Get models from cache (fast)
        cached_models = simple_health_cache.get_models_health() or []
        system_health = simple_health_cache.get_system_health() or {}
        
        total_models = system_health.get("total_models", 0)
        tracked_models = len(cached_models)
        
        # Apply filters
        filtered_models = cached_models
        if gateway:
            filtered_models = [m for m in filtered_models if m.get("gateway") == gateway]
        if provider:
            filtered_models = [m for m in filtered_models if m.get("provider") == provider]
        if status:
            filtered_models = [m for m in filtered_models if m.get("status") == status]
        
        # Apply pagination
        paginated_models = filtered_models[offset:offset + limit]
        
        # Process model data
        models_data = []
        for model in paginated_models:
            models_data.append({
                "model_id": model.get("model_id", model.get("name", "Unknown")),
                "name": model.get("name", "Unknown"),
                "provider": model.get("provider", "Unknown"),
                "gateway": model.get("gateway", "Unknown"),
                "status": model.get("status", "Unknown"),
                "status_color": model.get("status_color", "gray"),
                "response_time_ms": model.get("response_time_ms", 0),
                "response_time": f"{model.get('response_time_ms', 0)}ms",
                "uptime_percentage": model.get("uptime_percentage", 0),
                "uptime": f"{model.get('uptime_percentage', 0)}%",
                "last_checked": model.get("last_checked", datetime.now(UTC).isoformat()),
                "success_rate": model.get("success_rate", 0),
                "error_count": model.get("error_count", 0),
                "total_requests": model.get("total_requests", 0),
            })
        
        return {
            "success": True,
            "data": models_data,
            "models": models_data,
            "total_models": total_models,
            "tracked_models": tracked_models,
            "filtered_models": len(filtered_models),
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < len(filtered_models),
                "total_filtered": len(filtered_models)
            },
            "timestamp": datetime.now(UTC).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to get optimized models data: {e}", exc_info=True)
        capture_error(
            e,
            context_type='models_data_optimization',
            context_data={'limit': limit, 'offset': offset},
            tags={'endpoint': 'models_health', 'error_type': 'data_processing_failed'}
        )
        
        return {
            "success": False,
            "data": [],
            "models": [],
            "total_models": 0,
            "tracked_models": 0,
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }
