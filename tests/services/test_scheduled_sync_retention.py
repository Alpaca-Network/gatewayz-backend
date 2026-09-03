"""Tests for the data retention scheduler (threat model L11): usage_records
and activity_log cleanup jobs, wired the same way as ledger reconciliation."""

from unittest.mock import patch

import pytest

import src.services.scheduled_sync as scheduled_sync
from src.services.scheduled_sync import (
    run_scheduled_retention_cleanup,
    start_retention_scheduler,
    stop_retention_scheduler,
)


@pytest.mark.asyncio
async def test_run_scheduled_retention_cleanup_calls_both_cleanups():
    with (
        patch("src.db.retention.cleanup_usage_records", return_value=12) as mock_usage,
        patch("src.db.retention.cleanup_activity_log", return_value=34) as mock_activity,
    ):
        await run_scheduled_retention_cleanup()

    mock_usage.assert_called_once()
    mock_activity.assert_called_once()
    assert scheduled_sync._last_retention_status["last_usage_records_deleted"] == 12
    assert scheduled_sync._last_retention_status["last_activity_log_deleted"] == 34


@pytest.mark.asyncio
async def test_run_scheduled_retention_cleanup_never_raises_on_failure():
    """A cleanup failure must not crash the scheduler's job runner."""
    with patch("src.db.retention.cleanup_usage_records", side_effect=RuntimeError("boom")):
        await run_scheduled_retention_cleanup()  # must not raise


def test_start_and_stop_retention_scheduler():
    try:
        start_retention_scheduler()
        assert scheduled_sync._retention_scheduler is not None
        assert scheduled_sync._retention_scheduler.get_job("retention_cleanup") is not None
    finally:
        stop_retention_scheduler()

    assert scheduled_sync._retention_scheduler is None


def test_start_retention_scheduler_uses_configured_interval(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "RETENTION_CLEANUP_INTERVAL_HOURS", 6)
    try:
        start_retention_scheduler()
        job = scheduled_sync._retention_scheduler.get_job("retention_cleanup")
        assert job.trigger.interval.total_seconds() == 6 * 3600
    finally:
        stop_retention_scheduler()


def test_start_retention_scheduler_failure_does_not_raise():
    """Matches the other schedulers' fail-soft behavior — startup must not crash
    if APScheduler itself can't be started."""
    with patch(
        "src.services.scheduled_sync.AsyncIOScheduler", side_effect=RuntimeError("no event loop")
    ):
        start_retention_scheduler()  # must not raise
    assert scheduled_sync._retention_scheduler is None


def test_stop_retention_scheduler_noop_when_not_started():
    scheduled_sync._retention_scheduler = None
    stop_retention_scheduler()  # must not raise
