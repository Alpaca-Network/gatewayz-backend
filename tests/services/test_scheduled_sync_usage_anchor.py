"""Usage-anchor scheduled jobs (src/services/scheduled_sync.py): record a job
run on success and failure, flag failed/unsent days as not ok, and only
start when USAGE_ANCHOR_ENABLED is on."""

from unittest.mock import patch

import pytest

import src.services.scheduled_sync as scheduled_sync


@pytest.mark.asyncio
async def test_usage_anchor_records_on_success():
    with (
        patch(
            "src.services.chain.usage_anchor.run_usage_anchor_once",
            return_value={"day": "2026-10-18", "status": "confirmed"},
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_usage_anchor()
    assert mock_record.call_args.args[0] == "usage_anchor"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "unanchored"])
async def test_usage_anchor_flags_failed_days(status):
    with (
        patch(
            "src.services.chain.usage_anchor.run_usage_anchor_once",
            return_value={"day": "2026-10-18", "status": status, "error": "boom"},
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_usage_anchor()
    assert mock_record.call_args.kwargs["ok"] is False
    assert mock_record.call_args.kwargs["error"] == "boom"


@pytest.mark.asyncio
async def test_usage_anchor_records_on_exception():
    with (
        patch(
            "src.services.chain.usage_anchor.run_usage_anchor_once",
            side_effect=RuntimeError("rpc down"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_usage_anchor()  # must not raise
    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_usage_anchor_sweep_records_both_paths():
    with (
        patch(
            "src.services.chain.usage_anchor.sweep_usage_anchors",
            return_value={"checked": 0, "by_status": {}},
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_usage_anchor_sweep()
    assert mock_record.call_args.args[0] == "usage_anchor_sweep"
    assert mock_record.call_args.kwargs["ok"] is True
    with (
        patch(
            "src.services.chain.usage_anchor.sweep_usage_anchors",
            side_effect=RuntimeError("x"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_usage_anchor_sweep()
    assert mock_record.call_args.kwargs["ok"] is False


def test_scheduler_does_not_start_when_disabled(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "USAGE_ANCHOR_ENABLED", False)
    with patch.object(scheduled_sync, "AsyncIOScheduler") as mock_sched:
        scheduled_sync.start_usage_anchor_scheduler()
    mock_sched.assert_not_called()
    assert scheduled_sync._usage_anchor_scheduler is None


def test_scheduler_registers_cron_and_sweep_when_enabled(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "USAGE_ANCHOR_ENABLED", True)
    with patch.object(scheduled_sync, "AsyncIOScheduler") as mock_sched:
        scheduled_sync.start_usage_anchor_scheduler()
        ids = [c.kwargs["id"] for c in mock_sched.return_value.add_job.call_args_list]
        assert ids == ["usage_anchor", "usage_anchor_sweep"]
        mock_sched.return_value.start.assert_called_once()
        scheduled_sync.stop_usage_anchor_scheduler()
    assert scheduled_sync._usage_anchor_scheduler is None
