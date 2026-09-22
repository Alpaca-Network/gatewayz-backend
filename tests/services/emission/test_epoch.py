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
    monkeypatch.setattr(epoch.Config, "PROVIDER_EMISSION_USD_PER_DAY", "100")
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

    # The sole eligible provider (share=1.0) gets the full USD pool
    # ($100/day -> 100_000_000 micros), paid later in ETH on Base. The
    # sole staker (100% of stake) gets the full 41% WAYZ stakers leg as
    # credits. The WAYZ providers leg is not emitted (providers are paid
    # in USD/ETH now), so it's recorded as providers_wei=0 and lands in
    # treasury -- the WAYZ split still sums to exactly emission_wei.
    emission_wei = Decimal(result["emission_wei"])
    providers_wei = Decimal(result["split"]["providers_wei"])
    stakers_wei = Decimal(result["split"]["stakers_wei"])
    treasury_wei = Decimal(result["split"]["treasury_wei"])
    assert providers_wei + stakers_wei + treasury_wei == emission_wei
    assert stakers_wei == Decimal(100000 * 10**18 * 4100 // 10000)
    assert providers_wei == 0
    assert treasury_wei == Decimal(100000 * 10**18 * (4100 + 1800) // 10000)
    assert result["staker_dust_wei"] == "0"
    assert result["provider_payout_asset"] == "ETH"
    assert result["providers_usd_micros"] == 100_000_000
    assert result["provider_dust_usd_micros"] == 0

    mock_create_earning.assert_called_once()
    called_provider_id, called_epoch_date, called_amount = mock_create_earning.call_args[0]
    assert called_provider_id == 1
    assert called_epoch_date == "2026-09-12"
    assert called_amount == 100_000_000  # USD micros

    rows = mock_create_scores.call_args[0][0]
    assert rows[0]["allocation_usd_micros"] == 100_000_000
    assert rows[0]["allocation_wei"] == "0"

    mock_create_epoch.assert_called_once()
    args, kwargs = mock_create_epoch.call_args
    assert args[0] == "2026-09-12"
    # create_epoch(epoch_date, emission_wei, providers_wei, stakers_wei, treasury_wei, ...)
    assert args[2] == int(providers_wei)
    assert args[3] == int(stakers_wei)
    assert args[4] == int(treasury_wei)
    assert kwargs["status"] == "allocated"
    assert kwargs["providers_usd_micros"] == 100_000_000


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
def test_run_emission_epoch_3way_sum_is_exact_with_nonzero_provider_and_staker_dust(
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
    """HIGH fix-round-1 regression: with THREE eligible providers splitting
    unevenly (guaranteed floor-division remainder) and THREE stakers
    splitting unevenly (same), both provider_dust_wei and staker_dust_wei
    must be > 0 AND fully absorbed into treasury_wei -- providers_wei +
    stakers_wei + treasury_wei must still equal emission_wei exactly."""
    mock_get_epoch.return_value = None
    mock_stale.return_value = False
    # Three providers, unevenly-weighted activity -> unequal shares ->
    # floor(providers_wei * share) for each leaves a nonzero remainder.
    mock_work_window.return_value = [
        {
            "provider_id": pid,
            "node_id": pid,
            "model": "llama-3.1-8b-instruct",
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "latency_ms": 200,
            "attested": False,
            "created_at": "2026-09-12T10:00:00+00:00",
        }
        for pid, tokens in [(1, 1000), (2, 333), (3, 111)]
    ]
    mock_approved_providers.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
    mock_active_nodes.return_value = []
    mock_tiers.return_value = []
    mock_create_scores.return_value = True
    mock_create_earning.return_value = ({"id": 99}, "created")

    # Three stakers, unevenly-weighted stake -> same floor-division dust
    # on the staker leg.
    mock_list_wallets.return_value = [
        {"wallet_address": "0xa", "staked_amount": str(1000 * 10**18)},
        {"wallet_address": "0xb", "staked_amount": str(333 * 10**18)},
        {"wallet_address": "0xc", "staked_amount": str(111 * 10**18)},
    ]
    mock_get_accrual.return_value = None
    mock_get_wallet.return_value = {"user_id": 7, "is_active": True}
    mock_create_accrual.side_effect = lambda *args, **kwargs: {
        "id": 5,
        "wallet_address": args[0],
        "reward_date": args[1],
        "user_id": 7,
        "staked_amount_wei": args[2],
        "credits": kwargs.get("credits", "0"),
        "rate_id": None,
    }
    mock_pay_pending.return_value = ("paid", Decimal("1.000000"))
    mock_create_epoch.return_value = {"epoch_date": "2026-09-12"}

    result = epoch.run_emission_epoch(date(2026, 9, 12))

    emission_wei = Decimal(result["emission_wei"])
    providers_wei = Decimal(result["split"]["providers_wei"])
    stakers_wei = Decimal(result["split"]["stakers_wei"])
    treasury_wei = Decimal(result["split"]["treasury_wei"])

    assert providers_wei + stakers_wei + treasury_wei == emission_wei
    assert int(result["provider_dust_wei"]) > 0
    assert int(result["staker_dust_wei"]) > 0
    # Uneven USD shares floor to micros too; allocated + dust == the pool.
    assert result["provider_dust_usd_micros"] > 0
    assert result["providers_usd_micros"] + result["provider_dust_usd_micros"] == 100_000_000
    usd_paid = sum(call.args[2] for call in mock_create_earning.call_args_list)
    assert usd_paid == result["providers_usd_micros"]
    assert Decimal(result["dust_wei"]) == Decimal(result["provider_dust_wei"]) + Decimal(
        result["staker_dust_wei"]
    )

    args, _kwargs = mock_create_epoch.call_args
    assert args[2] == int(providers_wei)
    assert args[3] == int(stakers_wei)
    assert args[4] == int(treasury_wei)
    assert args[2] + args[3] + args[4] == int(emission_wei)


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
    emission_wei = Decimal(result["emission_wei"])
    providers_wei = Decimal(result["split"]["providers_wei"])
    stakers_wei = Decimal(result["split"]["stakers_wei"])
    treasury_wei = Decimal(result["split"]["treasury_wei"])

    # Zero eligible providers AND zero staked wallets -- BOTH the
    # providers_wei and stakers_wei legs are entirely dust, so the whole
    # emission_wei lands in treasury and the persisted providers_wei/
    # stakers_wei are 0, not their nominal pre-dust amounts (HIGH
    # fix-round-1: previously this row would have reported providers_wei
    # still at its full 41% while ALSO folding that same amount into
    # treasury, double-counting it).
    assert providers_wei == 0
    assert stakers_wei == 0
    assert treasury_wei == emission_wei
    assert providers_wei + stakers_wei + treasury_wei == emission_wei
    assert Decimal(result["provider_dust_wei"]) == emission_wei * 4100 // 10000
    assert Decimal(result["staker_dust_wei"]) == emission_wei * 4100 // 10000


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
    assert summary["dust_wei"] == 0  # sole wallet -> 100% share, no floor remainder
    mock_create_accrual.assert_called_once()
    _args, kwargs = mock_create_accrual.call_args
    assert kwargs["skip_reason"] == "wayz_payout_not_implemented"
    assert kwargs["source"] == "emission"
    assert kwargs["status"] == "pending"


def test_pay_stakers_no_stake_returns_all_zero(emission_mode):
    """HIGH fix-round-1: with no staked wallets at all, the ENTIRE
    stakers_wei pool is unallocated dust for the caller to fold into
    treasury -- mirrors _score_and_persist_providers' "no eligible
    providers" handling."""
    with patch("src.services.emission.epoch.list_wallets_with_stake", return_value=[]):
        summary = epoch._pay_stakers("2026-09-12", 1_000_000)
    assert summary == {
        "paid": 0,
        "pending": 0,
        "skipped": 0,
        "credits_paid": "0",
        "capped": 0,
        "asset": "credits",
        "dust_wei": 1_000_000,
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
        "allocation_wei": "0",
        "allocation_usd_micros": 2_500_000,
    }
    mock_epoch_scores.return_value = [
        {"provider_id": 9, "share": "0.5"},
        {"provider_id": 1, "share": "0.2"},
        {"provider_id": 2, "share": "0.1"},
    ]
    view = epoch.get_provider_emission_view(1)
    assert view["rank"] == 2
    assert view["providers_scored"] == 3
    assert view["allocation_usd"] == "2.5"
    assert view["payout_asset"] == "ETH"


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
        "provider_pool_usd_per_day",
        "provider_payout_asset",
        "providers_bps",
        "stakers_bps",
        "treasury_bps",
        "last_epoch",
    }
    assert summary["last_epoch"] == "2026-09-12"
