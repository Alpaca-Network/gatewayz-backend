"""Hourly GPU utilization rollup scheduler (gatewayz-backend#2263 #2264,
spec §6). Computes the previous full UTC hour from provider_work into
gpu_utilization_hourly, and backfills the last 7 days the first time it
finds the table empty (fresh deploy / fresh migration).

Mirrors the AsyncIOScheduler start/stop pattern in
src/services/scheduled_sync.py (start_scheduler/stop_scheduler) exactly,
including max_instances=1 + coalesce=True so a slow run never overlaps
itself and a missed tick doesn't pile up duplicate runs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.db.gpu_rollups import aggregate_hour, is_utilization_empty, upsert_hourly_rows

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

_BACKFILL_HOURS = 24 * 7


def _run_hour(hour: datetime) -> None:
    rows = aggregate_hour(hour)
    if not rows:
        logger.debug(f"gpu_utilization_hourly: no provider_work rows for hour={hour.isoformat()}")
        return
    if not upsert_hourly_rows(rows):
        logger.warning(f"gpu_utilization_hourly: upsert failed for hour={hour.isoformat()}")


def run_hourly_rollup() -> None:
    """Compute the previous full UTC hour. Backfills the last
    _BACKFILL_HOURS (7 days) first if the table is empty (first run after
    the migration lands, or a fresh environment).
    """
    now = datetime.now(UTC)
    previous_hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    if is_utilization_empty():
        logger.info(
            f"gpu_utilization_hourly is empty; backfilling the last {_BACKFILL_HOURS} hours"
        )
        # previous_hour itself is computed by the unconditional _run_hour()
        # call below -- backfill only the _BACKFILL_HOURS-1 hours before it,
        # so the total distinct hours computed is exactly _BACKFILL_HOURS,
        # not _BACKFILL_HOURS+1 with previous_hour recomputed twice.
        oldest = previous_hour - timedelta(hours=_BACKFILL_HOURS - 1)
        for i in range(_BACKFILL_HOURS - 1):
            _run_hour(oldest + timedelta(hours=i))

    _run_hour(previous_hour)


def start_gpu_rollup_scheduler() -> None:
    """Start the hourly rollup job. Called during application startup
    (src/services/startup.py's lifespan). A failure here is logged and
    swallowed by the caller -- must never fail app startup.
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("GPU utilization rollup scheduler already running")
        return

    try:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            run_hourly_rollup,
            trigger=IntervalTrigger(hours=1),
            id="gpu_utilization_hourly_rollup",
            name="GPU Utilization Hourly Rollup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _scheduler.start()
        logger.info("GPU utilization rollup scheduler started (interval: 1h)")
    except Exception as e:
        logger.error(f"Failed to start GPU utilization rollup scheduler: {e}")


def stop_gpu_rollup_scheduler() -> None:
    """Stop the scheduler gracefully. Called during application shutdown."""
    global _scheduler

    if _scheduler is None:
        return

    logger.info("Stopping GPU utilization rollup scheduler...")
    try:
        _scheduler.shutdown(wait=True)
        logger.info("GPU utilization rollup scheduler stopped")
    except Exception as e:
        logger.error(f"Error stopping GPU utilization rollup scheduler: {e}")
    finally:
        _scheduler = None
