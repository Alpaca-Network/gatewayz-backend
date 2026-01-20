"""
Pricing Sync API Endpoints

Endpoints to trigger and monitor pricing synchronization
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from src.services.pricing_sync_background import (
    get_pricing_sync_service,
    sync_pricing_on_model_update,
)

router = APIRouter(prefix="/pricing-sync", tags=["Pricing Sync"])


class SyncRequest(BaseModel):
    """Request to sync pricing"""

    model_ids: list[int] | None = None
    provider: str | None = None
    sync_all: bool = False
    sync_stale: bool = False
    stale_hours: int = 24


class SyncResponse(BaseModel):
    """Response from pricing sync"""

    success: bool
    stats: dict
    message: str


@router.post("/trigger", response_model=SyncResponse)
async def trigger_pricing_sync(
    request: SyncRequest, background_tasks: BackgroundTasks
):
    """
    Trigger pricing synchronization

    Options:
    - Sync specific model IDs
    - Sync all models from a provider
    - Sync all models
    - Sync stale pricing (older than N hours)

    Examples:
        POST /pricing-sync/trigger
        {
            "model_ids": [1, 2, 3]  # Sync specific models
        }

        {
            "provider": "openrouter"  # Sync all OpenRouter models
        }

        {
            "sync_all": true  # Sync all models (runs in background)
        }

        {
            "sync_stale": true,  # Sync stale pricing
            "stale_hours": 24
        }
    """
    service = get_pricing_sync_service()

    try:
        # Sync specific models
        if request.model_ids:
            stats = service.sync_pricing_for_models(request.model_ids)
            return SyncResponse(
                success=True,
                stats=stats,
                message=f"Synced pricing for {len(request.model_ids)} models",
            )

        # Sync provider
        elif request.provider:
            stats = service.sync_provider_models(request.provider)
            return SyncResponse(
                success=True,
                stats=stats,
                message=f"Synced pricing for provider: {request.provider}",
            )

        # Sync stale
        elif request.sync_stale:
            stats = service.sync_stale_pricing(hours=request.stale_hours)
            return SyncResponse(
                success=True,
                stats=stats,
                message=f"Synced stale pricing (older than {request.stale_hours}h)",
            )

        # Sync all (background)
        elif request.sync_all:
            # Run in background for large syncs
            background_tasks.add_task(service.sync_all_models)
            return SyncResponse(
                success=True,
                stats={"status": "started"},
                message="Full pricing sync started in background",
            )

        else:
            raise HTTPException(
                status_code=400,
                detail="Must specify model_ids, provider, sync_all, or sync_stale",
            )

    except Exception as e:
        return SyncResponse(success=False, stats={}, message=f"Error: {str(e)}")


@router.get("/status", response_model=dict)
async def get_pricing_sync_status():
    """
    Get pricing sync statistics

    Returns info about:
    - Total models with pricing
    - Average price levels
    - Last update times
    """
    from src.services.model_pricing_service import get_pricing_stats

    try:
        stats = get_pricing_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear-cache")
async def clear_pricing_cache_endpoint():
    """Clear the pricing cache"""
    from src.services.model_pricing_service import clear_pricing_cache

    try:
        clear_pricing_cache()
        return {"success": True, "message": "Pricing cache cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
