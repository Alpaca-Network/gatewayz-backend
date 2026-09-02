"""Tests for src.routes.staking (supports gatewayz-backend#2246)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


@patch("src.routes.staking.get_stake_totals")
@patch("src.routes.staking.get_wallet_stake")
def test_wallet_unknown_returns_zeros_and_unsynced(mock_get_stake, mock_totals):
    mock_get_stake.return_value = None
    mock_totals.return_value = ("0", 0)

    response = client.get(f"/staking/wallets/0x{'1' * 40}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["staked_amount"] == "0"
    assert data["daily_allowance"] == "0"
    assert data["last_synced_block"] is None
    assert data["last_synced_at"] is None
    assert data["synced"] is False


@patch("src.routes.staking.get_stake_totals")
@patch("src.routes.staking.get_wallet_stake")
def test_wallet_known_passes_through_values_as_strings(mock_get_stake, mock_totals):
    mock_get_stake.return_value = {
        "wallet_address": "0x" + "1" * 40,
        "staked_amount": "123000000000000000000",
        "daily_allowance": "10",
        "last_synced_block": 999,
        "last_synced_at": "2026-09-01T00:00:00+00:00",
    }
    mock_totals.return_value = ("123000000000000000000", 1)

    response = client.get(f"/staking/wallets/0x{'1' * 40}")

    assert response.status_code == 200
    data = response.json()["data"]
    # Guards against int/float mangling of a wei-scale numeric string.
    assert data["staked_amount"] == "123000000000000000000"
    assert data["daily_allowance"] == "10"
    assert data["last_synced_block"] == 999
    assert data["last_synced_at"] == "2026-09-01T00:00:00+00:00"
    assert data["synced"] is True
    assert data["total_staked"] == "123000000000000000000"


def test_wallet_rejects_invalid_address():
    response = client.get("/staking/wallets/not-an-address")
    assert response.status_code == 422


@patch("src.routes.staking.get_stake_totals")
@patch("src.routes.staking.get_wallet_stake")
def test_wallet_configured_flips_with_config(mock_get_stake, mock_totals, monkeypatch):
    mock_get_stake.return_value = None
    mock_totals.return_value = ("0", 0)
    monkeypatch.setattr("src.routes.staking.Config.WAYZ_STAKING_CONTRACT_ADDRESS", None)

    response = client.get(f"/staking/wallets/0x{'2' * 40}")
    assert response.json()["data"]["configured"] is False

    monkeypatch.setattr("src.routes.staking.Config.WAYZ_STAKING_CONTRACT_ADDRESS", "0x" + "3" * 40)
    response = client.get(f"/staking/wallets/0x{'2' * 40}")
    assert response.json()["data"]["configured"] is True
    assert response.json()["data"]["contracts"]["staking"] == "0x" + "3" * 40
    assert response.json()["data"]["contracts"]["chain_id"] == 43113


@patch("src.routes.staking.get_sync_cursor_row")
@patch("src.routes.staking.get_stake_totals")
def test_summary_aggregates(mock_totals, mock_cursor_row, monkeypatch):
    monkeypatch.setattr("src.routes.staking.Config.WAYZ_STAKING_CONTRACT_ADDRESS", "0x" + "3" * 40)
    mock_totals.return_value = ("1000000000000000000000", 3)
    mock_cursor_row.return_value = {
        "last_synced_block": 12345,
        "updated_at": "2026-09-01T00:00:00+00:00",
    }

    response = client.get("/staking/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_staked"] == "1000000000000000000000"
    assert data["wallet_count"] == 3
    assert data["last_synced_block"] == 12345
    assert data["last_synced_at"] == "2026-09-01T00:00:00+00:00"


@patch("src.routes.staking.get_sync_cursor_row")
@patch("src.routes.staking.get_stake_totals")
def test_summary_unstake_cooldown_constant(mock_totals, mock_cursor_row):
    mock_totals.return_value = ("0", 0)
    mock_cursor_row.return_value = None

    response = client.get("/staking/summary")
    assert response.json()["data"]["unstake_cooldown_seconds"] == 604800


@patch("src.routes.staking.get_sync_cursor_row")
@patch("src.routes.staking.get_stake_totals")
def test_summary_no_cursor_row_returns_nulls(mock_totals, mock_cursor_row):
    mock_totals.return_value = ("0", 0)
    mock_cursor_row.return_value = None

    response = client.get("/staking/summary")

    data = response.json()["data"]
    assert data["last_synced_block"] is None
    assert data["last_synced_at"] is None
