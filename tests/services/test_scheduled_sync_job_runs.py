"""Every scheduled job in src/services/scheduled_sync.py (plus
src/services/gpu/rollup.py's hourly rollup) must call
src.services.ops.job_runs.record_job_run on BOTH its success and its
failure path. One test pair per job, mirroring the existing
tests/services/test_scheduled_sync_gpu_liveness.py / _retention.py style.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.services.scheduled_sync as scheduled_sync


@pytest.mark.asyncio
async def test_model_sync_records_on_success():
    result = {
        "success": True,
        "total_models_synced": 3,
        "total_models_fetched": 5,
        "providers_processed": 1,
    }
    with (
        patch("src.services.model_catalog_sync.sync_all_providers", return_value=result),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_model_sync()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "model_sync"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_model_sync_records_on_exception():
    with (
        patch("src.services.model_catalog_sync.sync_all_providers", side_effect=RuntimeError("x")),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_model_sync()  # must not raise

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "model_sync"
    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_price_refresh_records_on_success():
    result = {"success": True, "prices_updated": 2, "prices_unchanged": 1, "providers_checked": 3}
    with (
        patch("src.services.price_refresh.refresh_all_prices", return_value=result),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_price_refresh()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "price_refresh"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_price_refresh_records_on_exception():
    with (
        patch("src.services.price_refresh.refresh_all_prices", side_effect=RuntimeError("x")),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_price_refresh()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_ledger_reconciliation_records_on_success():
    report = MagicMock(ok=True, total_drift=0, ledger_ref_count=5)
    with (
        patch(
            "src.services.billing.ledger_reconciliation.reconcile_window", return_value=(report, 0)
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_ledger_reconciliation()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "ledger_reconciliation"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_ledger_reconciliation_records_on_exception():
    with (
        patch(
            "src.services.billing.ledger_reconciliation.reconcile_window",
            side_effect=RuntimeError("x"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_ledger_reconciliation()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_retention_cleanup_records_on_success():
    with (
        patch("src.db.retention.cleanup_usage_records", return_value=10),
        patch("src.db.retention.cleanup_activity_log", return_value=5),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_retention_cleanup()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "retention_cleanup"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_retention_cleanup_records_on_exception():
    with (
        patch("src.db.retention.cleanup_usage_records", side_effect=RuntimeError("x")),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_retention_cleanup()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_wayz_staking_sync_records_on_success():
    fake_client = MagicMock()
    fake_result = MagicMock(
        wallets_discovered=1, wallets_synced=1, wallets_failed=0, total_staked=100, to_block=42
    )
    with (
        patch(
            "src.services.chain.wayz_staking_client.WayzStakingClient.from_config",
            return_value=fake_client,
        ),
        patch("src.services.chain.wayz_staking_sync.sync_once", return_value=fake_result),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_wayz_staking_sync()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "wayz_staking_sync"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_wayz_staking_sync_records_on_exception():
    fake_client = MagicMock()
    with (
        patch(
            "src.services.chain.wayz_staking_client.WayzStakingClient.from_config",
            return_value=fake_client,
        ),
        patch(
            "src.services.chain.wayz_staking_sync.sync_once", side_effect=RuntimeError("rpc down")
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_wayz_staking_sync()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_gpu_spot_check_records_on_success():
    stats = {"verified": 2, "failed": 0, "skipped": 1}
    with (
        patch(
            "src.services.gpu.spot_check.run_spot_check_verification",
            new_callable=AsyncMock,
            return_value=stats,
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_gpu_spot_check()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "gpu_spot_check"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_gpu_spot_check_records_on_exception():
    with (
        patch(
            "src.services.gpu.spot_check.run_spot_check_verification",
            new_callable=AsyncMock,
            side_effect=RuntimeError("x"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_gpu_spot_check()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_gpu_settlement_records_on_success():
    fake_client = MagicMock()
    reconcile_result = MagicMock(
        settlements_checked=0, settlements_confirmed_sent=0, settlements_marked_failed=0
    )
    settlement_result = MagicMock(
        providers_considered=1, settlements_sent=1, settlements_failed=0, total_sent_wei=1000
    )
    with (
        patch(
            "src.services.chain.wayz_rewards_client.WayzProviderRewardsClient.from_config",
            return_value=fake_client,
        ),
        patch(
            "src.services.gpu.settlement.reconcile_stuck_settlements",
            new_callable=AsyncMock,
            return_value=reconcile_result,
        ),
        patch(
            "src.services.gpu.settlement.run_settlement_once",
            new_callable=AsyncMock,
            return_value=settlement_result,
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_gpu_settlement()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "gpu_settlement"
    assert mock_record.call_args.kwargs["ok"] is True
    # Reconciliation outcome is folded into this job's own summary, not a
    # separate "gpu_settlement_reconcile" job.
    assert "reconcile" in mock_record.call_args.kwargs["summary"]


@pytest.mark.asyncio
async def test_gpu_settlement_records_on_exception():
    fake_client = MagicMock()
    with (
        patch(
            "src.services.chain.wayz_rewards_client.WayzProviderRewardsClient.from_config",
            return_value=fake_client,
        ),
        patch(
            "src.services.gpu.settlement.reconcile_stuck_settlements",
            new_callable=AsyncMock,
            side_effect=RuntimeError("x"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_gpu_settlement()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_pricing_drift_records_on_success():
    result = {"ok": True, "checked": 10, "drift": [], "unpriced": []}
    with (
        patch(
            "src.services.billing.pricing_drift_monitor.audit_pricing_drift", return_value=result
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_pricing_drift_audit()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "pricing_drift"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_pricing_drift_records_on_drift_found():
    result = {
        "ok": False,
        "checked": 10,
        "drift": [{"model": "x"}],
        "unpriced": [],
        "worst_deficit_pct": 5.0,
    }
    with (
        patch(
            "src.services.billing.pricing_drift_monitor.audit_pricing_drift", return_value=result
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_pricing_drift_audit()

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_pricing_drift_records_on_exception():
    with (
        patch(
            "src.services.billing.pricing_drift_monitor.audit_pricing_drift",
            side_effect=RuntimeError("x"),
        ),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_scheduled_pricing_drift_audit()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_gpu_liveness_sweep_records_on_success():
    with (
        patch("src.db.gpu.sweep_liveness", return_value=(1, 0)),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_gpu_liveness_sweep()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "gpu_liveness_sweep"
    assert mock_record.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
async def test_gpu_liveness_sweep_records_on_exception():
    with (
        patch("src.db.gpu.sweep_liveness", side_effect=RuntimeError("x")),
        patch.object(scheduled_sync, "record_job_run") as mock_record,
    ):
        await scheduled_sync.run_gpu_liveness_sweep()  # must not raise

    assert mock_record.call_args.kwargs["ok"] is False


def test_gpu_rollup_records_on_success():
    import src.services.gpu.rollup as rollup

    # Patch the names as bound INTO rollup's own namespace (`from ... import
    # X` binds a local reference at import time) -- patching
    # src.db.gpu_rollups.X directly would not affect rollup.X.
    with (
        patch.object(rollup, "is_utilization_empty", return_value=False),
        patch.object(rollup, "aggregate_hour", return_value=[{"hour": "x"}]),
        patch.object(rollup, "upsert_hourly_rows", return_value=True),
        patch.object(rollup, "record_job_run") as mock_record,
    ):
        rollup.run_hourly_rollup()

    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "gpu_rollup"
    assert mock_record.call_args.kwargs["ok"] is True


def test_gpu_rollup_records_on_exception():
    import src.services.gpu.rollup as rollup

    with (
        patch.object(rollup, "is_utilization_empty", side_effect=RuntimeError("x")),
        patch.object(rollup, "record_job_run") as mock_record,
    ):
        rollup.run_hourly_rollup()  # must not raise

    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["ok"] is False
