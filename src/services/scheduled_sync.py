"""
Scheduled Model Sync Service (Phase 3 - Issue #996)

Provides background job to sync models from provider APIs to database
at regular intervals, keeping the database fresh for DB-first architecture.

Features:
- APScheduler-based job scheduling
- Configurable sync interval
- Error handling with logging
- Graceful shutdown
- Health monitoring integration
"""

import asyncio
import logging
from datetime import datetime, UTC
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config.config import Config

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None

# Track last sync status for health monitoring
_last_sync_status: dict[str, Any] = {
    "last_run_time": None,
    "last_success_time": None,
    "last_error": None,
    "total_runs": 0,
    "successful_runs": 0,
    "failed_runs": 0,
    "last_duration_seconds": None,
    "last_models_synced": 0,
}


async def warm_caches_after_sync(changed_providers: list[str]) -> None:
    """
    Proactively warm caches after a successful incremental sync.

    Runs as a fire-and-forget background task so no user pays the
    full cache rebuild cost on the first request after sync.
    Failures are non-fatal — logged as warnings only.
    """
    from src.services.model_catalog_cache import (
        get_cached_full_catalog,
        warm_unique_models_cache_all_variants,
        cache_catalog_stats,
    )
    from src.db.models_catalog_db import get_models_stats

    logger.info(
        f"Cache warming started after sync "
        f"(providers with changes: {changed_providers})"
    )

    # Brief delay to let DB writes propagate
    await asyncio.sleep(2)

    # Phase 1: Full catalog
    try:
        catalog = await asyncio.to_thread(get_cached_full_catalog)
        model_count = len(catalog) if catalog else 0
        logger.info(f"Cache warm [1/3]: Full catalog warmed ({model_count} models)")
    except Exception as e:
        logger.warning(f"Cache warm [1/3]: Full catalog warming failed (non-fatal): {e}")

    # Phase 2: Unique models (all filter/sort variants)
    try:
        warm_stats = await warm_unique_models_cache_all_variants()
        logger.info(
            f"Cache warm [2/3]: Unique models warmed "
            f"({warm_stats.get('successful', 0)}/{warm_stats.get('total_variants', 0)} variants)"
        )
    except Exception as e:
        logger.warning(f"Cache warm [2/3]: Unique models warming failed (non-fatal): {e}")

    # Phase 3: Catalog stats
    try:
        stats = await asyncio.to_thread(get_models_stats)
        if stats:
            cache_catalog_stats(stats)
            logger.info("Cache warm [3/3]: Catalog stats warmed")
        else:
            logger.warning("Cache warm [3/3]: get_models_stats returned empty")
    except Exception as e:
        logger.warning(f"Cache warm [3/3]: Catalog stats warming failed (non-fatal): {e}")

    logger.info("Cache warming complete after sync")


async def run_scheduled_model_sync():
    """
    Run the scheduled model sync job with incremental change detection.

    This function is called by APScheduler at the configured interval.
    It syncs all providers to the database using efficient incremental sync:
    - Fetches models from ALL providers
    - Compares with DB using content hashing
    - Only writes changed/new models
    - Only invalidates cache for providers with changes

    This minimizes DB writes and cache invalidation overhead.
    """
    from src.services.incremental_sync import sync_all_providers_incremental

    start_time = datetime.now(UTC)
    _last_sync_status["last_run_time"] = start_time
    _last_sync_status["total_runs"] += 1

    logger.info("=" * 80)
    logger.info("Starting scheduled incremental model sync")
    logger.info("=" * 80)

    try:
        # Run incremental sync in background thread to avoid blocking event loop
        # This uses content-based change detection to minimize DB writes
        result = await asyncio.to_thread(sync_all_providers_incremental, dry_run=False)

        # Calculate duration
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        if result.get("success"):
            # Success!
            _last_sync_status["successful_runs"] += 1
            _last_sync_status["last_success_time"] = end_time
            _last_sync_status["last_error"] = None
            _last_sync_status["last_duration_seconds"] = duration
            _last_sync_status["last_models_synced"] = result.get(
                "total_models_synced", 0
            )

            logger.info("=" * 80)
            logger.info("✅ Scheduled incremental sync SUCCESSFUL")
            logger.info(f"   Duration: {duration:.2f}s")
            logger.info(f"   Models fetched: {result.get('total_models_fetched', 0):,}")
            logger.info(f"   Models changed: {result.get('total_models_changed', 0):,}")
            logger.info(f"   Models synced: {result.get('total_models_synced', 0):,}")
            logger.info(f"   Change rate: {result.get('change_rate_percent', 0):.1f}%")
            logger.info(f"   Efficiency gain: {result.get('efficiency_gain_percent', 0):.1f}%")
            logger.info(
                f"   Providers synced: {result.get('providers_synced', 0)}/{result.get('total_providers', 0)}"
            )
            logger.info(f"   Providers with changes: {result.get('providers_with_changes', 0)}")
            logger.info("=" * 80)

            # Proactively warm caches so no user pays the rebuild cost
            if result.get("providers_with_changes", 0) > 0:
                changed = result.get("changed_providers", [])
                asyncio.create_task(
                    warm_caches_after_sync(changed),
                    name="post_sync_cache_warm",
                )
                logger.info(
                    f"Cache warming task queued for {len(changed)} changed providers"
                )

        else:
            # Failed
            _last_sync_status["failed_runs"] += 1
            error_msg = result.get("error", "Unknown error")
            _last_sync_status["last_error"] = error_msg
            _last_sync_status["last_duration_seconds"] = duration

            logger.error("=" * 80)
            logger.error("❌ Scheduled model sync FAILED")
            logger.error(f"   Duration: {duration:.2f}s")
            logger.error(f"   Error: {error_msg}")
            logger.error("=" * 80)

    except Exception as e:
        # Unexpected error
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        _last_sync_status["failed_runs"] += 1
        _last_sync_status["last_error"] = str(e)
        _last_sync_status["last_duration_seconds"] = duration

        logger.exception("=" * 80)
        logger.exception("❌ Scheduled model sync EXCEPTION")
        logger.exception(f"   Duration: {duration:.2f}s")
        logger.exception(f"   Error: {e}")
        logger.exception("=" * 80)


def start_scheduler():
    """
    Start the APScheduler for scheduled model sync.

    Called during application startup (in app lifespan).
    Only starts if ENABLE_SCHEDULED_MODEL_SYNC is enabled.
    """
    global _scheduler

    # Check if scheduled sync is enabled
    if not Config.ENABLE_SCHEDULED_MODEL_SYNC:
        logger.info("Scheduled model sync DISABLED: ENABLE_SCHEDULED_MODEL_SYNC=false")
        return

    # Get sync interval
    interval_minutes = Config.MODEL_SYNC_INTERVAL_MINUTES

    logger.info("=" * 80)
    logger.info("🚀 Starting Scheduled Model Sync Service")
    logger.info("=" * 80)
    logger.info(f"   Interval: {interval_minutes} minutes")
    logger.info("=" * 80)

    try:
        # Create scheduler
        _scheduler = AsyncIOScheduler()

        # Add the sync job
        _scheduler.add_job(
            run_scheduled_model_sync,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="model_sync",
            name="Model Sync Job",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
            coalesce=True,  # Combine missed runs
        )

        # Start the scheduler
        _scheduler.start()

        logger.info("✅ Scheduled model sync service started successfully")
        logger.info(f"   Next sync in {interval_minutes} minutes")

    except Exception as e:
        logger.error(f"❌ Failed to start scheduled model sync service: {e}")
        logger.exception(e)


def stop_scheduler():
    """
    Stop the APScheduler gracefully.

    Called during application shutdown (in app lifespan).
    """
    global _scheduler

    if _scheduler is None:
        return

    logger.info("Stopping scheduled model sync service...")

    try:
        _scheduler.shutdown(wait=True)
        logger.info("✅ Scheduled model sync service stopped successfully")
    except Exception as e:
        logger.error(f"❌ Error stopping scheduled model sync service: {e}")
    finally:
        _scheduler = None


def get_sync_status() -> dict[str, Any]:
    """
    Get the current status of scheduled sync (for health monitoring).

    Returns:
        Dictionary with sync status metrics

    Example:
        >>> status = get_sync_status()
        >>> print(f"Last sync: {status['last_success_time']}")
        >>> print(f"Success rate: {status['success_rate']:.1f}%")
    """
    # Calculate success rate
    total_runs = _last_sync_status["total_runs"]
    successful_runs = _last_sync_status["successful_runs"]
    success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0

    # Calculate time since last sync
    last_success = _last_sync_status["last_success_time"]
    minutes_since_last_sync = None
    if last_success:
        delta = datetime.now(UTC) - last_success
        minutes_since_last_sync = delta.total_seconds() / 60

    # Determine health status
    is_healthy = True
    health_reason = "Healthy"

    if total_runs == 0:
        is_healthy = False
        health_reason = "No syncs run yet"
    elif minutes_since_last_sync and minutes_since_last_sync > Config.MODEL_SYNC_INTERVAL_MINUTES * 2:
        is_healthy = False
        health_reason = f"Last successful sync {minutes_since_last_sync:.0f} minutes ago (expected every {Config.MODEL_SYNC_INTERVAL_MINUTES} minutes)"
    elif success_rate < 50:
        is_healthy = False
        health_reason = f"Low success rate: {success_rate:.1f}%"

    return {
        # Status
        "is_healthy": is_healthy,
        "health_reason": health_reason,
        "enabled": Config.ENABLE_SCHEDULED_MODEL_SYNC,

        # Times
        "last_run_time": _last_sync_status["last_run_time"].isoformat() if _last_sync_status["last_run_time"] else None,
        "last_success_time": _last_sync_status["last_success_time"].isoformat() if _last_sync_status["last_success_time"] else None,
        "minutes_since_last_sync": round(minutes_since_last_sync, 1) if minutes_since_last_sync else None,

        # Counts
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "failed_runs": _last_sync_status["failed_runs"],
        "success_rate": round(success_rate, 1),

        # Last run details
        "last_error": _last_sync_status["last_error"],
        "last_duration_seconds": _last_sync_status["last_duration_seconds"],
        "last_models_synced": _last_sync_status["last_models_synced"],

        # Config
        "sync_interval_minutes": Config.MODEL_SYNC_INTERVAL_MINUTES,
    }


def trigger_manual_sync() -> dict[str, Any]:
    """
    Manually trigger a sync job (for admin endpoints).

    Returns:
        Status of the manual sync
    """
    logger.info("Manual sync triggered via API")

    # Run sync synchronously
    loop = asyncio.get_event_loop()
    loop.create_task(run_scheduled_model_sync())

    return {
        "success": True,
        "message": "Manual sync triggered - check logs for progress",
        "timestamp": datetime.now(UTC).isoformat(),
    }
