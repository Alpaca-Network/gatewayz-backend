"""
Incremental Model Catalog Sync with Change Detection

This module implements an efficient sync strategy that:
1. Fetches models from ALL provider APIs
2. Compares with existing DB state using content hashing
3. Only updates/inserts models that have changed
4. Batch updates Redis cache only for changed providers
5. Minimizes database writes and cache invalidations

Key Features:
- Content-based change detection (SHA-256 hash of model data)
- Efficient bulk comparison using DB queries
- Minimal Redis cache updates (only changed providers)
- Progress tracking and detailed metrics
- Memory-efficient batch processing
"""

import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.db.models_catalog_db import bulk_upsert_models, get_models_by_gateway_for_catalog
from src.services.model_catalog_sync import (
    PROVIDER_FETCH_FUNCTIONS,
    ensure_provider_exists,
    transform_normalized_model_to_db_schema,
)

logger = logging.getLogger(__name__)


def compute_model_hash(model_data: dict[str, Any]) -> str:
    """
    Compute SHA-256 hash of model data for change detection.

    Only includes fields that matter for detecting changes:
    - model_name, provider_model_id
    - pricing (prompt, completion, image, request)
    - context_length
    - modality
    - capabilities (streaming, function_calling, vision)
    - metadata (architecture, tokenizer, etc)

    Excludes volatile fields:
    - created_at, updated_at
    - id, provider_id
    - metadata.synced_at (changes every sync cycle)

    Args:
        model_data: Model dictionary

    Returns:
        64-character hex string (SHA-256 hash)
    """
    # Extract only relevant fields for comparison
    comparable_data = {
        "model_name": model_data.get("model_name"),
        "provider_model_id": model_data.get("provider_model_id"),
        "context_length": model_data.get("context_length"),
        "modality": model_data.get("modality"),
        "pricing": {
            "prompt": str(model_data.get("pricing_prompt")),
            "completion": str(model_data.get("pricing_completion")),
            "image": str(model_data.get("pricing_image")),
            "request": str(model_data.get("pricing_request")),
        },
        "capabilities": {
            "streaming": model_data.get("supports_streaming", False),
            "function_calling": model_data.get("supports_function_calling", False),
            "vision": model_data.get("supports_vision", False),
        },
        "metadata": {
            k: v for k, v in model_data.get("metadata", {}).items()
            if k not in ("synced_at",)
        },
        "description": model_data.get("description"),
        "top_provider": model_data.get("top_provider"),
        "per_request_limits": model_data.get("per_request_limits"),
    }

    # Serialize to JSON with sorted keys for consistent hashing
    json_str = json.dumps(comparable_data, sort_keys=True, default=str)

    # Compute SHA-256 hash
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


def get_existing_model_hashes(provider_id: int) -> dict[str, str]:
    """
    Get existing model hashes from database for a provider.

    Returns a mapping of provider_model_id -> content_hash for all
    models belonging to the given provider.

    Args:
        provider_id: Provider's database ID

    Returns:
        Dict mapping provider_model_id to SHA-256 hash
    """
    try:
        from src.config.supabase_config import get_supabase_client

        supabase = get_supabase_client()

        # Fetch only the fields needed for hashing
        result = (
            supabase.table("models")
            .select(
                "provider_model_id, model_name, context_length, modality, "
                "pricing_prompt, pricing_completion, pricing_image, pricing_request, "
                "supports_streaming, supports_function_calling, supports_vision, "
                "metadata, description, top_provider, per_request_limits"
            )
            .eq("provider_id", provider_id)
            .execute()
        )

        if not result.data:
            return {}

        # Compute hash for each existing model
        model_hashes = {}
        for model in result.data:
            provider_model_id = model.get("provider_model_id")
            if provider_model_id:
                model_hash = compute_model_hash(model)
                model_hashes[provider_model_id] = model_hash

        return model_hashes

    except Exception as e:
        logger.error(f"Failed to fetch existing model hashes: {e}")
        return {}


def sync_provider_incremental(
    provider_slug: str, dry_run: bool = False
) -> dict[str, Any]:
    """
    Sync a single provider with incremental change detection.

    Process:
    1. Fetch models from provider API
    2. Get existing models from DB and compute their hashes
    3. Compare hashes to detect changes
    4. Only write changed/new models to DB
    5. Only invalidate cache if changes were made

    Args:
        provider_slug: Provider slug (e.g., 'openrouter')
        dry_run: If True, detect changes but don't write to DB

    Returns:
        Sync result with detailed metrics
    """
    start_time = time.time()

    try:
        # Ensure provider exists
        provider = ensure_provider_exists(provider_slug)
        if not provider or not provider.get("is_active"):
            return {
                "success": False,
                "provider": provider_slug,
                "error": f"Provider '{provider_slug}' not found or inactive",
                "models_fetched": 0,
                "models_changed": 0,
                "models_synced": 0,
            }

        provider_id = provider["id"]

        # Get fetch function
        fetch_func = PROVIDER_FETCH_FUNCTIONS.get(provider_slug)
        if not fetch_func:
            return {
                "success": False,
                "provider": provider_slug,
                "error": f"No fetch function for '{provider_slug}'",
                "models_fetched": 0,
                "models_changed": 0,
                "models_synced": 0,
            }

        logger.info(f"[{provider_slug.upper()}] Starting incremental sync...")

        # Step 1: Fetch models from provider API
        fetch_start = time.time()
        normalized_models = fetch_func()
        fetch_duration = time.time() - fetch_start

        if not normalized_models:
            return {
                "success": True,
                "provider": provider_slug,
                "models_fetched": 0,
                "models_changed": 0,
                "models_synced": 0,
                "message": "No models fetched from API",
                "fetch_duration": fetch_duration,
            }

        logger.info(
            f"[{provider_slug.upper()}] Fetched {len(normalized_models)} models "
            f"in {fetch_duration:.2f}s"
        )

        # Step 2: Get existing model hashes from DB
        hash_start = time.time()
        existing_hashes = get_existing_model_hashes(provider_id)
        hash_duration = time.time() - hash_start

        logger.info(
            f"[{provider_slug.upper()}] Loaded {len(existing_hashes)} existing model hashes "
            f"in {hash_duration:.2f}s"
        )

        # Step 3: Transform and compare
        transform_start = time.time()
        models_to_update = []
        new_models = []
        unchanged_models = 0
        skipped_models = 0

        for normalized_model in normalized_models:
            # Transform to DB schema
            db_model = transform_normalized_model_to_db_schema(
                normalized_model, provider_id, provider_slug
            )

            if not db_model:
                skipped_models += 1
                continue

            provider_model_id = db_model.get("provider_model_id")
            if not provider_model_id:
                skipped_models += 1
                continue

            # Compute hash of fetched model
            new_hash = compute_model_hash(db_model)

            # Compare with existing
            existing_hash = existing_hashes.get(provider_model_id)

            if existing_hash is None:
                # New model
                new_models.append(db_model)
            elif existing_hash != new_hash:
                # Model changed
                models_to_update.append(db_model)
            else:
                # No change
                unchanged_models += 1

        transform_duration = time.time() - transform_start

        total_changed = len(new_models) + len(models_to_update)

        logger.info(
            f"[{provider_slug.upper()}] Change detection complete:\n"
            f"  - New models: {len(new_models)}\n"
            f"  - Updated models: {len(models_to_update)}\n"
            f"  - Unchanged: {unchanged_models}\n"
            f"  - Skipped: {skipped_models}\n"
            f"  - Total changed: {total_changed}/{len(normalized_models)}"
        )

        # Step 4: Write changes to DB (only if there are changes)
        db_duration = 0
        models_synced = 0

        if total_changed > 0 and not dry_run:
            db_start = time.time()
            all_changes = new_models + models_to_update

            try:
                bulk_upsert_models(all_changes)
                models_synced = len(all_changes)
                logger.info(
                    f"[{provider_slug.upper()}] Synced {models_synced} changed models to DB"
                )
            except Exception as e:
                logger.error(f"[{provider_slug.upper()}] DB sync failed: {e}")
                return {
                    "success": False,
                    "provider": provider_slug,
                    "error": f"DB sync failed: {e}",
                    "models_fetched": len(normalized_models),
                    "models_changed": total_changed,
                    "models_synced": 0,
                }

            db_duration = time.time() - db_start

        # Step 5: Invalidate cache only if changes were made
        cache_duration = 0
        if models_synced > 0 and not dry_run:
            cache_start = time.time()
            try:
                from src.services.model_catalog_cache import invalidate_provider_catalog

                invalidate_provider_catalog(provider_slug)
                logger.info(f"[{provider_slug.upper()}] Cache invalidated")
            except Exception as e:
                logger.warning(f"[{provider_slug.upper()}] Cache invalidation failed: {e}")
            cache_duration = time.time() - cache_start

        total_duration = time.time() - start_time

        return {
            "success": True,
            "provider": provider_slug,
            "models_fetched": len(normalized_models),
            "models_new": len(new_models),
            "models_updated": len(models_to_update),
            "models_unchanged": unchanged_models,
            "models_skipped": skipped_models,
            "models_changed": total_changed,
            "models_synced": models_synced,
            "dry_run": dry_run,
            "metrics": {
                "fetch_duration": fetch_duration,
                "hash_duration": hash_duration,
                "transform_duration": transform_duration,
                "db_duration": db_duration,
                "cache_duration": cache_duration,
                "total_duration": total_duration,
            },
        }

    except Exception as e:
        logger.exception(f"[{provider_slug.upper()}] Incremental sync failed: {e}")
        return {
            "success": False,
            "provider": provider_slug,
            "error": str(e),
            "models_fetched": 0,
            "models_changed": 0,
            "models_synced": 0,
        }


def sync_all_providers_incremental(
    provider_slugs: list[str] | None = None,
    dry_run: bool = False,
    max_concurrent: int = 1,
) -> dict[str, Any]:
    """
    Sync all providers incrementally with change detection.

    This is the main entry point for efficient scheduled sync.

    Process:
    1. For each provider:
       - Fetch models from API
       - Detect changes via hash comparison
       - Only write changed models to DB
       - Only invalidate provider cache if changed
    2. After ALL providers:
       - If ANY provider changed, invalidate global caches once

    Args:
        provider_slugs: Optional list of providers to sync (defaults to all)
        dry_run: If True, detect changes but don't write
        max_concurrent: Future: support parallel provider sync (currently serial)

    Returns:
        Overall sync results with aggregated metrics
    """
    sync_start = time.time()

    try:
        from src.config.config import Config

        # Determine which providers to sync
        if provider_slugs:
            providers_to_sync = provider_slugs
        else:
            skip_set = Config.MODEL_SYNC_SKIP_PROVIDERS
            all_providers = list(PROVIDER_FETCH_FUNCTIONS.keys())
            providers_to_sync = [p for p in all_providers if p not in skip_set]

            if skip_set:
                logger.info(
                    f"Skipping {len(skip_set)} providers: {', '.join(sorted(skip_set))}"
                )

        logger.info("=" * 80)
        logger.info(f"INCREMENTAL SYNC: {len(providers_to_sync)} providers")
        logger.info(f"Dry run: {dry_run}")
        logger.info("=" * 80)

        results = []
        total_fetched = 0
        total_changed = 0
        total_synced = 0
        total_unchanged = 0
        providers_with_changes = []
        errors = []

        # Sync each provider
        for i, provider_slug in enumerate(providers_to_sync, 1):
            logger.info(f"\n[{i}/{len(providers_to_sync)}] Syncing: {provider_slug.upper()}")

            result = sync_provider_incremental(provider_slug, dry_run=dry_run)
            results.append(result)

            if result["success"]:
                total_fetched += result.get("models_fetched", 0)
                total_changed += result.get("models_changed", 0)
                total_synced += result.get("models_synced", 0)
                total_unchanged += result.get("models_unchanged", 0)

                if result.get("models_changed", 0) > 0:
                    providers_with_changes.append(provider_slug)
            else:
                errors.append({
                    "provider": provider_slug,
                    "error": result.get("error")
                })

        # Invalidate global caches ONCE if any provider had changes
        if providers_with_changes and not dry_run:
            logger.info(
                f"\n{len(providers_with_changes)} providers had changes, "
                f"invalidating global caches..."
            )
            try:
                from src.services.model_catalog_cache import (
                    invalidate_full_catalog,
                    invalidate_unique_models,
                    invalidate_catalog_stats,
                )

                invalidate_full_catalog()
                invalidate_unique_models()
                invalidate_catalog_stats()

                logger.info("✅ Global caches invalidated")
            except Exception as e:
                logger.warning(f"Global cache invalidation failed: {e}")

        total_duration = time.time() - sync_start
        success_count = sum(1 for r in results if r.get("success"))

        # Calculate efficiency metrics
        change_rate = (total_changed / total_fetched * 100) if total_fetched > 0 else 0
        efficiency_gain = (total_unchanged / total_fetched * 100) if total_fetched > 0 else 0

        logger.info("=" * 80)
        logger.info("INCREMENTAL SYNC COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Duration: {total_duration:.2f}s")
        logger.info(f"Providers synced: {success_count}/{len(providers_to_sync)}")
        logger.info(f"Models fetched: {total_fetched:,}")
        logger.info(f"Models changed: {total_changed:,} ({change_rate:.1f}%)")
        logger.info(f"Models unchanged: {total_unchanged:,} ({efficiency_gain:.1f}%)")
        logger.info(f"Models synced to DB: {total_synced:,}")
        logger.info(f"Providers with changes: {len(providers_with_changes)}")
        logger.info(f"Errors: {len(errors)}")
        logger.info("=" * 80)

        return {
            "success": len(errors) == 0,
            "dry_run": dry_run,
            "total_providers": len(providers_to_sync),
            "providers_synced": success_count,
            "providers_with_changes": len(providers_with_changes),
            "changed_providers": providers_with_changes,
            "total_models_fetched": total_fetched,
            "total_models_changed": total_changed,
            "total_models_unchanged": total_unchanged,
            "total_models_synced": total_synced,
            "change_rate_percent": round(change_rate, 2),
            "efficiency_gain_percent": round(efficiency_gain, 2),
            "errors": errors,
            "total_duration_seconds": round(total_duration, 2),
            "results_by_provider": results,
        }

    except Exception as e:
        logger.exception(f"Incremental sync failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "total_providers": 0,
            "providers_synced": 0,
            "total_models_fetched": 0,
            "total_models_changed": 0,
            "total_models_synced": 0,
        }
