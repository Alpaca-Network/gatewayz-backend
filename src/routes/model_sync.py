"""
API routes for model catalog synchronization
Dynamically fetches and syncs models from provider APIs to database
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.services.model_catalog_sync import (
    PROVIDER_FETCH_FUNCTIONS,
    sync_all_providers,
    sync_provider_models,
)
from src.services.provider_model_sync_service import (
    sync_providers_to_database,
    trigger_full_sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/model-sync",
    tags=["Admin - Model Sync"],
)


class SyncResponse(BaseModel):
    """Response from sync operation"""
    success: bool = Field(..., description="Whether sync was successful")
    message: str = Field(..., description="Human-readable summary message")
    details: dict = Field(..., description="Detailed sync results")


class ProviderListResponse(BaseModel):
    """Response with list of available providers"""
    providers: list[str] = Field(..., description="List of provider slugs")
    count: int = Field(..., description="Number of providers")


@router.get("/providers", response_model=ProviderListResponse)
async def list_available_providers():
    """
    Get list of providers that can be synced

    Returns list of all provider slugs that have fetch functions configured.
    """
    providers = list(PROVIDER_FETCH_FUNCTIONS.keys())
    return ProviderListResponse(
        providers=sorted(providers),
        count=len(providers)
    )


@router.post("/provider/{provider_slug}", response_model=SyncResponse)
async def sync_single_provider(
    provider_slug: str,
    dry_run: bool = Query(False, description="Fetch but don't write to database")
):
    """
    Sync models from a specific provider's API to database

    This endpoint:
    1. Ensures the provider exists in the database (creates if needed)
    2. Fetches the latest model catalog from the provider's API
    3. Transforms models to database schema
    4. Upserts models into the database (insert or update)

    Args:
        provider_slug: Provider identifier (e.g., 'openrouter', 'deepinfra')
        dry_run: If True, performs fetch and transformation but doesn't write to DB

    Returns:
        Sync results including counts and any errors
    """
    try:
        # Validate provider exists
        if provider_slug not in PROVIDER_FETCH_FUNCTIONS:
            available = sorted(PROVIDER_FETCH_FUNCTIONS.keys())
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider '{provider_slug}' not found. Available providers: {', '.join(available)}"
            )

        result = sync_provider_models(provider_slug, dry_run=dry_run)

        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Sync failed")
            )

        message = (
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Synced {result.get('models_synced', 0)} models from {provider_slug}. "
            f"Fetched: {result.get('models_fetched', 0)}, "
            f"Transformed: {result.get('models_transformed', 0)}, "
            f"Skipped: {result.get('models_skipped', 0)}"
        )

        return SyncResponse(
            success=True,
            message=message,
            details=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in sync endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/all", response_model=SyncResponse)
async def sync_all_provider_models(
    providers: list[str] | None = Query(
        None,
        description="Specific providers to sync (comma-separated). If not provided, syncs all providers."
    ),
    dry_run: bool = Query(False, description="Fetch but don't write to database")
):
    """
    Sync models from all providers (or specified list)

    This is a bulk operation that:
    1. Ensures all providers exist in the database
    2. Fetches models from each provider's API in sequence
    3. Transforms and syncs all models to the database

    Args:
        providers: Optional list of specific providers to sync. If not provided, syncs all.
        dry_run: If True, performs fetch and transformation but doesn't write to DB

    Returns:
        Aggregate sync results across all providers

    Example:
        POST /admin/model-sync/all
        POST /admin/model-sync/all?providers=openrouter&providers=deepinfra
        POST /admin/model-sync/all?dry_run=true
    """
    try:
        # Validate providers if specified
        if providers:
            invalid_providers = [p for p in providers if p not in PROVIDER_FETCH_FUNCTIONS]
            if invalid_providers:
                available = sorted(PROVIDER_FETCH_FUNCTIONS.keys())
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid providers: {', '.join(invalid_providers)}. "
                           f"Available: {', '.join(available)}"
                )

        result = sync_all_providers(provider_slugs=providers, dry_run=dry_run)

        # Even if there are errors, we return 200 with details
        # Only raise if catastrophic failure
        if not result.get("providers_processed"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Sync failed completely")
            )

        error_count = len(result.get("errors", []))
        success_count = result["providers_processed"] - error_count

        message = (
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Processed {result['providers_processed']} providers. "
            f"Success: {success_count}, Errors: {error_count}. "
            f"Total synced: {result.get('total_models_synced', 0)} models "
            f"(Fetched: {result.get('total_models_fetched', 0)}, "
            f"Transformed: {result.get('total_models_transformed', 0)}, "
            f"Skipped: {result.get('total_models_skipped', 0)})"
        )

        return SyncResponse(
            success=result.get("success", False),
            message=message,
            details=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in sync all endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/status", response_model=dict)
async def get_sync_status():
    """
    Get current sync status and statistics

    Returns information about:
    - Available providers
    - Providers configured with fetch functions
    - Current database statistics
    """
    try:
        from src.db.models_catalog_db import get_models_stats
        from src.db.providers_db import get_all_providers, get_providers_stats

        # Get database stats
        provider_stats = get_providers_stats()
        model_stats = get_models_stats()

        # Get all providers from database
        db_providers = get_all_providers(include_inactive=True)

        # Get providers with fetch functions
        fetchable_providers = sorted(PROVIDER_FETCH_FUNCTIONS.keys())

        # Find which providers in DB have fetch functions
        db_provider_slugs = {p["slug"] for p in db_providers}
        fetchable_in_db = sorted(db_provider_slugs.intersection(fetchable_providers))
        fetchable_not_in_db = sorted(set(fetchable_providers) - db_provider_slugs)

        return {
            "providers": {
                "in_database": len(db_providers),
                "with_fetch_functions": len(fetchable_providers),
                "fetchable_in_db": len(fetchable_in_db),
                "fetchable_not_in_db": len(fetchable_not_in_db),
                "stats": provider_stats,
            },
            "models": {
                "stats": model_stats,
            },
            "fetchable_providers": fetchable_providers,
            "fetchable_in_db": fetchable_in_db,
            "fetchable_not_in_db": fetchable_not_in_db,
        }

    except Exception as e:
        logger.error(f"Error getting sync status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/full", response_model=SyncResponse)
async def trigger_full_provider_and_model_sync(
    dry_run: bool = Query(False, description="Fetch but don't write to database")
):
    """
    Trigger a complete sync of providers and models

    This endpoint performs a full synchronization in two phases:
    1. **Provider Sync**: Syncs all providers from GATEWAY_REGISTRY to database
       - Ensures providers table matches the code configuration
       - Creates/updates provider records
       - Sets metadata like colors, priority, site URLs

    2. **Model Sync**: Syncs models from all configured providers
       - Fetches latest model catalogs from each provider's API
       - Transforms and upserts models to database
       - Updates pricing, capabilities, and metadata

    This is the recommended way to fully refresh the catalog.

    Args:
        dry_run: If True, performs sync operations but doesn't write to DB

    Returns:
        Combined sync results from both providers and models

    Example:
        POST /admin/model-sync/full
        POST /admin/model-sync/full?dry_run=true
    """
    try:
        logger.info("🚀 Starting full provider and model sync...")

        if dry_run:
            # For dry run, just call the sync functions without writing
            provider_result = await sync_providers_to_database()

            # Note: sync_all_providers already supports dry_run
            model_result = sync_all_providers(dry_run=True)

            result = {
                "success": True,
                "providers": provider_result,
                "models": model_result,
                "dry_run": True
            }
        else:
            # Use the existing full sync function
            result = await trigger_full_sync()

        provider_success = result.get("providers", {}).get("success", False)
        model_success = result.get("models", {}).get("success", False)
        overall_success = provider_success and model_success

        providers_synced = result.get("providers", {}).get("synced_count", 0)
        models_synced = result.get("models", {}).get("total_models_synced", 0)
        providers_processed = result.get("models", {}).get("providers_processed", 0)

        message = (
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Full sync completed. "
            f"Providers: {providers_synced} synced, "
            f"Models: {models_synced} synced from {providers_processed} providers. "
            f"Provider sync: {'✓' if provider_success else '✗'}, "
            f"Model sync: {'✓' if model_success else '✗'}"
        )

        return SyncResponse(
            success=overall_success,
            message=message,
            details=result
        )

    except Exception as e:
        logger.error(f"Error in full sync endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/providers-only", response_model=SyncResponse)
async def sync_providers_only():
    """
    Sync only providers from GATEWAY_REGISTRY to database

    This endpoint syncs providers without syncing models, useful when:
    - You only need to update provider metadata
    - You want to add new providers to the database first
    - You're testing provider configuration changes

    This operation is fast (< 1 second) as it only updates provider records.

    Returns:
        Provider sync results

    Example:
        POST /admin/model-sync/providers-only
    """
    try:
        logger.info("🔄 Syncing providers only...")

        result = await sync_providers_to_database()

        synced_count = result.get("synced_count", 0)
        success = result.get("success", False)

        message = (
            f"Provider sync completed. "
            f"Synced {synced_count} providers from GATEWAY_REGISTRY. "
            f"Status: {'✓ Success' if success else '✗ Failed'}"
        )

        return SyncResponse(
            success=success,
            message=message,
            details=result
        )

    except Exception as e:
        logger.error(f"Error in provider sync endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
