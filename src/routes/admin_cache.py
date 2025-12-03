"""
Admin Cache Management Endpoints

Provides administrative endpoints for managing health cache, including
invalidation, statistics, and monitoring.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.security.deps import get_api_key
from src.services.cache_invalidation_service import cache_invalidation_service
from src.services.health_cache_service import health_cache_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/cache", tags=["admin", "cache"])


@router.get("/health/status", response_model=dict[str, Any], tags=["admin", "cache"])
async def get_cache_status(api_key: str = Depends(get_api_key)):
    """
    Get status of all health cache entries

    Returns which cache entries are currently populated.
    """
    try:
        status = cache_invalidation_service.get_cache_status()
        return status
    except Exception as e:
        logger.error(f"Failed to get cache status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cache status") from e


@router.get("/health/stats", response_model=dict[str, Any], tags=["admin", "cache"])
async def get_cache_statistics(api_key: str = Depends(get_api_key)):
    """
    Get detailed cache statistics including compression ratios

    Returns compression statistics for all cached health data.
    """
    try:
        stats = cache_invalidation_service.get_cache_statistics()
        return stats
    except Exception as e:
        logger.error(f"Failed to get cache statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cache statistics") from e


@router.delete("/health/system", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_system_health_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate system health cache

    Clears the cached system health data and dependent caches.
    """
    try:
        success = cache_invalidation_service.invalidate_system_health()
        # Also invalidate dependent caches
        cache_invalidation_service.invalidate_dependent_caches("health:system")

        return {
            "success": success,
            "message": "System health cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate system health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/providers", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_providers_health_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate providers health cache

    Clears the cached providers health data and dependent caches.
    """
    try:
        success = cache_invalidation_service.invalidate_providers_health()
        # Also invalidate dependent caches
        cache_invalidation_service.invalidate_dependent_caches("health:providers")

        return {
            "success": success,
            "message": "Providers health cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate providers health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/models", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_models_health_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate models health cache

    Clears the cached models health data and dependent caches.
    """
    try:
        success = cache_invalidation_service.invalidate_models_health()
        # Also invalidate dependent caches
        cache_invalidation_service.invalidate_dependent_caches("health:models")

        return {
            "success": success,
            "message": "Models health cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate models health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/summary", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_summary_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate health summary cache

    Clears the cached health summary data.
    """
    try:
        success = cache_invalidation_service.invalidate_summary()

        return {
            "success": success,
            "message": "Health summary cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate summary cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/dashboard", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_dashboard_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate health dashboard cache

    Clears the cached health dashboard data.
    """
    try:
        success = cache_invalidation_service.invalidate_dashboard()

        return {
            "success": success,
            "message": "Health dashboard cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate dashboard cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/gateway", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_gateway_health_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate gateway health cache

    Clears the cached gateway health data.
    """
    try:
        success = cache_invalidation_service.invalidate_gateway_health()

        return {
            "success": success,
            "message": "Gateway health cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate gateway health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.delete("/health/all", response_model=dict[str, Any], tags=["admin", "cache"])
async def invalidate_all_health_cache(api_key: str = Depends(get_api_key)):
    """
    Invalidate all health-related cache

    Clears all cached health data including system, providers, models,
    summary, dashboard, and gateway health.
    """
    try:
        success = cache_invalidation_service.invalidate_all_health_cache()

        return {
            "success": success,
            "message": "All health cache invalidated",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to invalidate all health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to invalidate cache") from e


@router.post("/health/refresh", response_model=dict[str, Any], tags=["admin", "cache"])
async def refresh_health_cache(api_key: str = Depends(get_api_key)):
    """
    Refresh all health cache

    Invalidates all health cache and triggers fresh data fetch.
    """
    try:
        # Invalidate all cache
        cache_invalidation_service.invalidate_all_health_cache()

        return {
            "success": True,
            "message": "Health cache refresh initiated",
            "note": "Cache will be repopulated on next request",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to refresh health cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh cache") from e


@router.get("/health/compression-stats", response_model=dict[str, Any], tags=["admin", "cache"])
async def get_compression_statistics(api_key: str = Depends(get_api_key)):
    """
    Get detailed compression statistics

    Returns compression ratios and sizes for all cached health data.
    """
    try:
        stats = health_cache_service.get_all_cache_stats()

        # Calculate totals
        total_original = 0
        total_compressed = 0
        entries_count = 0

        for key, stat in stats.items():
            if stat.get("compressed"):
                total_original += stat.get("original_size", 0)
                total_compressed += stat.get("compressed_size", 0)
                entries_count += 1

        overall_ratio = (
            total_compressed / total_original if total_original > 0 else 0
        )

        return {
            "cache_stats": stats,
            "summary": {
                "total_entries": entries_count,
                "total_original_size": total_original,
                "total_compressed_size": total_compressed,
                "overall_compression_ratio": overall_ratio,
                "bandwidth_saved_percent": (1 - overall_ratio) * 100 if overall_ratio > 0 else 0,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get compression statistics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics") from e


@router.get("/redis/info", response_model=dict[str, Any], tags=["admin", "cache"])
async def get_redis_info(api_key: str = Depends(get_api_key)):
    """
    Get Redis server information

    Returns Redis server stats including memory usage and connected clients.
    """
    try:
        from src.redis_config import get_redis_info

        info = get_redis_info()
        return {
            "redis_info": info,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get Redis info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get Redis info") from e


@router.post("/redis/clear", response_model=dict[str, Any], tags=["admin", "cache"])
async def clear_redis_cache(api_key: str = Depends(get_api_key)):
    """
    Clear entire Redis cache

    WARNING: This clears ALL Redis data, not just health cache.
    Use with caution in production.
    """
    try:
        from src.redis_config import clear_redis_cache as clear_redis

        success = clear_redis()

        return {
            "success": success,
            "message": "Redis cache cleared",
            "warning": "All Redis data has been cleared",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to clear Redis cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear Redis cache") from e
