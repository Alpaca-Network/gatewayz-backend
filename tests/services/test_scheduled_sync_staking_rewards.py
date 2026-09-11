"""Tests for the daily staking-rewards scheduler wiring in
src/services/scheduled_sync.py (gatewayz-backend staking rewards), wired
the same way as the GPU node liveness sweep
(tests/services/test_scheduled_sync_gpu_liveness.py)."""

from unittest.mock import patch

import pytest

import src.services.scheduled_sync as scheduled_sync
from src.services.scheduled_sync import (
    run_scheduled_staking_rewards,
    start_staking_rewards_scheduler,
    stop_staking_rewards_scheduler,
)
from src.services.staking_rewards import StakingRewardsStaleError


@pytest.mark.asyncio
async def test_run_scheduled_staking_rewards_records_success():
    result = {
        "reward_date": "2026-09-10",
        "wallets": 3,
        "paid": 2,
        "pending": 1,
        "skipped": 0,
        "credits_paid": "0.5",
        "capped": 0,
        "duration": 0.01,
    }
    with (
        patch("src.services.staking_rewards.run_staking_rewards_once", return_value=result),
        patch("src.services.scheduled_sync.record_job_run") as mock_record,
    ):
        await run_scheduled_staking_rewards()

    mock_record.assert_called_once()
    args, kwargs = mock_record.call_args
    assert args[0] == "staking_rewards"
    assert kwargs["ok"] is True
    assert kwargs["summary"] == result


@pytest.mark.asyncio
async def test_run_scheduled_staking_rewards_records_disabled_skip():
    with (
        patch(
            "src.services.staking_rewards.run_staking_rewards_once",
            return_value={"skipped": "disabled"},
        ),
        patch("src.services.scheduled_sync.record_job_run") as mock_record,
    ):
        await run_scheduled_staking_rewards()

    kwargs = mock_record.call_args.kwargs
    assert mock_record.call_args.args[0] == "staking_rewards"
    assert kwargs["ok"] is True
    assert kwargs["summary"] == {"skipped": "disabled"}


@pytest.mark.asyncio
async def test_run_scheduled_staking_rewards_records_stale_as_failure():
    with (
        patch(
            "src.services.staking_rewards.run_staking_rewards_once",
            side_effect=StakingRewardsStaleError("stake_sync_stale"),
        ),
        patch("src.services.scheduled_sync.record_job_run") as mock_record,
    ):
        await run_scheduled_staking_rewards()

    kwargs = mock_record.call_args.kwargs
    assert kwargs["ok"] is False
    assert "stake_sync_stale" in kwargs["error"]


@pytest.mark.asyncio
async def test_run_scheduled_staking_rewards_never_raises_on_unexpected_error():
    with (
        patch(
            "src.services.staking_rewards.run_staking_rewards_once",
            side_effect=RuntimeError("boom"),
        ),
        patch("src.services.scheduled_sync.record_job_run") as mock_record,
    ):
        await run_scheduled_staking_rewards()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


def test_start_and_stop_staking_rewards_scheduler():
    try:
        start_staking_rewards_scheduler()
        assert scheduled_sync._staking_rewards_scheduler is not None
        assert scheduled_sync._staking_rewards_scheduler.get_job("staking_rewards") is not None
    finally:
        stop_staking_rewards_scheduler()

    assert scheduled_sync._staking_rewards_scheduler is None


def test_start_staking_rewards_scheduler_uses_configured_cron_time(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "STAKING_REWARDS_CRON_HOUR_UTC", 3)
    monkeypatch.setattr(scheduled_sync.Config, "STAKING_REWARDS_CRON_MINUTE_UTC", 45)
    try:
        start_staking_rewards_scheduler()
        job = scheduled_sync._staking_rewards_scheduler.get_job("staking_rewards")
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"] == "3"
        assert fields["minute"] == "45"
    finally:
        stop_staking_rewards_scheduler()


def test_start_staking_rewards_scheduler_failure_does_not_raise():
    """Matches the other schedulers' fail-soft behavior -- startup must not
    crash if APScheduler itself can't be started."""
    with patch(
        "src.services.scheduled_sync.AsyncIOScheduler", side_effect=RuntimeError("no event loop")
    ):
        start_staking_rewards_scheduler()  # must not raise
    assert scheduled_sync._staking_rewards_scheduler is None


def test_stop_staking_rewards_scheduler_noop_when_not_started():
    scheduled_sync._staking_rewards_scheduler = None
    stop_staking_rewards_scheduler()  # must not raise
