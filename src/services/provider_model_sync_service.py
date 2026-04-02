"""
Provider & Model Synchronization Service

Manages automatic syncing of:
1. Providers: DB is source of truth (Phase 2A) — sync_providers_to_database() is a no-op
2. Models: Synced periodically from provider APIs (fetches latest)

Usage:
    # On startup:
    await sync_providers_on_startup()

    # Periodic model sync:
    await sync_models_periodically()

    # Manual trigger:
    await trigger_full_sync()
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from src.services.model_catalog_sync import sync_provider_models

logger = logging.getLogger(__name__)

# Global state for background sync task
_background_sync_task: asyncio.Task | None = None
_last_model_sync: datetime | None = None


# FALLBACK — api_key_env_var now reads from DB providers table (Phase 2D).
# Kept as documented fallback for seed_providers.py and cold-start scenarios.
PROVIDER_ENV_VAR_MAP = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "vercel-ai-gateway": "VERCEL_AI_GATEWAY_KEY",
    "featherless": "FEATHERLESS_API_KEY",
    "chutes": "CHUTES_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "google-vertex": "GOOGLE_APPLICATION_CREDENTIALS",
    "cerebras": "CEREBRAS_API_KEY",
    "nebius": "NEBIUS_API_KEY",
    "xai": "XAI_API_KEY",
    "novita": "NOVITA_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "aimo": "AIMO_API_KEY",
    "near": "NEAR_API_KEY",
    "fal": "FAL_API_KEY",
    "helicone": "HELICONE_API_KEY",
    "alpaca": "ALPACA_NETWORK_API_KEY",
    "alibaba": "ALIBABA_CLOUD_API_KEY",
    "clarifai": "CLARIFAI_API_KEY",
    "onerouter": "ONEROUTER_API_KEY",
    "simplismart": "SIMPLISMART_API_KEY",
    "aihubmix": "AIHUBMIX_API_KEY",
    "anannas": "ANANNAS_API_KEY",
    "cloudflare-workers-ai": "CLOUDFLARE_WORKERS_AI_API_KEY",
}


async def sync_providers_to_database() -> dict[str, Any]:
    """No-op: DB is now the source of truth for provider data (Phase 2A).

    Previously this function read from GATEWAY_REGISTRY in catalog.py and
    upserted to the providers table. Now the DB providers table is maintained
    directly via migrations and admin APIs.
    """
    logger.info("Provider sync skipped: DB is source of truth (Phase 2A)")
    return {"success": True, "providers_synced": 0, "note": "DB is source of truth"}


async def sync_models_from_providers(
    provider_slugs: list[str] | None = None, batch_size: int = 5
) -> dict[str, Any]:
    """
    Sync models from provider APIs to database

    Args:
        provider_slugs: Specific providers to sync, or None for all
        batch_size: Number of providers to sync concurrently

    Returns:
        Sync result dictionary
    """
    global _last_model_sync

    try:
        if provider_slugs is None:
            # Sync all active providers with fetch functions (DB-first)
            try:
                from src.db.providers_db import get_active_provider_slugs
                from src.services.gateway_registry import get_gateway_registry

                provider_slugs = get_active_provider_slugs()
                registry = get_gateway_registry()
                provider_slugs = [
                    s for s in provider_slugs if registry.get(s, {}).get("has_fetch_function", True)
                ]
                if not provider_slugs:
                    logger.warning(
                        "DB returned zero fetchable providers; falling back to hardcoded list"
                    )
                    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

                    provider_slugs = list(PROVIDER_FETCH_FUNCTIONS.keys())
            except Exception:
                logger.warning("Failed to load providers from DB; using hardcoded fallback")
                from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

                provider_slugs = list(PROVIDER_FETCH_FUNCTIONS.keys())

        logger.info(f"🔄 Starting model sync for {len(provider_slugs)} providers")

        total_models_synced = 0
        providers_synced = 0
        errors = []

        # Process providers in batches to avoid overwhelming the system
        for i in range(0, len(provider_slugs), batch_size):
            batch = provider_slugs[i : i + batch_size]
            logger.info(f"Processing batch {i // batch_size + 1}: {', '.join(batch)}")

            # Sync each provider in the batch sequentially
            # (Can be made concurrent with asyncio.gather if needed)
            for provider_slug in batch:
                try:
                    result = await asyncio.to_thread(
                        sync_provider_models, provider_slug, dry_run=False
                    )

                    if result["success"]:
                        models_count = result.get("models_synced", 0)
                        total_models_synced += models_count
                        providers_synced += 1
                        logger.info(f"  ✅ {provider_slug}: {models_count} models synced")
                    else:
                        error_msg = result.get("error", "Unknown error")
                        errors.append(f"{provider_slug}: {error_msg}")
                        logger.warning(f"  ⚠️  {provider_slug}: {error_msg}")

                except Exception as e:
                    errors.append(f"{provider_slug}: {str(e)}")
                    logger.error(f"  ❌ {provider_slug}: {e}")

            # Small delay between batches
            if i + batch_size < len(provider_slugs):
                await asyncio.sleep(1)

        _last_model_sync = datetime.now(UTC)

        logger.info(
            f"✅ Model sync complete: {providers_synced}/{len(provider_slugs)} providers, "
            f"{total_models_synced} total models"
        )

        return {
            "success": True,
            "providers_synced": providers_synced,
            "total_providers": len(provider_slugs),
            "total_models_synced": total_models_synced,
            "errors": errors,
            "synced_at": _last_model_sync.isoformat(),
        }

    except Exception as e:
        logger.error(f"❌ Model sync failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "providers_synced": 0, "total_models_synced": 0}


async def sync_providers_on_startup() -> dict[str, Any]:
    """
    Sync providers on application startup

    This is called during lifespan startup to ensure providers
    are always up-to-date with the DB providers table.

    Returns:
        Sync result dictionary
    """
    logger.info("🚀 Starting provider sync on startup")
    result = await sync_providers_to_database()

    if result["success"]:
        logger.info("✅ Startup provider sync completed successfully")
    else:
        logger.error("❌ Startup provider sync failed (app will continue)")

    return result


async def sync_initial_models_on_startup(
    high_priority_providers: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sync critical models on startup (optional, can be heavy)

    Only syncs high-priority providers to avoid delaying startup.
    Full sync runs periodically via background task.

    Args:
        high_priority_providers: List of critical providers to sync immediately

    Returns:
        Sync result dictionary
    """
    if high_priority_providers is None:
        # Default high-priority providers (fast, commonly used)
        high_priority_providers = [
            "openrouter",  # Aggregator with many models
            "openai",  # GPT models
            "anthropic",  # Claude models
            "groq",  # Fast inference
        ]

    logger.info(
        f"🚀 Starting initial model sync for {len(high_priority_providers)} high-priority providers"
    )

    result = await sync_models_from_providers(
        provider_slugs=high_priority_providers, batch_size=2  # Smaller batch for startup
    )

    if result["success"]:
        logger.info("✅ Startup model sync completed")
    else:
        logger.warning("⚠️  Startup model sync had errors (full sync will run periodically)")

    return result


async def periodic_model_sync_task(interval_hours: int = 6):
    """
    Background task that periodically syncs models from all providers

    Args:
        interval_hours: Hours between syncs (default: 6 hours)
    """
    global _last_model_sync

    logger.info(f"📅 Starting periodic model sync task (every {interval_hours} hours)")

    while True:
        try:
            # Wait for the specified interval
            await asyncio.sleep(interval_hours * 3600)

            logger.info("⏰ Starting scheduled model sync")
            result = await sync_models_from_providers()

            if result["success"]:
                logger.info(
                    f"✅ Scheduled sync completed: {result['total_models_synced']} models "
                    f"from {result['providers_synced']} providers"
                )
            else:
                logger.error(f"❌ Scheduled sync failed: {result.get('error')}")

        except asyncio.CancelledError:
            logger.info("📅 Periodic model sync task cancelled")
            break
        except Exception as e:
            logger.error(f"❌ Error in periodic sync task: {e}", exc_info=True)
            # Continue running despite errors
            await asyncio.sleep(300)  # Wait 5 minutes before retry


async def start_background_model_sync(interval_hours: int = 6):
    """
    Start background model sync task

    Args:
        interval_hours: Hours between syncs (default: 6 hours)
    """
    global _background_sync_task

    if _background_sync_task is not None:
        logger.warning("Background sync task already running")
        return

    _background_sync_task = asyncio.create_task(
        periodic_model_sync_task(interval_hours), name="periodic_model_sync"
    )

    logger.info(f"✅ Background model sync task started (interval: {interval_hours}h)")


async def stop_background_model_sync():
    """Stop background model sync task"""
    global _background_sync_task

    if _background_sync_task is not None:
        _background_sync_task.cancel()
        try:
            await _background_sync_task
        except asyncio.CancelledError:
            pass
        _background_sync_task = None
        logger.info("✅ Background model sync task stopped")


async def trigger_full_sync() -> dict[str, Any]:
    """
    Manually trigger a full sync of providers and models

    Returns:
        Combined sync results
    """
    logger.info("🔄 Starting manual full sync")

    # Sync providers first
    provider_result = await sync_providers_to_database()

    # Then sync all models
    model_result = await sync_models_from_providers()

    return {
        "success": provider_result["success"] and model_result["success"],
        "providers": provider_result,
        "models": model_result,
    }


def get_last_model_sync() -> datetime | None:
    """Get timestamp of last successful model sync"""
    return _last_model_sync
