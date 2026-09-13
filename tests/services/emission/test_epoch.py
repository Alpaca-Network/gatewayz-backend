"""Tests for src.services.emission.epoch (Chutes-style WAYZ emission
rewards, gatewayz-backend tokenomics). DB access is mocked at the point of
use in epoch.py, same convention as tests/routes/test_gpu_earnings.py."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

import src.services.emission.epoch as epoch
from src.services.staking_rewards import StakingRewardsStaleError


@pytest.fixture(autouse=True)
def _reset_config_disabled():
    epoch._config_disabled_reason = None
    yield
    epoch._config_disabled_reason = None


@pytest.fixture
def emission_mode(monkeypatch):
    monkeypatch.setattr(epoch.Config, "REWARDS_MODE", "emission")
    monkeypatch.setattr(epoch.Config, "WAYZ_DAILY_EMISSION", "100000")
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_PROVIDERS_BPS", 4100)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_STAKERS_BPS", 4100)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", 1800)
    monkeypatch.setattr(epoch.Config, "STAKER_REWARD_ASSET", "credits")
    monkeypatch.setattr(epoch.Config, "WAYZ_CREDIT_RATE", "0.001")
    monkeypatch.setattr(epoch.Config, "STAKING_REWARDS_DAILY_CAP_CREDITS", 50.0)
    monkeypatch.setattr(epoch.Config, "STAKING_REWARDS_MIN_CREDITS", 0.0001)
    monkeypatch.setattr(epoch.Config, "COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER", None)


# ---------------------------------------------------------------------------
# check_emission_config
# ---------------------------------------------------------------------------


def test_check_emission_config_valid_sums_enable_the_job(emission_mode):
    epoch.check_emission_config()
    assert epoch.is_emission_job_disabled() is None


def test_check_emission_config_bad_reward_split_disables_the_job(emission_mode, monkeypatch):
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", 1000)  # sums to 9200
    epoch.check_emission_config()
    reason = epoch.is_emission_job_disabled()
    assert reason is not None
    assert "9200" in reason


def test_check_emission_config_bad_weight_sum_disables_the_job(emission_mode, monkeypatch):
    monkeypatch.setattr(epoch.Config, "PROVIDER_SCORE_WEIGHT_SPEED_BPS", 1000)  # sums to 9000
    epoch.check_emission_config()
    assert epoch.is_emission_job_disabled() is not None


# ---------------------------------------------------------------------------
# run_emission_epoch -- gating
# ---------------------------------------------------------------------------


def test_run_emission_epoch_skipped_when_mode_is_per_unit(monkeypatch):
    monkeypatch.setattr(epoch.Config, "REWARDS_MODE", "per_unit")
    assert epoch.run_emission_epoch(date(2026, 9, 12)) == {"skipped": "disabled"}


def test_run_emission_epoch_skipped_when_config_disabled(emission_mode):
    epoch._config_disabled_reason = "bad config"
    result = epoch.run_emission_epoch(date(2026, 9, 12))
    assert result == {"skipped": "bad_config", "reason": "bad config"}


@patch("src.services.emission.epoch.get_epoch")
def test_run_emission_epoch_skipped_when_already_run(mock_get_epoch, emission_mode):
    mock_get_epoch.return_value = {"epoch_date": "2026-09-12", "status": "allocated"}
    result = epoch.run_emission_epoch(date(2026, 9, 12))
    assert result["skipped"] == "already_run"
    mock_get_epoch.assert_called_once_with("2026-09-12")


@patch("src.services.emission.epoch.is_stake_sync_stale")
@patch("src.services.emission.epoch.get_epoch")
def test_run_emission_epoch_raises_when_stake_sync_stale(mock_get_epoch, mock_stale, emission_mode):
    mock_get_epoch.return_value = None
    mock_stale.return_value = True
    with pytest.raises(StakingRewardsStaleError):
        epoch.run_emission_epoch(date(2026, 9, 12))


# ---------------------------------------------------------------------------
# run_emission_epoch -- happy path
# ---------------------------------------------------------------------------


@patch("src.services.emission.epoch.create_epoch")
@patch("src.services.emission.epoch.pay_pending_accrual")
@patch("src.services.emission.epoch.create_accrual")
@patch("src.services.emission.epoch.get_wallet")
@patch("src.services.emission.epoch.get_accrual")
@patch("src.services.emission.epoch.list_wallets_with_stake")
@patch("src.services.emission.epoch.create_emission_earning")
@patch("src.services.emission.epoch.create_provider_scores")
@patch("src.services.emission.epoch.get_payout_tiers")
@patch("src.services.emission.epoch.list_active_nodes")
@patch("src.services.emission.epoch.list_approved_providers")
@patch("src.services.emission.epoch.list_verified_work_window")
@patch("src.services.emission.epoch.is_stake_sync_stale")
@patch("src.services.emission.epoch.get_epoch")
def test_run_emission_epoch_happy_path(
    mock_get_epoch,
    mock_stale,
    mock_work_window,
    mock_approved_providers,
    mock_active_nodes,
    mock_tiers,
    mock_create_scores,
    mock_create_earning,
    mock_list_wallets,
    mock_get_accrual,
    mock_get_wallet,
    mock_create_accrual,
    mock_pay_pending,
    mock_create_epoch,
    emission_mode,
):
    mock_get_epoch.return_value = None
    mock_stale.return_value = False
    mock_work_window.return_value = [
        {
            "provider_id": 1,
            "node_id": 10,
            "model": "llama-3.1-8b-instruct",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "latency_ms": 200,
            "attested": False,
            "created_at": "2026-09-12T10:00:00+00:00",
        },
        {
            "provider_id": 1,
            "node_id": 10,
            "model": "llama-3.1-8b-instruct",
            "prompt_tokens": 2000,
            "completion_tokens": 1000,
            "latency_ms": 220,
            "attested": False,
            "created_at": "2026-09-12T11:00:00+00:00",
        },
    ]
    mock_approved_providers.return_value = [{"id": 1}]
    mock_active_nodes.return_value = []
    mock_tiers.return_value = []
    mock_create_scores.return_value = True
    mock_create_earning.return_value = ({"id": 99}, "created")

    mock_list_wallets.return_value = [
        {"wallet_address": "0xabc", "staked_amount": "1000000000000000000"}
    ]
    mock_get_accrual.return_value = None
    mock_get_wallet.return_value = {"user_id": 7, "is_active": True}
    mock_create_accrual.return_value = {
        "id": 5,
        "wallet_address": "0xabc",
        "reward_date": "2026-09-12",
        "user_id": 7,
        "staked_amount_wei": "1000000000000000000",
        "credits": "1.000000",
        "rate_id": None,
    }
    mock_pay_pending.return_value = ("paid", Decimal("1.000000"))
    mock_create_epoch.return_value = {"epoch_date": "2026-09-12"}

    result = epoch.run_emission_epoch(date(2026, 9, 12))

    assert result["epoch_date"] == "2026-09-12"
    assert result["providers_scored"] == 1
    assert result["stakers_paid"] == 1
    assert result["credits_paid"] == "1.000000"
    assert Decimal(result["split"]["providers_wei"]) == Decimal(100000 * 10**18 * 4100 // 10000)

    # The sole eligible provider with all the activity gets the entire
    # providers_wei pool (minus at most a tiny floor-dust remainder).
    mock_create_earning.assert_called_once()
    called_provider_id, called_epoch_date, called_amount = mock_create_earning.call_args[0]
    assert called_provider_id == 1
    assert called_epoch_date == "2026-09-12"
    assert called_amount > 0

    mock_create_epoch.assert_called_once()
    args, kwargs = mock_create_epoch.call_args
    assert args[0] == "2026-09-12"
    assert kwargs["status"] == "allocated"


@patch("src.services.emission.epoch.create_epoch")
@patch("src.services.emission.epoch.list_wallets_with_stake")
@patch("src.services.emission.epoch.create_provider_scores")
@patch("src.services.emission.epoch.get_payout_tiers")
@patch("src.services.emission.epoch.list_active_nodes")
@patch("src.services.emission.epoch.list_approved_providers")
@patch("src.services.emission.epoch.list_verified_work_window")
@patch("src.services.emission.epoch.is_stake_sync_stale")
@patch("src.services.emission.epoch.get_epoch")
def test_run_emission_epoch_no_eligible_providers_dusts_treasury(
    mock_get_epoch,
    mock_stale,
    mock_work_window,
    mock_approved_providers,
    mock_active_nodes,
    mock_tiers,
    mock_create_scores,
    mock_list_wallets,
    mock_create_epoch,
    emission_mode,
):
    mock_get_epoch.return_value = None
    mock_stale.return_value = False
    mock_work_window.return_value = []
    mock_approved_providers.return_value = []
    mock_active_nodes.return_value = []
    mock_tiers.return_value = []
    mock_list_wallets.return_value = []
    mock_create_epoch.return_value = {"epoch_date": "2026-09-12"}

    result = epoch.run_emission_epoch(date(2026, 9, 12))

    assert result["providers_scored"] == 0
    treasury_wei = Decimal(result["split"]["treasury_wei"])
    emission_wei = Decimal(result["emission_wei"])
    stakers_wei = emission_wei * 4100 // 10000
    # The whole providers_wei leg becomes dust with zero eligible
    # providers, landing entirely in treasury alongside the usual 18% cut.
    expected_treasury = emission_wei - stakers_wei
    assert treasury_wei == expected_treasury


# ---------------------------------------------------------------------------
# staker payout -- wayz mode records a documented pending limitation
# ---------------------------------------------------------------------------


@patch("src.services.emission.epoch.create_accrual")
@patch("src.services.emission.epoch.get_accrual")
@patch("src.services.emission.epoch.list_wallets_with_stake")
def test_pay_stakers_wayz_mode_records_pending_not_implemented(
    mock_list_wallets, mock_get_accrual, mock_create_accrual, emission_mode, monkeypatch
):
    monkeypatch.setattr(epoch.Config, "STAKER_REWARD_ASSET", "wayz")
    mock_list_wallets.return_value = [{"wallet_address": "0xabc", "staked_amount": "1000"}]
    mock_get_accrual.return_value = None

    summary = epoch._pay_stakers("2026-09-12", 1_000_000)

    assert summary["paid"] == 0
    assert summary["pending"] == 1
    assert summary["asset"] == "wayz"
    mock_create_accrual.assert_called_once()
    _args, kwargs = mock_create_accrual.call_args
    assert kwargs["skip_reason"] == "wayz_payout_not_implemented"
    assert kwargs["source"] == "emission"
    assert kwargs["status"] == "pending"


def test_pay_stakers_no_stake_returns_all_zero(emission_mode):
    with patch("src.services.emission.epoch.list_wallets_with_stake", return_value=[]):
        summary = epoch._pay_stakers("2026-09-12", 1_000_000)
    assert summary == {
        "paid": 0,
        "pending": 0,
        "skipped": 0,
        "credits_paid": "0",
        "capped": 0,
        "asset": "credits",
    }


# ---------------------------------------------------------------------------
# Read views
# ---------------------------------------------------------------------------


@patch("src.services.emission.epoch.list_provider_scores_for_epoch")
@patch("src.services.emission.epoch.get_latest_provider_score")
def test_get_provider_emission_view_computes_rank(mock_latest, mock_epoch_scores):
    mock_latest.return_value = {
        "epoch_date": "2026-09-12",
        "compute": "0.5",
        "speed": "0.8",
        "availability": "1.0",
        "unique_models": "0.3",
        "raw_score": "0.6",
        "adjusted_score": "0.65",
        "share": "0.2",
        "allocation_wei": str(2 * 10**18),
    }
    mock_epoch_scores.return_value = [
        {"provider_id": 9, "share": "0.5"},
        {"provider_id": 1, "share": "0.2"},
        {"provider_id": 2, "share": "0.1"},
    ]
    view = epoch.get_provider_emission_view(1)
    assert view["rank"] == 2
    assert view["providers_scored"] == 3
    assert view["allocation_wayz"] == "2"


@patch("src.services.emission.epoch.get_latest_provider_score")
def test_get_provider_emission_view_none_when_never_scored(mock_latest):
    mock_latest.return_value = None
    assert epoch.get_provider_emission_view(1) is None


def test_get_staker_emission_view_none_outside_emission_mode(monkeypatch):
    monkeypatch.setattr(epoch.Config, "REWARDS_MODE", "per_unit")
    assert epoch.get_staker_emission_view(1000) is None


@patch("src.services.emission.epoch.list_wallets_with_stake")
@patch("src.services.emission.epoch.get_latest_epoch")
def test_get_staker_emission_view_computes_your_share(
    mock_latest_epoch, mock_wallets, emission_mode
):
    mock_latest_epoch.return_value = {
        "epoch_date": "2026-09-12",
        "stakers_wei": str(1000 * 10**18),
    }
    mock_wallets.return_value = [
        {"wallet_address": "0xabc", "staked_amount": str(100 * 10**18)},
        {"wallet_address": "0xdef", "staked_amount": str(900 * 10**18)},
    ]
    view = epoch.get_staker_emission_view(total_staked_wei=100 * 10**18)
    assert Decimal(view["your_share"]) == Decimal("0.1")
    assert Decimal(view["estimated_credits_per_day"]) > 0


@patch("src.services.emission.epoch.get_latest_epoch")
def test_get_public_emission_summary_is_aggregate_only(mock_latest_epoch, emission_mode):
    mock_latest_epoch.return_value = {"epoch_date": "2026-09-12"}
    summary = epoch.get_public_emission_summary()
    assert set(summary.keys()) == {
        "mode",
        "daily_emission_wayz",
        "providers_bps",
        "stakers_bps",
        "treasury_bps",
        "last_epoch",
    }
    assert summary["last_epoch"] == "2026-09-12"
