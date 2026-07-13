import logging

from fastapi import APIRouter, HTTPException, Query

from src.db.ranking import get_all_latest_apps, get_ranking_models_from_usage

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ranking/models", tags=["ranking"])
async def get_ranking_models(
    limit: int | None = Query(None, description="Limit number of results"),
    offset: int | None = Query(0, description="Offset for pagination"),
):
    """Get ranking models sourced from real Gatewayz usage, falling back to the
    scraped snapshot per time-period bucket while traffic is still low."""
    try:
        # Get models with pagination support
        models = get_ranking_models_from_usage(limit=limit, offset=offset)
        logger.info(f"Retrieved {len(models)} ranking models")

        return {
            "success": True,
            "data": models,
            "count": len(models),
            "limit": limit,
            "offset": offset or 0,
            "has_logo_urls": any(model.get("logo_url") for model in models),
        }

    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch models: {str(e)}") from e


@router.get("/ranking/apps", tags=["ranking"])
async def get_ranking_apps():
    """Get all apps from latest_apps table for ranking page"""
    try:
        # Get apps based on filters
        apps = get_all_latest_apps()

        logger.info(f"Retrieved {len(apps)} apps")

        return {
            "success": True,
            "data": apps,
            "count": len(apps),
        }

    except Exception as e:
        logger.error(f"Failed to fetch apps: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch apps: {str(e)}") from e
