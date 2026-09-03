"""Tests for the GPU node liveness sweep scheduler (Milestone 4 W-A1,
gatewayz-backend#2262), wired the same way as data retention cleanup."""

from unittest.mock import patch

import pytest

import src.services.scheduled_sync as scheduled_sync
from src.services.scheduled_sync import (
    run_gpu_liveness_sweep,
    start_gpu_liveness_scheduler,
    stop_gpu_liveness_scheduler,
)


@pytest.mark.asyncio
async def test_run_gpu_liveness_sweep_calls_db_sweep_and_records_status():
    with patch("src.db.gpu.sweep_liveness", return_value=(2, 1)) as mock_sweep:
        n_degraded, n_offline = await run_gpu_liveness_sweep()

    mock_sweep.assert_called_once()
    assert n_degraded == 2
    assert n_offline == 1
    assert scheduled_sync._last_gpu_liveness_status["last_degraded"] == 2
    assert scheduled_sync._last_gpu_liveness_status["last_offline"] == 1


@pytest.mark.asyncio
async def test_run_gpu_liveness_sweep_never_raises_on_failure():
    """A sweep failure must not crash the scheduler's job runner."""
    with patch("src.db.gpu.sweep_liveness", side_effect=RuntimeError("boom")):
        n_degraded, n_offline = await run_gpu_liveness_sweep()  # must not raise

    assert (n_degraded, n_offline) == (0, 0)


def test_start_and_stop_gpu_liveness_scheduler():
    try:
        start_gpu_liveness_scheduler()
        assert scheduled_sync._gpu_liveness_scheduler is not None
        assert scheduled_sync._gpu_liveness_scheduler.get_job("gpu_liveness_sweep") is not None
    finally:
        stop_gpu_liveness_scheduler()

    assert scheduled_sync._gpu_liveness_scheduler is None


def test_start_gpu_liveness_scheduler_uses_configured_interval(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "GPU_LIVENESS_SWEEP_INTERVAL_MINUTES", 5)
    try:
        start_gpu_liveness_scheduler()
        job = scheduled_sync._gpu_liveness_scheduler.get_job("gpu_liveness_sweep")
        assert job.trigger.interval.total_seconds() == 5 * 60
    finally:
        stop_gpu_liveness_scheduler()


def test_start_gpu_liveness_scheduler_failure_does_not_raise():
    """Matches the other schedulers' fail-soft behavior -- startup must not
    crash if APScheduler itself can't be started."""
    with patch(
        "src.services.scheduled_sync.AsyncIOScheduler", side_effect=RuntimeError("no event loop")
    ):
        start_gpu_liveness_scheduler()  # must not raise
    assert scheduled_sync._gpu_liveness_scheduler is None


def test_stop_gpu_liveness_scheduler_noop_when_not_started():
    scheduled_sync._gpu_liveness_scheduler = None
    stop_gpu_liveness_scheduler()  # must not raise
