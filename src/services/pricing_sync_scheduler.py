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

    This function starts a background task that runs pricing sync
    at regular intervals.
    """
    global _scheduler_task

    if _scheduler_task is not None:
        logger.warning("Pricing sync scheduler already running")
        return

    _scheduler_task = asyncio.create_task(
        _pricing_sync_scheduler_loop(),
        name="pricing_sync_scheduler_loop"
    )

    logger.info("✅ Pricing sync scheduler task created")


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
