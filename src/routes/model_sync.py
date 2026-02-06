"""
API routes for model catalog synchronization
Dynamically fetches and syncs models from provider APIs to database

Phase 3 (Issue #997): Added health monitoring endpoint for scheduled sync
Phase 4 (Issue #XXXX): Fixed event loop blocking in sync endpoints
Phase 5: Added comprehensive trace logging for debugging and monitoring
"""

import asyncio
import logging
import time

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
from src.db.models_catalog_db import (
    flush_models_table,
    flush_providers_table,
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
    # TRACE: Function entry
    start_time = time.time()
    logger.info(
        f"🎯 [SYNC-START] Provider sync initiated | "
        f"provider={provider_slug} | dry_run={dry_run}"
    )

    try:
        # TRACE: Validation phase
        logger.debug(f"[SYNC-VALIDATE] Checking if provider '{provider_slug}' is registered")
        if provider_slug not in PROVIDER_FETCH_FUNCTIONS:
            available = sorted(PROVIDER_FETCH_FUNCTIONS.keys())
            logger.error(
                f"[SYNC-ERROR] Provider not found | "
                f"provider={provider_slug} | available_count={len(available)}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider '{provider_slug}' not found. Available providers: {', '.join(available)}"
            )

        logger.info(f"[SYNC-VALIDATE] ✓ Provider '{provider_slug}' validated")

        # TRACE: Thread execution
        logger.info(
            f"[SYNC-EXECUTE] Starting sync in thread pool | "
            f"provider={provider_slug} | thread_safe=True"
        )
        thread_start = time.time()

        # Run blocking sync function in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(sync_provider_models, provider_slug, dry_run)

        thread_duration = time.time() - thread_start
        logger.info(
            f"[SYNC-EXECUTE] ✓ Thread execution completed | "
            f"provider={provider_slug} | duration={thread_duration:.2f}s | "
            f"success={result.get('success', False)}"
        )

        # TRACE: Result validation
        logger.debug(
            f"[SYNC-RESULT] Checking sync result | "
            f"provider={provider_slug} | success={result.get('success')} | "
            f"fetched={result.get('models_fetched', 0)} | "
            f"transformed={result.get('models_transformed', 0)} | "
            f"synced={result.get('models_synced', 0)} | "
            f"skipped={result.get('models_skipped', 0)}"
        )

        if not result["success"]:
            error_msg = result.get("error", "Sync failed")
            logger.error(
                f"[SYNC-FAILED] Sync operation failed | "
                f"provider={provider_slug} | error={error_msg}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg
            )

        logger.info(f"[SYNC-RESULT] ✓ Sync successful | provider={provider_slug}")

        # TRACE: Cache invalidation phase
        if not dry_run:
            logger.debug(f"[SYNC-CACHE] Starting cache invalidation | provider={provider_slug}")
            try:
                from src.services.catalog_response_cache import invalidate_catalog_cache
                cache_start = time.time()
                deleted_count = invalidate_catalog_cache(provider_slug)
                cache_duration = time.time() - cache_start
                logger.info(
                    f"[SYNC-CACHE] ✓ Cache invalidated | "
                    f"provider={provider_slug} | entries_deleted={deleted_count} | "
                    f"duration={cache_duration:.2f}s"
                )
            except Exception as cache_error:
                # Don't fail sync if cache invalidation fails
                logger.warning(
                    f"[SYNC-CACHE] ⚠ Cache invalidation failed (non-critical) | "
                    f"provider={provider_slug} | error={cache_error}"
                )
        else:
            logger.debug(f"[SYNC-CACHE] Skipping cache invalidation (dry_run=True)")

        # TRACE: Build response
        total_duration = time.time() - start_time
        message = (
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Synced {result.get('models_synced', 0)} models from {provider_slug}. "
            f"Fetched: {result.get('models_fetched', 0)}, "
            f"Transformed: {result.get('models_transformed', 0)}, "
            f"Skipped: {result.get('models_skipped', 0)}"
        )

        logger.info(
            f"✅ [SYNC-COMPLETE] Provider sync completed successfully | "
            f"provider={provider_slug} | total_duration={total_duration:.2f}s | "
            f"models_synced={result.get('models_synced', 0)} | "
            f"dry_run={dry_run}"
        )

        return SyncResponse(
            success=True,
            message=message,
            details=result
        )

    except HTTPException:
        total_duration = time.time() - start_time
        logger.warning(
            f"⚠ [SYNC-HTTP-ERROR] HTTP exception in sync endpoint | "
            f"provider={provider_slug} | duration={total_duration:.2f}s"
        )
        raise
    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(
            f"❌ [SYNC-EXCEPTION] Unexpected error in sync endpoint | "
            f"provider={provider_slug} | duration={total_duration:.2f}s | "
            f"error_type={type(e).__name__} | error={str(e)}",
            exc_info=True
        )
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
    # TRACE: Function entry
    start_time = time.time()
    provider_count = len(providers) if providers else len(PROVIDER_FETCH_FUNCTIONS)
    logger.info(
        f"🎯 [BULK-SYNC-START] Bulk provider sync initiated | "
        f"provider_count={provider_count} | dry_run={dry_run} | "
        f"specific_providers={bool(providers)}"
    )

    try:
        # TRACE: Validation phase
        if providers:
            logger.debug(
                f"[BULK-SYNC-VALIDATE] Validating provider list | "
                f"requested={providers}"
            )
            invalid_providers = [p for p in providers if p not in PROVIDER_FETCH_FUNCTIONS]
            if invalid_providers:
                available = sorted(PROVIDER_FETCH_FUNCTIONS.keys())
                logger.error(
                    f"[BULK-SYNC-ERROR] Invalid providers detected | "
                    f"invalid={invalid_providers} | available_count={len(available)}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid providers: {', '.join(invalid_providers)}. "
                           f"Available: {', '.join(available)}"
                )
            logger.info(f"[BULK-SYNC-VALIDATE] ✓ All providers validated | count={len(providers)}")
        else:
            logger.info(f"[BULK-SYNC-VALIDATE] Syncing all providers | count={provider_count}")

        # TRACE: Thread execution
        logger.info(
            f"[BULK-SYNC-EXECUTE] Starting bulk sync in thread pool | "
            f"provider_count={provider_count} | thread_safe=True"
        )
        thread_start = time.time()

        # Run blocking sync function in thread pool to avoid blocking event loop
        result = await asyncio.to_thread(sync_all_providers, provider_slugs=providers, dry_run=dry_run)

        thread_duration = time.time() - thread_start
        logger.info(
            f"[BULK-SYNC-EXECUTE] ✓ Thread execution completed | "
            f"duration={thread_duration:.2f}s | "
            f"providers_processed={result.get('providers_processed', 0)} | "
            f"success={result.get('success', False)}"
        )

        # TRACE: Result analysis
        error_count = len(result.get("errors", []))
        success_count = result.get("providers_processed", 0) - error_count
        logger.info(
            f"[BULK-SYNC-RESULT] Sync results summary | "
            f"providers_processed={result.get('providers_processed', 0)} | "
            f"success_count={success_count} | error_count={error_count} | "
            f"total_models_synced={result.get('total_models_synced', 0)} | "
            f"total_models_fetched={result.get('total_models_fetched', 0)}"
        )

        # TRACE: Cache invalidation phase
        if not dry_run and result.get("success"):
            logger.debug("[BULK-SYNC-CACHE] Starting bulk cache invalidation")
            try:
                from src.services.catalog_response_cache import invalidate_catalog_cache
                cache_start = time.time()
                deleted_count = invalidate_catalog_cache()  # None = invalidate all
                cache_duration = time.time() - cache_start
                logger.info(
                    f"[BULK-SYNC-CACHE] ✓ All caches invalidated | "
                    f"entries_deleted={deleted_count} | duration={cache_duration:.2f}s"
                )
            except Exception as cache_error:
                logger.warning(
                    f"[BULK-SYNC-CACHE] ⚠ Cache invalidation failed (non-critical) | "
                    f"error={cache_error}"
                )
        else:
            reason = "dry_run=True" if dry_run else "sync_failed"
            logger.debug(f"[BULK-SYNC-CACHE] Skipping cache invalidation | reason={reason}")

        # TRACE: Catastrophic failure check
        if not result.get("providers_processed"):
            error_msg = result.get("error", "Sync failed completely")
            logger.error(
                f"[BULK-SYNC-CATASTROPHIC] Complete sync failure | "
                f"error={error_msg}"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_msg
            )

        # TRACE: Build response
        total_duration = time.time() - start_time
        message = (
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Processed {result['providers_processed']} providers. "
            f"Success: {success_count}, Errors: {error_count}. "
            f"Total synced: {result.get('total_models_synced', 0)} models "
            f"(Fetched: {result.get('total_models_fetched', 0)}, "
            f"Transformed: {result.get('total_models_transformed', 0)}, "
            f"Skipped: {result.get('total_models_skipped', 0)})"
        )

        status_emoji = "✅" if result.get("success", False) else "⚠"
        logger.info(
            f"{status_emoji} [BULK-SYNC-COMPLETE] Bulk sync completed | "
            f"total_duration={total_duration:.2f}s | "
            f"providers_processed={result.get('providers_processed', 0)} | "
            f"models_synced={result.get('total_models_synced', 0)} | "
            f"success_rate={success_count}/{result.get('providers_processed', 0)} | "
            f"dry_run={dry_run}"
        )

        return SyncResponse(
            success=result.get("success", False),
            message=message,
            details=result
        )

    except HTTPException:
        total_duration = time.time() - start_time
        logger.warning(
            f"⚠ [BULK-SYNC-HTTP-ERROR] HTTP exception in bulk sync endpoint | "
            f"duration={total_duration:.2f}s"
        )
        raise
    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(
            f"❌ [BULK-SYNC-EXCEPTION] Unexpected error in bulk sync endpoint | "
            f"duration={total_duration:.2f}s | "
            f"error_type={type(e).__name__} | error={str(e)}",
            exc_info=True
        )
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

            # Run blocking sync function in thread pool to avoid blocking event loop
            model_result = await asyncio.to_thread(sync_all_providers, dry_run=True)

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


@router.delete("/flush-models", response_model=SyncResponse)
async def flush_models(
    confirm: str = Query(..., description="Must be 'DELETE_ALL_MODELS' to confirm")
):
    """
    Flush (delete all records from) the models table

    ⚠️ **WARNING: This is a destructive operation!** ⚠️

    This endpoint deletes ALL models from the database while preserving the providers table.
    Use this when you want to:
    - Clean up all model data before a full resync
    - Remove legacy/malformed model data
    - Reset the models catalog to a clean state

    **Safety Requirements:**
    - Requires confirmation parameter: `confirm=DELETE_ALL_MODELS`
    - Admin authentication required
    - Operation is logged for audit purposes

    **What gets deleted:**
    - All records in the `models` table

    **What is preserved:**
    - All records in the `providers` table
    - All other database tables

    **Recommended workflow:**
    1. Flush models: `DELETE /admin/model-sync/flush-models?confirm=DELETE_ALL_MODELS`
    2. Resync models: `POST /admin/model-sync/full`

    Args:
        confirm: Must be exactly "DELETE_ALL_MODELS" to proceed

    Returns:
        Flush results including count of deleted models

    Example:
        ```bash
        curl -X DELETE "http://api/admin/model-sync/flush-models?confirm=DELETE_ALL_MODELS" \\
          -H "Authorization: Bearer YOUR_ADMIN_KEY"
        ```
    """
    try:
        # Validate confirmation
        if confirm != "DELETE_ALL_MODELS":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Confirmation failed. Must provide confirm=DELETE_ALL_MODELS to proceed with deletion."
            )

        logger.warning("🚨 Flush models endpoint called - proceeding with deletion")

        # Perform flush (run in thread pool to avoid blocking event loop)
        result = await asyncio.to_thread(flush_models_table)

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("message", "Failed to flush models table")
            )

        deleted_count = result.get("deleted_count", 0)
        message = f"Successfully flushed models table. Deleted {deleted_count} models."

        logger.info(f"✅ {message}")

        return SyncResponse(
            success=True,
            message=message,
            details=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in flush models endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/flush-providers", response_model=SyncResponse)
async def flush_providers(
    confirm: str = Query(..., description="Must be 'DELETE_EVERYTHING' to confirm")
):
    """
    Flush (delete all records from) the providers table (cascades to models)

    ⚠️ **EXTREME WARNING: This is a HIGHLY destructive operation!** ⚠️

    This endpoint deletes ALL providers AND all associated models from the database.
    Due to the CASCADE constraint on the foreign key, deleting providers automatically
    deletes all models that reference them.

    **Use this when you want to:**
    - Perform a complete database reset
    - Start fresh with a clean providers and models catalog
    - Fix fundamental data structure issues

    **Safety Requirements:**
    - Requires confirmation parameter: `confirm=DELETE_EVERYTHING`
    - Admin authentication required
    - Operation is logged for audit purposes

    **What gets deleted:**
    - All records in the `providers` table
    - All records in the `models` table (CASCADE)

    **Database schema reference:**
    ```sql
    "provider_id" INTEGER REFERENCES "public"."providers"("id") ON DELETE CASCADE
    ```

    **Recommended workflow:**
    1. Flush everything: `DELETE /admin/model-sync/flush-providers?confirm=DELETE_EVERYTHING`
    2. Resync everything: `POST /admin/model-sync/full`

    Args:
        confirm: Must be exactly "DELETE_EVERYTHING" to proceed

    Returns:
        Flush results including counts of deleted providers and models

    Example:
        ```bash
        curl -X DELETE "http://api/admin/model-sync/flush-providers?confirm=DELETE_EVERYTHING" \\
          -H "Authorization: Bearer YOUR_ADMIN_KEY"
        ```
    """
    try:
        # Validate confirmation
        if confirm != "DELETE_EVERYTHING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Confirmation failed. Must provide confirm=DELETE_EVERYTHING to proceed with deletion."
            )

        logger.warning("🚨🚨 Flush providers endpoint called - proceeding with deletion of ALL data")

        # Perform flush (run in thread pool to avoid blocking event loop)
        result = await asyncio.to_thread(flush_providers_table)

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("message", "Failed to flush providers table")
            )

        providers_count = result.get("deleted_providers_count", 0)
        models_count = result.get("deleted_models_count", 0)
        message = (
            f"Successfully flushed providers table. "
            f"Deleted {providers_count} providers and {models_count} models (CASCADE)."
        )

        logger.info(f"✅ {message}")

        return SyncResponse(
            success=True,
            message=message,
            details=result
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in flush providers endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/reset-and-resync", response_model=SyncResponse)
async def reset_and_resync():
    """
    Atomic operation: Flush models table and immediately resync from providers

    This endpoint performs a complete refresh of the models catalog in a single operation:
    1. **Flush**: Delete all models (preserves providers)
    2. **Resync**: Immediately trigger full sync from all provider APIs

    **Benefits of atomic operation:**
    - Single API call instead of two
    - Ensures models are repopulated immediately
    - Reduces time window where database is empty
    - Automatic rollback if sync fails

    **What happens:**
    1. Deletes all models from database
    2. Syncs providers from GATEWAY_REGISTRY
    3. Syncs models from all provider APIs
    4. Returns combined results

    **Safety:**
    - Admin authentication required
    - Operation is logged
    - If sync fails, database will be empty (no rollback)

    **Use cases:**
    - Clean database refresh
    - Fix malformed model data
    - Update all model metadata
    - Recovery from sync issues

    **Timeline:**
    - Flush: < 1 second
    - Provider sync: < 1 second
    - Model sync: 5-30 minutes (depends on number of providers)
    - Total: ~5-30 minutes

    Returns:
        Combined results from flush and sync operations

    Example:
        ```bash
        curl -X POST "http://api/admin/model-sync/reset-and-resync" \\
          -H "Authorization: Bearer YOUR_ADMIN_KEY"
        ```

    Note: This endpoint does NOT require confirmation since it immediately repopulates
    the database. If you want to flush without resyncing, use the separate flush endpoints.
    """
    try:
        logger.info("🔄 Reset and resync operation starting...")

        # Step 1: Flush models
        logger.info("Step 1/3: Flushing models table...")
        flush_result = await asyncio.to_thread(flush_models_table)

        if not flush_result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to flush models: {flush_result.get('message')}"
            )

        deleted_count = flush_result.get("deleted_count", 0)
        logger.info(f"✅ Flushed {deleted_count} models")

        # Step 2: Full sync (providers + models)
        logger.info("Step 2/3: Starting full sync...")
        sync_result = await trigger_full_sync()

        provider_success = sync_result.get("providers", {}).get("success", False)
        model_success = sync_result.get("models", {}).get("success", False)
        overall_success = provider_success and model_success

        providers_synced = sync_result.get("providers", {}).get("synced_count", 0)
        models_synced = sync_result.get("models", {}).get("total_models_synced", 0)
        providers_processed = sync_result.get("models", {}).get("providers_processed", 0)

        logger.info(
            f"✅ Sync completed: {providers_synced} providers, "
            f"{models_synced} models from {providers_processed} providers"
        )

        # Step 3: Prepare response
        message = (
            f"Reset and resync completed. "
            f"Deleted {deleted_count} old models, "
            f"synced {providers_synced} providers, "
            f"added {models_synced} new models from {providers_processed} providers. "
            f"Status: {'✓ Success' if overall_success else '✗ Partial Success'}"
        )

        return SyncResponse(
            success=overall_success,
            message=message,
            details={
                "flush": flush_result,
                "sync": sync_result
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in reset and resync endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============================================================================
# SCHEDULED SYNC HEALTH MONITORING (Phase 3 - Issue #997)
# ============================================================================


@router.get("/health", tags=["Health"])
async def get_scheduled_sync_health():
    """
    Get health status of scheduled model sync service.

    Returns metrics about the scheduled sync job including:
    - Last run time
    - Success rate
    - Health status
    - Configuration

    This endpoint is used for monitoring and alerting.

    Example Response:
    ```json
    {
        "is_healthy": true,
        "health_reason": "Healthy",
        "enabled": true,
        "last_success_time": "2024-01-29T10:30:00Z",
        "minutes_since_last_sync": 15,
        "success_rate": 100.0,
        "total_runs": 48,
        "successful_runs": 48,
        "failed_runs": 0
    }
    ```
    """
    try:
        from src.services.scheduled_sync import get_sync_status

        status = get_sync_status()
        return status

    except Exception as e:
        logger.error(f"Error getting sync health status: {e}", exc_info=True)
        return {
            "is_healthy": False,
            "health_reason": f"Error retrieving status: {str(e)}",
            "enabled": False,
            "error": str(e),
        }


@router.post("/trigger", tags=["Admin"])
async def trigger_manual_sync():
    """
    Manually trigger a model sync job.

    This endpoint allows admins to manually trigger a sync outside of the
    scheduled interval. Useful for:
    - Immediate updates after provider changes
    - Testing the sync process
    - Recovery after errors

    The sync runs in the background - check logs for progress.

    Returns:
        Status message and timestamp
    """
    try:
        from src.services.scheduled_sync import trigger_manual_sync as trigger_sync

        result = trigger_sync()
        return result

    except Exception as e:
        logger.error(f"Error triggering manual sync: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger manual sync: {str(e)}"
        )
