import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.db.ranking import get_all_latest_apps, get_all_latest_models
from src.utils.sentry_context import capture_database_error

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory cache for ranking data with TTL
# This provides a fallback when Supabase is unavailable
_models_cache: dict[str, Any] = {
    "data": None,
    "timestamp": 0,
    "ttl": 300,  # 5 minutes TTL
}
_apps_cache: dict[str, Any] = {
    "data": None,
    "timestamp": 0,
    "ttl": 300,  # 5 minutes TTL
}


def _is_cache_valid(cache: dict[str, Any]) -> bool:
    """Check if cache is still valid based on TTL."""
    if cache["data"] is None:
        return False
    return (time.time() - cache["timestamp"]) < cache["ttl"]


def _update_cache(cache: dict[str, Any], data: list[dict]) -> None:
    """Update cache with new data."""
    cache["data"] = data
    cache["timestamp"] = time.time()


def _get_cached_data(cache: dict[str, Any]) -> list[dict] | None:
    """Get cached data if available (even if stale, for fallback)."""
    return cache["data"]


@router.get("/ranking/models", tags=["ranking"])
async def get_ranking_models(
    limit: int | None = Query(None, description="Limit number of results"),
    offset: int | None = Query(0, description="Offset for pagination"),
):
    """
    Get all models from latest_models table for ranking page with logo URLs.

    This endpoint implements graceful degradation:
    - Returns fresh data from database when available
    - Falls back to cached data when database is unavailable
    - Returns empty list with success=True if no data is available

    This prevents frontend "Failed to fetch" errors caused by backend 500 responses.
    """
    global _models_cache

    try:
        # Try to get fresh data from database
        models = get_all_latest_models(limit=limit, offset=offset)
        logger.info(f"Retrieved {len(models)} models from latest_models table")

        # Update cache with fresh data (only cache full results, not paginated)
        if limit is None and (offset is None or offset == 0):
            _update_cache(_models_cache, models)
            logger.debug("Updated ranking models cache with fresh data")

        return {
            "success": True,
            "data": models,
            "count": len(models),
            "limit": limit,
            "offset": offset or 0,
            "has_logo_urls": any(model.get("logo_url") for model in models),
            "source": "database",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except RuntimeError as e:
        # Supabase/database unavailable - use cached data or return empty
        logger.warning(f"Database unavailable for ranking models: {e}")

        # Capture error to Sentry with context
        capture_database_error(
            exception=e,
            operation="select",
            table="latest_models",
            details={
                "endpoint": "/ranking/models",
                "limit": limit,
                "offset": offset,
                "error_type": "database_unavailable",
            },
        )

        # Try to use cached data as fallback
        cached_data = _get_cached_data(_models_cache)
        if cached_data is not None:
            cache_age_seconds = int(time.time() - _models_cache["timestamp"])
            logger.info(
                f"Using cached ranking models ({len(cached_data)} models, "
                f"age: {cache_age_seconds}s)"
            )

            # Apply pagination to cached data if requested
            if offset or limit:
                start = offset or 0
                end = start + (limit or len(cached_data))
                paginated_data = cached_data[start:end]
            else:
                paginated_data = cached_data

            return {
                "success": True,
                "data": paginated_data,
                "count": len(paginated_data),
                "limit": limit,
                "offset": offset or 0,
                "has_logo_urls": any(model.get("logo_url") for model in paginated_data),
                "source": "cache",
                "cache_age_seconds": cache_age_seconds,
                "database_available": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # No cache available - return empty list (graceful degradation)
        logger.warning("No cached ranking models available, returning empty list")
        return {
            "success": True,
            "data": [],
            "count": 0,
            "limit": limit,
            "offset": offset or 0,
            "has_logo_urls": False,
            "source": "none",
            "database_available": False,
            "message": "Model data temporarily unavailable. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        # Unexpected error - log, capture to Sentry, but still return gracefully
        logger.error(f"Unexpected error fetching ranking models: {e}", exc_info=True)

        capture_database_error(
            exception=e,
            operation="select",
            table="latest_models",
            details={
                "endpoint": "/ranking/models",
                "limit": limit,
                "offset": offset,
                "error_type": type(e).__name__,
            },
        )

        # Try cache fallback for any error
        cached_data = _get_cached_data(_models_cache)
        if cached_data is not None:
            cache_age_seconds = int(time.time() - _models_cache["timestamp"])
            logger.info(f"Using cached data after error ({len(cached_data)} models)")

            if offset or limit:
                start = offset or 0
                end = start + (limit or len(cached_data))
                paginated_data = cached_data[start:end]
            else:
                paginated_data = cached_data

            return {
                "success": True,
                "data": paginated_data,
                "count": len(paginated_data),
                "limit": limit,
                "offset": offset or 0,
                "has_logo_urls": any(model.get("logo_url") for model in paginated_data),
                "source": "cache",
                "cache_age_seconds": cache_age_seconds,
                "error_occurred": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Return empty list as last resort
        return {
            "success": True,
            "data": [],
            "count": 0,
            "limit": limit,
            "offset": offset or 0,
            "has_logo_urls": False,
            "source": "none",
            "error_occurred": True,
            "message": "Model data temporarily unavailable. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/ranking/apps", tags=["ranking"])
async def get_ranking_apps():
    """
    Get all apps from latest_apps table for ranking page.

    This endpoint implements graceful degradation similar to /ranking/models.
    """
    global _apps_cache

    try:
        # Get apps from database
        apps = get_all_latest_apps()
        logger.info(f"Retrieved {len(apps)} apps")

        # Update cache with fresh data
        _update_cache(_apps_cache, apps)

        return {
            "success": True,
            "data": apps,
            "count": len(apps),
            "source": "database",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except RuntimeError as e:
        # Database unavailable - use cached data or return empty
        logger.warning(f"Database unavailable for ranking apps: {e}")

        capture_database_error(
            exception=e,
            operation="select",
            table="latest_apps",
            details={
                "endpoint": "/ranking/apps",
                "error_type": "database_unavailable",
            },
        )

        cached_data = _get_cached_data(_apps_cache)
        if cached_data is not None:
            cache_age_seconds = int(time.time() - _apps_cache["timestamp"])
            logger.info(f"Using cached ranking apps ({len(cached_data)} apps)")

            return {
                "success": True,
                "data": cached_data,
                "count": len(cached_data),
                "source": "cache",
                "cache_age_seconds": cache_age_seconds,
                "database_available": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "success": True,
            "data": [],
            "count": 0,
            "source": "none",
            "database_available": False,
            "message": "App data temporarily unavailable. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Unexpected error fetching ranking apps: {e}", exc_info=True)

        capture_database_error(
            exception=e,
            operation="select",
            table="latest_apps",
            details={
                "endpoint": "/ranking/apps",
                "error_type": type(e).__name__,
            },
        )

        cached_data = _get_cached_data(_apps_cache)
        if cached_data is not None:
            cache_age_seconds = int(time.time() - _apps_cache["timestamp"])
            return {
                "success": True,
                "data": cached_data,
                "count": len(cached_data),
                "source": "cache",
                "cache_age_seconds": cache_age_seconds,
                "error_occurred": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "success": True,
            "data": [],
            "count": 0,
            "source": "none",
            "error_occurred": True,
            "message": "App data temporarily unavailable. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
