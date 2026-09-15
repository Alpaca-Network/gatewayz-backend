"""Tests for the holdings-rewards scheduler wiring in
src/services/scheduled_sync.py -- the observation sweep (several times a
day) and the daily accrual, wired exactly like the staking-rewards job in
tests/services/test_scheduled_sync_staking_rewards.py."""

from unittest.mock import patch

import pytest

import src.services.scheduled_sync as scheduled_sync
from src.services.holdings.rewards import HoldingsSnapshotsMissingError
from src.services.scheduled_sync import (
    holdings_snapshot_cron_hours,
    run_scheduled_holdings_rewards,
    run_scheduled_holdings_snapshots,
    start_holdings_rewards_scheduler,
    start_holdings_snapshots_scheduler,
    stop_holdings_rewards_scheduler,
    stop_holdings_snapshots_scheduler,
)


class TestSnapshotCronHours:
    def test_four_sweeps_a_day_are_six_hours_apart(self):
        assert holdings_snapshot_cron_hours(4) == [0, 6, 12, 18]

    def test_a_count_that_does_not_divide_24_still_gets_that_many_sweeps(self):
        hours = holdings_snapshot_cron_hours(5)
        assert len(hours) == 5
        assert hours == sorted(set(hours))

    def test_count_is_clamped_to_a_sane_range(self):
        assert holdings_snapshot_cron_hours(0) == [0]
        assert holdings_snapshot_cron_hours(-3) == [0]
        assert len(holdings_snapshot_cron_hours(500)) == 24


@pytest.mark.asyncio
class TestSnapshotJobRun:
    async def test_records_success(self):
        result = {"wallets_considered": 3, "rows_recorded": 5}
        with (
            patch(
                "src.services.holdings.snapshots.run_holdings_snapshots_once", return_value=result
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_snapshots()
        assert mock_record.call_args.args[0] == "holdings_snapshots"
        assert mock_record.call_args.kwargs["ok"] is True
        assert mock_record.call_args.kwargs["summary"] == result

    async def test_records_disabled_skip_as_a_successful_run(self):
        with (
            patch(
                "src.services.holdings.snapshots.run_holdings_snapshots_once",
                return_value={"skipped": "disabled"},
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_snapshots()
        assert mock_record.call_args.kwargs["ok"] is True
        assert mock_record.call_args.kwargs["summary"] == {"skipped": "disabled"}

    async def test_never_raises_on_unexpected_error(self):
        with (
            patch(
                "src.services.holdings.snapshots.run_holdings_snapshots_once",
                side_effect=RuntimeError("boom"),
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_snapshots()  # must not raise
        assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
class TestRewardsJobRun:
    async def test_records_success(self):
        result = {"reward_date": "2026-09-14", "paid": 2, "credits_paid": "1.5"}
        with (
            patch("src.services.holdings.rewards.run_holdings_rewards_once", return_value=result),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_rewards()
        assert mock_record.call_args.args[0] == "holdings_rewards"
        assert mock_record.call_args.kwargs["ok"] is True
        assert mock_record.call_args.kwargs["summary"] == result

    async def test_records_disabled_skip_as_a_successful_run(self):
        with (
            patch(
                "src.services.holdings.rewards.run_holdings_rewards_once",
                return_value={"skipped": "disabled"},
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_rewards()
        assert mock_record.call_args.kwargs["ok"] is True
        assert mock_record.call_args.kwargs["summary"] == {"skipped": "disabled"}

    async def test_missing_snapshots_is_recorded_as_a_failed_run(self):
        """A date with no observed balances means the sweep did not run --
        a coverage gap an operator needs to see, not a silent success."""
        with (
            patch(
                "src.services.holdings.rewards.run_holdings_rewards_once",
                side_effect=HoldingsSnapshotsMissingError("no observed balances for 2026-09-14"),
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_rewards()
        assert mock_record.call_args.kwargs["ok"] is False
        assert "no observed balances" in mock_record.call_args.kwargs["error"]

    async def test_never_raises_on_unexpected_error(self):
        with (
            patch(
                "src.services.holdings.rewards.run_holdings_rewards_once",
                side_effect=RuntimeError("boom"),
            ),
            patch("src.services.scheduled_sync.record_job_run") as mock_record,
        ):
            await run_scheduled_holdings_rewards()  # must not raise
        assert mock_record.call_args.kwargs["ok"] is False


class TestSchedulerLifecycle:
    def test_start_and_stop_snapshots_scheduler(self):
        try:
            start_holdings_snapshots_scheduler()
            assert scheduled_sync._holdings_snapshots_scheduler is not None
            job = scheduled_sync._holdings_snapshots_scheduler.get_job("holdings_snapshots")
            assert job is not None
        finally:
            stop_holdings_snapshots_scheduler()
        assert scheduled_sync._holdings_snapshots_scheduler is None

    def test_snapshots_scheduler_uses_the_configured_sweep_count(self, monkeypatch):
        monkeypatch.setattr(scheduled_sync.Config, "HOLDINGS_SNAPSHOTS_PER_DAY", 2)
        monkeypatch.setattr(scheduled_sync.Config, "HOLDINGS_SNAPSHOT_CRON_MINUTE_UTC", 5)
        try:
            start_holdings_snapshots_scheduler()
            job = scheduled_sync._holdings_snapshots_scheduler.get_job("holdings_snapshots")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "0,12"
            assert fields["minute"] == "5"
        finally:
            stop_holdings_snapshots_scheduler()

    def test_start_and_stop_rewards_scheduler(self):
        try:
            start_holdings_rewards_scheduler()
            assert scheduled_sync._holdings_rewards_scheduler is not None
            assert (
                scheduled_sync._holdings_rewards_scheduler.get_job("holdings_rewards") is not None
            )
        finally:
            stop_holdings_rewards_scheduler()
        assert scheduled_sync._holdings_rewards_scheduler is None

    def test_rewards_scheduler_uses_the_configured_cron_time(self, monkeypatch):
        monkeypatch.setattr(scheduled_sync.Config, "HOLDINGS_REWARDS_CRON_HOUR_UTC", 2)
        monkeypatch.setattr(scheduled_sync.Config, "HOLDINGS_REWARDS_CRON_MINUTE_UTC", 15)
        try:
            start_holdings_rewards_scheduler()
            job = scheduled_sync._holdings_rewards_scheduler.get_job("holdings_rewards")
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == "2"
            assert fields["minute"] == "15"
        finally:
            stop_holdings_rewards_scheduler()

    def test_scheduler_start_failure_does_not_raise(self):
        with patch(
            "src.services.scheduled_sync.AsyncIOScheduler",
            side_effect=RuntimeError("no event loop"),
        ):
            start_holdings_snapshots_scheduler()  # must not raise
            start_holdings_rewards_scheduler()  # must not raise
        assert scheduled_sync._holdings_snapshots_scheduler is None
        assert scheduled_sync._holdings_rewards_scheduler is None

    def test_stop_is_a_noop_when_not_started(self):
        scheduled_sync._holdings_snapshots_scheduler = None
        scheduled_sync._holdings_rewards_scheduler = None
        stop_holdings_snapshots_scheduler()  # must not raise
        stop_holdings_rewards_scheduler()  # must not raise
