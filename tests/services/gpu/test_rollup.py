"""Tests for src.services.gpu.rollup (gatewayz-backend#2263 #2264, spec §6)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.services.gpu.rollup import (
    _BACKFILL_HOURS,
    run_hourly_rollup,
    start_gpu_rollup_scheduler,
    stop_gpu_rollup_scheduler,
)


@patch("src.services.gpu.rollup.upsert_hourly_rows")
@patch("src.services.gpu.rollup.aggregate_hour")
@patch("src.services.gpu.rollup.is_utilization_empty", return_value=False)
def test_run_hourly_rollup_computes_only_previous_hour_when_not_empty(
    mock_empty, mock_aggregate, mock_upsert
):
    mock_aggregate.return_value = [{"hour": "x", "region": "r", "model": "m"}]

    run_hourly_rollup()

    assert mock_aggregate.call_count == 1
    mock_upsert.assert_called_once_with([{"hour": "x", "region": "r", "model": "m"}])


@patch("src.services.gpu.rollup.upsert_hourly_rows")
@patch("src.services.gpu.rollup.aggregate_hour", return_value=[])
@patch("src.services.gpu.rollup.is_utilization_empty", return_value=False)
def test_run_hourly_rollup_skips_upsert_when_no_rows(mock_empty, mock_aggregate, mock_upsert):
    run_hourly_rollup()
    mock_upsert.assert_not_called()


@patch("src.services.gpu.rollup.upsert_hourly_rows")
@patch("src.services.gpu.rollup.aggregate_hour", return_value=[])
@patch("src.services.gpu.rollup.is_utilization_empty", return_value=True)
def test_run_hourly_rollup_backfills_when_table_empty(mock_empty, mock_aggregate, mock_upsert):
    run_hourly_rollup()

    # _BACKFILL_HOURS distinct hours total: previous hour + the
    # _BACKFILL_HOURS-1 hours before it.
    assert mock_aggregate.call_count == _BACKFILL_HOURS


@patch("src.services.gpu.rollup.upsert_hourly_rows")
@patch("src.services.gpu.rollup.aggregate_hour", return_value=[])
@patch("src.services.gpu.rollup.is_utilization_empty", return_value=True)
def test_backfill_covers_the_previous_168_distinct_hours(mock_empty, mock_aggregate, mock_upsert):
    run_hourly_rollup()

    called_hours = [call.args[0] for call in mock_aggregate.call_args_list]
    assert len(set(called_hours)) == len(called_hours), "backfill re-computed an hour twice"

    now = datetime.now(UTC)
    expected_previous_hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    assert expected_previous_hour in called_hours
    oldest_expected = expected_previous_hour - timedelta(hours=_BACKFILL_HOURS - 1)
    assert oldest_expected in called_hours


def test_start_stop_scheduler_lifecycle():
    start_gpu_rollup_scheduler()
    try:
        import src.services.gpu.rollup as rollup_module

        assert rollup_module._scheduler is not None
        assert rollup_module._scheduler.get_job("gpu_utilization_hourly_rollup") is not None
    finally:
        stop_gpu_rollup_scheduler()

    import src.services.gpu.rollup as rollup_module

    assert rollup_module._scheduler is None


def test_start_scheduler_twice_is_a_no_op():
    start_gpu_rollup_scheduler()
    try:
        import src.services.gpu.rollup as rollup_module

        first = rollup_module._scheduler
        start_gpu_rollup_scheduler()
        assert rollup_module._scheduler is first
    finally:
        stop_gpu_rollup_scheduler()


def test_stop_scheduler_when_not_started_is_a_no_op():
    stop_gpu_rollup_scheduler()  # must not raise
