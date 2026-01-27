"""
Pricing Sync Scheduler - Phase 2.5

Automated background task that periodically syncs pricing from provider APIs
to the database.

Features:
- Runs every N hours (configurable via PRICING_SYNC_INTERVAL_HOURS)
- Can be enabled/disabled via PRICING_SYNC_ENABLED
- Comprehensive error handling and logging
- Metrics exported for monitoring
- Graceful shutdown
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Prometheus metrics
scheduled_sync_runs = Counter(
    "pricing_scheduled_sync_runs_total",
    "Total scheduled sync runs",
    ["status"]  # success, failed
)

scheduled_sync_duration = Histogram(
    "pricing_scheduled_sync_duration_seconds",
    "Duration of scheduled sync runs in seconds"
)

last_sync_timestamp = Gauge(
    "pricing_last_sync_timestamp",
    "Timestamp of last successful sync",
    ["provider"]
)

models_synced_total = Counter(
    "pricing_models_synced_total",
    "Total models synced",
    ["provider", "status"]  # updated, skipped, unchanged
)

# Global task reference for graceful shutdown
_scheduler_task: asyncio.Task | None = None
_shutdown_event = asyncio.Event()


async def start_pricing_sync_scheduler() -> None:
    """
    Start the automated pricing sync scheduler.

    This function starts background tasks for:
    1. Pricing sync at regular intervals
    2. Cleanup of stuck syncs every 15 minutes
    """
    global _scheduler_task

    if _scheduler_task is not None:
        logger.warning("Pricing sync scheduler already running")
        return

    # Start pricing sync scheduler
    _scheduler_task = asyncio.create_task(
        _pricing_sync_scheduler_loop(),
        name="pricing_sync_scheduler_loop"
    )

    # Start cleanup scheduler
    asyncio.create_task(
        _cleanup_scheduler_loop(),
        name="pricing_sync_cleanup_loop"
    )

    logger.info("✅ Pricing sync scheduler and cleanup tasks created")


async def stop_pricing_sync_scheduler() -> None:
    """
    Stop the automated pricing sync scheduler gracefully.
    """
    global _scheduler_task

    if _scheduler_task is None:
        return

    logger.info("Stopping pricing sync scheduler...")
    _shutdown_event.set()

    # Wait for graceful shutdown (max 30 seconds)
    try:
        await asyncio.wait_for(_scheduler_task, timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Pricing sync scheduler did not stop gracefully, cancelling...")
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass

    _scheduler_task = None
    _shutdown_event.clear()
    logger.info("✅ Pricing sync scheduler stopped")


async def _pricing_sync_scheduler_loop() -> None:
    """
    Main scheduler loop that runs pricing sync at regular intervals.
    """
    from src.config.config import Config

    interval_hours = Config.PRICING_SYNC_INTERVAL_HOURS
    interval_seconds = interval_hours * 3600

    logger.info(
        f"📅 Pricing sync scheduler started "
        f"(interval: {interval_hours}h = {interval_seconds}s)"
    )

    # Run initial sync after a short delay (30 seconds)
    # This allows the app to fully initialize first
    try:
        await asyncio.wait_for(
            _shutdown_event.wait(),
            timeout=30.0
        )
        # If wait succeeds, shutdown was requested
        logger.info("Scheduler shutdown requested before first sync")
        return
    except asyncio.TimeoutError:
        # Timeout is expected - continue with first sync
        pass

    while not _shutdown_event.is_set():
        try:
            logger.info("🔄 Starting scheduled pricing sync...")
            start_time = time.time()

            # Run the sync
            result = await _run_scheduled_sync()

            # Record duration
            duration = time.time() - start_time
            scheduled_sync_duration.observe(duration)

            # Log result
            if result["status"] == "success":
                scheduled_sync_runs.labels(status="success").inc()

                logger.info(
                    f"✅ Scheduled pricing sync completed successfully "
                    f"(duration: {duration:.2f}s, "
                    f"updated: {result.get('total_models_updated', 0)}, "
                    f"errors: {result.get('total_errors', 0)})"
                )

                # Update last sync timestamp for each provider
                for provider, provider_result in result.get("results", {}).items():
                    if provider_result.get("status") == "success":
                        last_sync_timestamp.labels(provider=provider).set(time.time())

                        # Update provider-specific metrics
                        models_synced_total.labels(
                            provider=provider,
                            status="updated"
                        ).inc(provider_result.get("models_updated", 0))

                        models_synced_total.labels(
                            provider=provider,
                            status="skipped"
                        ).inc(provider_result.get("models_skipped", 0))

                        models_synced_total.labels(
                            provider=provider,
                            status="unchanged"
                        ).inc(provider_result.get("models_unchanged", 0))

            else:
                scheduled_sync_runs.labels(status="failed").inc()

                logger.error(
                    f"❌ Scheduled pricing sync failed "
                    f"(duration: {duration:.2f}s): {result.get('error_message')}"
                )

            # Wait for next interval or shutdown
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=interval_seconds
                )
                # If wait succeeds, shutdown was requested
                logger.info("Scheduler shutdown requested")
                break
            except asyncio.TimeoutError:
                # Timeout is expected - continue with next iteration
                continue

        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")
            raise
        except Exception as e:
            scheduled_sync_runs.labels(status="failed").inc()
            logger.error(f"❌ Error in pricing sync scheduler: {e}", exc_info=True)

            # Send alert to Sentry
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(e)
            except Exception:
                pass

            # Wait before retrying (shorter interval on error)
            retry_delay = min(interval_seconds, 3600)  # Max 1 hour retry
            logger.info(f"Retrying in {retry_delay}s...")

            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=retry_delay
                )
                logger.info("Scheduler shutdown requested during retry wait")
                break
            except asyncio.TimeoutError:
                continue

    logger.info("📅 Pricing sync scheduler loop exited")


async def _run_scheduled_sync() -> Dict[str, Any]:
    """
    Run scheduled pricing sync for all configured providers.

    Returns:
        Sync result dictionary
    """
    try:
        from src.services.pricing_sync_service import run_scheduled_sync

        result = await run_scheduled_sync(triggered_by="scheduler")

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_models_updated": result.get("total_models_updated", 0),
            "total_models_skipped": result.get("total_models_skipped", 0),
            "total_errors": result.get("total_errors", 0),
            "results": result.get("results", {})
        }

    except Exception as e:
        logger.error(f"Scheduled sync execution failed: {e}", exc_info=True)

        return {
            "status": "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_message": str(e)
        }


async def trigger_manual_sync() -> Dict[str, Any]:
    """
    Trigger a manual pricing sync outside the regular schedule.

    This is useful for admin endpoints or emergency syncs.

    Returns:
        Sync result dictionary
    """
    logger.info("🔄 Manual pricing sync triggered")
    start_time = time.time()

    try:
        from src.services.pricing_sync_service import run_scheduled_sync

        result = await run_scheduled_sync(triggered_by="manual")

        duration = time.time() - start_time

        logger.info(
            f"✅ Manual pricing sync completed "
            f"(duration: {duration:.2f}s, "
            f"updated: {result.get('total_models_updated', 0)})"
        )

        return {
            "status": "success",
            "duration_seconds": duration,
            **result
        }

    except Exception as e:
        duration = time.time() - start_time

        logger.error(f"❌ Manual pricing sync failed (duration: {duration:.2f}s): {e}", exc_info=True)

        return {
            "status": "failed",
            "duration_seconds": duration,
            "error_message": str(e)
        }


def get_scheduler_status() -> Dict[str, Any]:
    """
    Get current status of the pricing sync scheduler.

    Returns:
        Status dictionary with scheduler state and last sync info
    """
    global _scheduler_task

    from src.config.config import Config

    status = {
        "enabled": Config.PRICING_SYNC_ENABLED,
        "interval_hours": Config.PRICING_SYNC_INTERVAL_HOURS,
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "providers": Config.PRICING_SYNC_PROVIDERS
    }

    # Get last sync timestamps from metrics
    try:
        last_syncs = {}
        for provider in Config.PRICING_SYNC_PROVIDERS:
            metric_value = last_sync_timestamp.labels(provider=provider)._value.get()
            if metric_value and metric_value > 0:
                last_syncs[provider] = {
                    "timestamp": datetime.fromtimestamp(metric_value, tz=timezone.utc).isoformat(),
                    "seconds_ago": int(time.time() - metric_value)
                }

        if last_syncs:
            status["last_syncs"] = last_syncs
    except Exception as e:
        logger.debug(f"Could not get last sync metrics: {e}")

    return status


# ============================================================================
# Background Async Queue Functions (Fix for Railway 55s timeout)
# ============================================================================

async def queue_background_sync(triggered_by: str = "manual") -> str:
    """
    Queue a pricing sync to run in the background.

    This function returns immediately with a sync job ID,
    allowing the HTTP request to complete without waiting
    for the sync to finish. This solves the Railway 55-second timeout.

    Args:
        triggered_by: Who/what triggered the sync

    Returns:
        Sync job ID for status polling
    """
    from uuid import uuid4
    from src.config.supabase_config import get_supabase_client

    sync_id = str(uuid4())
    supabase = get_supabase_client()

    logger.info(f"Queueing background sync {sync_id} (triggered_by={triggered_by})")

    try:
        # Create sync job record immediately with status 'queued'
        supabase.table('pricing_sync_jobs').insert({
            'job_id': sync_id,
            'status': 'queued',
            'triggered_by': triggered_by,
            'triggered_at': datetime.now(timezone.utc).isoformat(),
            'providers_synced': 0,
            'models_updated': 0,
            'models_skipped': 0,
            'total_errors': 0
        }).execute()

        # Queue the actual sync work in background
        asyncio.create_task(
            _run_background_sync_with_error_handling(sync_id, triggered_by),
            name=f"pricing_sync_{sync_id}"
        )

        logger.info(f"✅ Background sync {sync_id} queued successfully")
        return sync_id

    except Exception as e:
        logger.error(f"Failed to queue background sync: {e}", exc_info=True)
        raise


async def _run_background_sync_with_error_handling(sync_id: str, triggered_by: str) -> None:
    """
    Run the pricing sync in background with comprehensive error handling.

    This ensures the sync status is ALWAYS updated, even if errors occur.
    This prevents stuck syncs.
    """
    from src.config.supabase_config import get_supabase_client
    from src.services.pricing_sync_service import run_scheduled_sync

    supabase = get_supabase_client()
    start_time = time.time()

    try:
        # Update status to running
        supabase.table('pricing_sync_jobs').update({
            'status': 'running',
            'started_at': datetime.now(timezone.utc).isoformat()
        }).eq('job_id', sync_id).execute()

        logger.info(f"🔄 Background sync {sync_id} started")

        # Run the actual sync
        result = await run_scheduled_sync(triggered_by=triggered_by)

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Update status to completed
        supabase.table('pricing_sync_jobs').update({
            'status': 'completed',
            'completed_at': datetime.now(timezone.utc).isoformat(),
            'providers_synced': result.get('providers_synced', 0),
            'models_updated': result.get('total_models_updated', 0),
            'models_skipped': result.get('total_models_skipped', 0),
            'total_errors': result.get('total_errors', 0),
            'error_message': None,
            'result_data': result
        }).eq('job_id', sync_id).execute()

        scheduled_sync_runs.labels(status="success").inc()

        logger.info(
            f"✅ Background sync {sync_id} completed successfully "
            f"(duration: {duration_ms}ms, updated: {result.get('total_models_updated', 0)} models)"
        )

    except Exception as e:
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        logger.error(f"❌ Background sync {sync_id} failed: {e}", exc_info=True)

        scheduled_sync_runs.labels(status="failed").inc()

        # CRITICAL: Always update status even on error (prevents stuck syncs)
        try:
            supabase.table('pricing_sync_jobs').update({
                'status': 'failed',
                'completed_at': datetime.now(timezone.utc).isoformat(),
                'error_message': str(e)[:500]  # Limit error message length
            }).eq('job_id', sync_id).execute()
        except Exception as update_error:
            logger.error(
                f"CRITICAL: Failed to update sync status for {sync_id}: {update_error}",
                exc_info=True
            )


async def get_sync_job_status(sync_id: str) -> Dict[str, Any] | None:
    """
    Get the status of a pricing sync job.

    Args:
        sync_id: The sync job ID

    Returns:
        Sync status dict or None if not found
    """
    from src.config.supabase_config import get_supabase_client

    supabase = get_supabase_client()

    try:
        response = supabase.table('pricing_sync_jobs').select('*').eq('job_id', sync_id).execute()

        if not response.data:
            return None

        job = response.data[0]

        # Calculate progress percentage
        progress = 0
        if job['status'] == 'queued':
            progress = 0
        elif job['status'] == 'running':
            progress = 50  # Rough estimate
        elif job['status'] == 'completed':
            progress = 100
        elif job['status'] == 'failed':
            progress = 0

        return {
            'sync_id': sync_id,
            'job_id': job['job_id'],
            'status': job['status'],
            'triggered_at': job['triggered_at'],
            'started_at': job.get('started_at'),
            'completed_at': job.get('completed_at'),
            'duration_seconds': job.get('duration_seconds'),
            'triggered_by': job['triggered_by'],
            'providers_synced': job.get('providers_synced', 0),
            'models_updated': job.get('models_updated', 0),
            'models_skipped': job.get('models_skipped', 0),
            'total_errors': job.get('total_errors', 0),
            'error_message': job.get('error_message'),
            'result_data': job.get('result_data'),
            'progress_percent': progress
        }

    except Exception as e:
        logger.error(f"Error getting sync status for {sync_id}: {e}")
        return None


async def get_active_syncs() -> list:
    """
    Get all active (queued or in_progress) pricing syncs.

    Returns:
        List of active sync status dicts
    """
    from src.config.supabase_config import get_supabase_client

    supabase = get_supabase_client()

    try:
        response = (
            supabase.table('pricing_sync_jobs')
            .select('*')
            .in_('status', ['queued', 'running'])
            .order('triggered_at', desc=True)
            .execute()
        )

        return [
            {
                'sync_id': job['job_id'],
                'job_id': job['job_id'],
                'status': job['status'],
                'triggered_at': job['triggered_at'],
                'started_at': job.get('started_at'),
                'triggered_by': job['triggered_by'],
                'progress_percent': 0 if job['status'] == 'queued' else 50
            }
            for job in response.data
        ]

    except Exception as e:
        logger.error(f"Error getting active syncs: {e}")
        return []


async def _cleanup_scheduler_loop() -> None:
    """
    Run cleanup every 15 minutes to catch and clean stuck syncs.

    This prevents stuck syncs from polluting the database and ensures
    all sync records eventually get a final status.
    """
    cleanup_interval_seconds = 900  # 15 minutes

    logger.info(
        f"🧹 Pricing sync cleanup scheduler started "
        f"(interval: {cleanup_interval_seconds}s = 15 minutes)"
    )

    # Run first cleanup after 5 minutes (allow some syncs to complete)
    try:
        await asyncio.wait_for(
            _shutdown_event.wait(),
            timeout=300.0  # 5 minutes
        )
        logger.info("Cleanup scheduler shutdown requested before first run")
        return
    except asyncio.TimeoutError:
        pass

    while not _shutdown_event.is_set():
        try:
            logger.info("🧹 Running scheduled cleanup for stuck pricing syncs...")

            from src.services.pricing_sync_cleanup import cleanup_stuck_syncs

            result = await cleanup_stuck_syncs(timeout_minutes=10)

            logger.info(
                f"✅ Scheduled cleanup complete: "
                f"found {result['stuck_syncs_found']}, "
                f"cleaned {result['syncs_cleaned']}"
            )

            # Wait for next interval or shutdown
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=cleanup_interval_seconds
                )
                logger.info("Cleanup scheduler shutdown requested")
                break
            except asyncio.TimeoutError:
                continue

        except asyncio.CancelledError:
            logger.info("Cleanup scheduler loop cancelled")
            raise
        except Exception as e:
            logger.error(f"❌ Error in cleanup scheduler: {e}", exc_info=True)

            # Wait before retrying
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=60.0  # Wait 1 minute on error
                )
                break
            except asyncio.TimeoutError:
                continue

    logger.info("🧹 Pricing sync cleanup scheduler stopped")
