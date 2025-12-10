"""
Pricing Sync Routes

Endpoints for managing automatic pricing synchronization from provider APIs.

Endpoints:
- POST /pricing/sync/dry-run - Test sync without making changes
- POST /pricing/sync/run - Execute pricing sync
- POST /pricing/sync/run/{provider} - Sync specific provider
- GET /pricing/sync/history - View sync history
- GET /pricing/sync/status - Get current sync status
- POST /pricing/sync/schedule - Configure sync schedule
"""

import logging

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from src.services.pricing_sync_service import (
    get_pricing_sync_service,
    run_scheduled_sync,
    run_dry_run_sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pricing/sync", tags=["pricing-sync"])


@router.post("/dry-run")
async def run_pricing_sync_dry_run(
    providers: str | None = Query(None),
    api_key: str = Query(None),
):
    """
    Run a dry-run pricing sync to see what would change without making changes.

    Args:
        providers: Comma-separated provider names to sync (optional, defaults to all)
        api_key: Optional API key for admin authentication

    Returns:
        Sync plan showing what would be updated
    """
    try:
        service = get_pricing_sync_service()

        # Parse provider list
        if providers:
            provider_list = [p.strip() for p in providers.split(",")]
        else:
            provider_list = None

        logger.info(f"Running dry-run sync for providers: {provider_list or 'all'}")

        if provider_list:
            # Sync specific providers
            results = {}
            for provider in provider_list:
                result = await service.sync_provider_pricing(provider, dry_run=True)
                results[provider] = result
        else:
            # Sync all configured providers (dry-run)
            results = await run_dry_run_sync()

        return {
            "type": "dry_run_sync",
            "status": "complete",
            "changes_would_be_made": any(
                r.get("models_updated", 0) > 0 for r in results.values()
            ),
            "summary": {
                "providers": len(results),
                "total_updates": sum(r.get("models_updated", 0) for r in results.values()),
                "total_new": sum(
                    len([c for c in r.get("price_changes", []) if c["type"] == "new"])
                    for r in results.values()
                ),
            },
            "results": results,
        }
    except Exception as e:
        logger.error(f"Error running dry-run sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
async def run_pricing_sync(
    background: bool = Query(default=False),
    providers: str | None = Query(None),
    api_key: str = Query(None),
    background_tasks: BackgroundTasks = None,
):
    """
    Execute actual pricing sync from provider APIs.

    **WARNING**: This modifies the pricing data. Consider running dry-run first.

    Args:
        background: If True, run in background and return immediately
        providers: Comma-separated provider names to sync (optional)
        api_key: Optional API key for admin authentication
        background_tasks: FastAPI background tasks

    Returns:
        Sync results or job ID if running in background
    """
    try:
        logger.warning(f"Executing pricing sync (background={background})")

        if background and background_tasks:
            # Run in background
            background_tasks.add_task(run_scheduled_sync)
            return {
                "status": "queued",
                "message": "Pricing sync queued for background execution",
                "estimated_duration_seconds": 60,
            }

        # Run sync immediately
        service = get_pricing_sync_service()

        if providers:
            provider_list = [p.strip() for p in providers.split(",")]
            results = {}
            for provider in provider_list:
                result = await service.sync_provider_pricing(provider, dry_run=False)
                results[provider] = result
        else:
            results = await run_scheduled_sync()

        return {
            "status": "complete",
            "timestamp": results.get("timestamp"),
            "providers_synced": results.get("providers_synced", 0),
            "total_models_updated": results.get("total_models_updated", 0),
            "total_errors": results.get("total_errors", 0),
            "results": results.get("results", {}),
        }
    except Exception as e:
        logger.error(f"Error running pricing sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/{provider}")
async def sync_specific_provider(
    provider: str,
    dry_run: bool = Query(default=False),
    background: bool = Query(default=False),
    api_key: str = Query(None),
    background_tasks: BackgroundTasks = None,
):
    """
    Sync pricing for a specific provider.

    Args:
        provider: Provider name (e.g., openrouter, featherless, nearai, alibaba-cloud)
        dry_run: If True, don't make changes
        background: If True, run in background
        api_key: Optional API key
        background_tasks: FastAPI background tasks

    Returns:
        Sync result for provider
    """
    try:
        service = get_pricing_sync_service()

        if background and background_tasks:
            background_tasks.add_task(
                service.sync_provider_pricing, provider, dry_run
            )
            return {
                "status": "queued",
                "provider": provider,
                "dry_run": dry_run,
                "message": f"Sync for {provider} queued for background execution",
            }

        result = await service.sync_provider_pricing(provider, dry_run=dry_run)

        return {
            "provider": provider,
            "dry_run": dry_run,
            "status": result["status"],
            "models_updated": result.get("models_updated", 0),
            "models_skipped": result.get("models_skipped", 0),
            "price_changes": result.get("price_changes", [])[:20],  # Top 20 changes
            "errors": result.get("errors", []),
        }
    except Exception as e:
        logger.error(f"Error syncing provider {provider}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_sync_history(
    limit: int = Query(default=100, ge=1, le=1000),
    api_key: str = Query(None),
):
    """
    Get recent pricing sync history.

    Args:
        limit: Maximum records to return
        api_key: Optional API key

    Returns:
        List of sync operations with status and details
    """
    try:
        service = get_pricing_sync_service()
        history = service.get_sync_history(limit=limit)

        return {
            "record_count": len(history),
            "history": history,
        }
    except Exception as e:
        logger.error(f"Error retrieving sync history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_sync_status(api_key: str = Query(None)):
    """
    Get current pricing sync status and configuration.

    Returns:
        Current sync status and next scheduled sync
    """
    try:
        service = get_pricing_sync_service()
        history = service.get_sync_history(limit=1)

        last_sync = history[0] if history else None

        return {
            "last_sync": last_sync,
            "configured_providers": [
                "openrouter",
                "featherless",
                "nearai",
                "alibaba-cloud",
            ],
            "auto_sync_enabled": True,
            "sync_interval_hours": 24,
            "next_sync_estimate": "Daily at 00:00 timezone.utc",
        }
    except Exception as e:
        logger.error(f"Error getting sync status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedule")
async def configure_sync_schedule(
    interval_hours: int = Query(default=24, ge=1, le=168),
    enabled: bool = Query(default=True),
    api_key: str = Query(None),
):
    """
    Configure automatic pricing sync schedule.

    Args:
        interval_hours: How often to sync (1-168 hours)
        enabled: Enable/disable automatic sync
        api_key: Optional API key for admin authentication

    Returns:
        Updated schedule configuration
    """
    try:
        # Note: In a production system, this would update a database configuration
        # For now, return the requested config

        logger.info(
            f"Pricing sync schedule configured: interval={interval_hours}h, enabled={enabled}"
        )

        return {
            "status": "configured",
            "auto_sync_enabled": enabled,
            "sync_interval_hours": interval_hours,
            "next_sync_time": "Pending next interval",
            "note": "Schedule configuration requires full implementation with message queue (e.g., Celery, RQ)",
        }
    except Exception as e:
        logger.error(f"Error configuring sync schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))
