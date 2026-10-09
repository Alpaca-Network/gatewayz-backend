"""The delegation scheduler wrappers record every run (ok or not) and the
measurement sweep alerts when it recorded nothing; the holdings sweep never
tries to read a Cardano stake address as an EVM wallet."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.services.delegation.alerts as alerts
import src.services.scheduled_sync as scheduled_sync


@pytest.mark.asyncio
async def test_disabled_runs_are_recorded_as_skipped(monkeypatch):
    monkeypatch.setattr(scheduled_sync.Config, "DELEGATED_STAKING_ENABLED", False)
    with patch.object(scheduled_sync, "record_job_run") as record:
        await scheduled_sync.run_scheduled_delegation_measurements()
        await scheduled_sync.run_scheduled_delegation_accruals()
        await scheduled_sync.run_scheduled_delegation_reconciliation()
    names = [c.args[0] for c in record.call_args_list]
    assert names == ["delegation_measurements", "delegation_accruals", "delegation_reconciliation"]
    for call in record.call_args_list:
        assert call.kwargs["ok"] is True
        assert call.kwargs["summary"] == {"skipped": "disabled"}


@pytest.mark.asyncio
async def test_a_failed_run_is_recorded_not_raised(monkeypatch):
    def boom(*_):
        raise RuntimeError("no measurements")

    monkeypatch.setattr(
        "src.services.delegation.rewards.run_delegation_accruals_once", boom, raising=True
    )
    with patch.object(scheduled_sync, "record_job_run") as record:
        await scheduled_sync.run_scheduled_delegation_accruals()
    assert record.call_args.kwargs["ok"] is False
    assert "no measurements" in record.call_args.kwargs["error"]


def test_scheduler_registers_three_jobs():
    scheduled_sync.start_delegation_scheduler()
    try:
        ids = {job.id for job in scheduled_sync._delegation_scheduler.get_jobs()}
        assert ids == {
            "delegation_measurements",
            "delegation_accruals",
            "delegation_reconciliation",
        }
    finally:
        scheduled_sync.stop_delegation_scheduler()


def test_measured_nothing_alerts_per_asset(monkeypatch):
    monkeypatch.setattr(alerts.Config, "DELEGATED_STAKING_ENABLED", True)
    sent = []
    monkeypatch.setattr(alerts, "_send", lambda cond, subject, lines: sent.append(cond) or True)
    summary = {
        "taken_at": "2026-10-08T06:10:00+00:00",
        "eth": {"wallets": 3, "recorded": 0, "read_failed": 3},
        "ada": {"wallets": 2, "recorded": 2},
    }
    assert alerts.alert_if_measured_nothing(summary) is True
    assert sent == ["delegation_measured_nothing_eth"]


def test_alerts_are_silent_while_disabled(monkeypatch):
    monkeypatch.setattr(alerts.Config, "DELEGATED_STAKING_ENABLED", False)
    with patch.object(alerts, "_send") as send:
        assert alerts.alert_overspent("eth", {}) is False
        assert alerts.alert_if_measured_nothing({"eth": {"wallets": 1, "recorded": 0}}) is False
    send.assert_not_called()


def test_holdings_sweep_skips_cardano_wallets(monkeypatch):
    import src.services.holdings.snapshots as snapshots

    read = []
    monkeypatch.setattr(snapshots.Config, "HOLDINGS_REWARDS_ENABLED", True)
    monkeypatch.setattr(snapshots.Config, "HOLDINGS_MIN_WALLET_AGE_DAYS", 0)
    monkeypatch.setattr(
        snapshots,
        "list_all_wallets",
        lambda: [
            {
                "wallet_address": "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw",
                "chain_namespace": "cip34",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(snapshots, "read_balances", lambda a, t: read.append(a))
    monkeypatch.setattr(
        snapshots,
        "list_enabled_tokens",
        lambda: [
            {
                "id": 1,
                "chain_id": 1,
                "contract_address": None,
                "symbol": "ETH",
                "decimals": 18,
                "price_id": "ethereum",
            }
        ],
    )
    monkeypatch.setattr(snapshots, "get_usd_prices", lambda ids: {})
    summary = snapshots.run_holdings_snapshots_once()
    assert read == []
    assert summary.get("wallets_considered", 0) == 0
