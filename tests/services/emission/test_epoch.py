"""Tests for src.services.emission.epoch (score-based GPU provider
emission rewards; the emission is split between GPU providers and the
treasury -- a staker share existed in an earlier design and was removed on
2026-11-25). DB access is mocked at the point of use in epoch.py, same
convention as tests/routes/test_gpu_earnings.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

import src.services.emission.epoch as epoch
from src.config.config import Config


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
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", 5900)
    monkeypatch.setattr(epoch.Config, "PROVIDER_EMISSION_USD_PER_DAY", "100")
    monkeypatch.setattr(epoch.Config, "COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER", None)


def _work_row(provider_id: int, tokens: int, hour: int = 10) -> dict:
    return {
        "provider_id": provider_id,
        "node_id": provider_id,
        "model": "llama-3.1-8b-instruct",
        "prompt_tokens": tokens,
        "completion_tokens": 0,
        "latency_ms": 200,
        "attested": False,
        "created_at": f"2026-09-12T{hour:02d}:00:00+00:00",
    }


@pytest.fixture
def epoch_db(emission_mode):
    """Every DB call run_emission_epoch makes, mocked at the point of use.
    Defaults describe an empty network; tests set return values."""
    names = [
        "get_epoch",
        "list_verified_work_window",
        "list_approved_providers",
        "list_active_nodes",
        "get_payout_tiers",
        "create_provider_scores",
        "create_emission_earning",
        "create_epoch",
    ]
    patchers = {name: patch(f"src.services.emission.epoch.{name}") for name in names}
    mocks = {name: patcher.start() for name, patcher in patchers.items()}
    mocks["get_epoch"].return_value = None
    mocks["list_verified_work_window"].return_value = []
    mocks["list_approved_providers"].return_value = []
    mocks["list_active_nodes"].return_value = []
    mocks["get_payout_tiers"].return_value = []
    mocks["create_provider_scores"].return_value = True
    mocks["create_emission_earning"].return_value = ({"id": 99}, "created")
    mocks["create_epoch"].return_value = {"epoch_date": "2026-09-12"}
    yield mocks
    for patcher in patchers.values():
        patcher.stop()


# ---------------------------------------------------------------------------
# The staker share is gone
# ---------------------------------------------------------------------------


def test_no_staker_symbol_remains_in_the_epoch_module():
    """Nothing named *staker* / *stake* is importable from epoch.py -- the
    staker leg (payout, view, stale-sync guard) was removed 2026-11-25."""
    leftovers = [name for name in dir(epoch) if "staker" in name.lower()]
    assert leftovers == []
    for name in (
        "_pay_stakers",
        "_pay_stakers_wayz",
        "_process_emission_wallet",
        "get_staker_emission_view",
        "is_stake_sync_stale",
        "StakingRewardsStaleError",
        "list_wallets_with_stake",
        "pay_pending_accrual",
        "create_accrual",
        "get_accrual",
    ):
        assert not hasattr(epoch, name), name


def test_staker_config_is_gone():
    for name in (
        "EMISSION_SPLIT_STAKERS_BPS",
        "STAKER_REWARD_ASSET",
        "WAYZ_CREDIT_RATE",
        "STAKING_REWARDS_DAILY_CAP_CREDITS",
        "STAKING_REWARDS_MIN_CREDITS",
    ):
        assert not hasattr(Config, name), name


def test_default_split_is_providers_4100_treasury_5900():
    """The config defaults (read from source, so a local env override can't
    mask them): the treasury default absorbed the former 4100 bps staker
    default, so the two sum to 10000."""
    import src.config.config as config_module

    with open(config_module.__file__) as f:
        text = f.read()
    assert '_get_env_var("EMISSION_SPLIT_PROVIDERS_BPS", "4100")' in text
    assert '_get_env_var("EMISSION_SPLIT_TREASURY_BPS", "5900")' in text
    assert "EMISSION_SPLIT_STAKERS_BPS" not in text
    assert 4100 + 5900 == 10000


# ---------------------------------------------------------------------------
# check_emission_config
# ---------------------------------------------------------------------------


def test_check_emission_config_valid_sums_enable_the_job(emission_mode):
    epoch.check_emission_config()
    assert epoch.is_emission_job_disabled() is None


@pytest.mark.parametrize(
    ("providers_bps", "treasury_bps"),
    [
        (4100, 1800),  # the OLD treasury value with the staker leg gone: 5900
        (4100, 5899),
        (4100, 5901),
        (0, 0),
        (10000, 10000),
        (5000, 4000),
    ],
)
def test_check_emission_config_rejects_a_split_that_does_not_sum_to_10000(
    emission_mode, monkeypatch, providers_bps, treasury_bps
):
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_PROVIDERS_BPS", providers_bps)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", treasury_bps)
    epoch.check_emission_config()
    reason = epoch.is_emission_job_disabled()
    assert reason is not None
    assert str(providers_bps + treasury_bps) in reason
    assert "STAKERS" not in reason
    # ...and the job then refuses to run rather than split off a bad config.
    result = epoch.run_emission_epoch(date(2026, 9, 12))
    assert result == {"skipped": "bad_config", "reason": reason}


@pytest.mark.parametrize(
    ("providers_bps", "treasury_bps"), [(4100, 5900), (0, 10000), (10000, 0), (2500, 7500)]
)
def test_check_emission_config_accepts_any_two_way_split_summing_to_10000(
    emission_mode, monkeypatch, providers_bps, treasury_bps
):
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_PROVIDERS_BPS", providers_bps)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", treasury_bps)
    epoch.check_emission_config()
    assert epoch.is_emission_job_disabled() is None


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


def test_run_emission_epoch_per_unit_mode_touches_no_db(monkeypatch):
    """per_unit (the production default) must stay a pure no-op."""
    monkeypatch.setattr(epoch.Config, "REWARDS_MODE", "per_unit")
    with (
        patch("src.services.emission.epoch.get_epoch") as mock_get_epoch,
        patch("src.services.emission.epoch.create_epoch") as mock_create_epoch,
        patch("src.services.emission.epoch.create_emission_earning") as mock_earning,
    ):
        epoch.run_emission_epoch(date(2026, 9, 12))
    mock_get_epoch.assert_not_called()
    mock_create_epoch.assert_not_called()
    mock_earning.assert_not_called()


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


@patch("src.services.emission.epoch.get_epoch")
def test_run_emission_epoch_already_run_returns_an_old_row_with_staker_columns(
    mock_get_epoch, emission_mode
):
    """Rows written before 2026-11-25 carry nonzero stakers_wei /
    stakers_paid and staker keys in `summary`; they are returned as stored."""
    old_row = {
        "epoch_date": "2026-09-12",
        "status": "allocated",
        "emission_wei": str(100000 * 10**18),
        "providers_wei": "0",
        "stakers_wei": str(41000 * 10**18),
        "treasury_wei": str(59000 * 10**18),
        "stakers_paid": 2,
        "summary": {"stakers_paid": 2, "staker_asset": "credits"},
    }
    mock_get_epoch.return_value = old_row
    result = epoch.run_emission_epoch(date(2026, 9, 12))
    assert result == {"skipped": "already_run", "epoch_date": "2026-09-12", "epoch": old_row}


# ---------------------------------------------------------------------------
# run_emission_epoch -- happy path
# ---------------------------------------------------------------------------

_STAKER_SUMMARY_KEYS = {
    "stakers_paid",
    "stakers_pending",
    "stakers_skipped",
    "credits_paid",
    "staker_asset",
    "staker_dust_wei",
}


def test_run_emission_epoch_happy_path(epoch_db):
    epoch_db["list_verified_work_window"].return_value = [
        {**_work_row(1, 1000, hour=10), "completion_tokens": 500},
        {**_work_row(1, 2000, hour=11), "completion_tokens": 1000, "latency_ms": 220},
    ]
    epoch_db["list_approved_providers"].return_value = [{"id": 1}]

    result = epoch.run_emission_epoch(date(2026, 9, 12))

    assert result["epoch_date"] == "2026-09-12"
    assert result["providers_scored"] == 1
    assert set(result) == {
        "epoch_date",
        "emission_wayz",
        "emission_wei",
        "split",
        "providers_scored",
        "top_share",
        "dust_wei",
        "provider_dust_wei",
        "provider_payout_asset",
        "provider_pool_usd",
        "providers_usd_micros",
        "provider_dust_usd_micros",
    }
    assert not (_STAKER_SUMMARY_KEYS & set(result))
    assert set(result["split"]) == {"providers_wei", "treasury_wei"}

    # The sole eligible provider (share=1.0) gets the full USD pool
    # ($100/day -> 100_000_000 micros), paid later in ETH on Base. The
    # WAYZ providers leg is not emitted (providers are paid in USD/ETH),
    # so it's recorded as providers_wei=0 and lands in treasury -- the
    # two-way WAYZ split sums to exactly emission_wei.
    emission_wei = int(result["emission_wei"])
    providers_wei = int(result["split"]["providers_wei"])
    treasury_wei = int(result["split"]["treasury_wei"])
    assert emission_wei == 100000 * 10**18
    assert providers_wei + treasury_wei == emission_wei
    assert providers_wei == 0
    assert treasury_wei == emission_wei
    assert int(result["provider_dust_wei"]) == emission_wei * 4100 // 10000
    assert result["dust_wei"] == result["provider_dust_wei"]
    assert result["provider_payout_asset"] == "ETH"
    assert result["providers_usd_micros"] == 100_000_000
    assert result["provider_dust_usd_micros"] == 0

    epoch_db["create_emission_earning"].assert_called_once()
    called_provider_id, called_epoch_date, called_amount = epoch_db[
        "create_emission_earning"
    ].call_args[0]
    assert called_provider_id == 1
    assert called_epoch_date == "2026-09-12"
    assert called_amount == 100_000_000  # USD micros

    rows = epoch_db["create_provider_scores"].call_args[0][0]
    assert rows[0]["allocation_usd_micros"] == 100_000_000
    assert rows[0]["allocation_wei"] == "0"

    epoch_db["create_epoch"].assert_called_once()
    args, kwargs = epoch_db["create_epoch"].call_args
    # create_epoch(epoch_date, emission_wei, providers_wei, treasury_wei, providers_scored)
    assert args == ("2026-09-12", emission_wei, providers_wei, treasury_wei, 1)
    assert kwargs["status"] == "allocated"
    assert kwargs["providers_usd_micros"] == 100_000_000
    assert kwargs["summary"] == result


@pytest.mark.parametrize(
    ("emission_wayz", "providers_bps"),
    [
        ("100000", 4100),
        ("3", 4100),
        ("0.000000000000000001", 4100),  # 1 wei: the providers leg floors to 0
        ("0.000000000000000007", 4100),  # 7 wei: 2 / 5 with floor dust
        ("99999.999999999999999999", 3333),
        ("123456789.123456789123456789", 9999),
        ("0", 4100),
        ("100000", 0),
        ("100000", 10000),
    ],
)
def test_run_emission_epoch_providers_plus_treasury_is_exactly_emission_wei(
    epoch_db, monkeypatch, emission_wayz, providers_bps
):
    """The invariant, exact, with three providers splitting unevenly (USD
    floor dust > 0): providers_wei + treasury_wei == emission_wei, both in
    the returned summary and in what is persisted."""
    monkeypatch.setattr(epoch.Config, "WAYZ_DAILY_EMISSION", emission_wayz)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_PROVIDERS_BPS", providers_bps)
    monkeypatch.setattr(epoch.Config, "EMISSION_SPLIT_TREASURY_BPS", 10000 - providers_bps)
    epoch_db["list_verified_work_window"].return_value = [
        _work_row(pid, tokens) for pid, tokens in [(1, 1000), (2, 333), (3, 111)]
    ]
    epoch_db["list_approved_providers"].return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

    result = epoch.run_emission_epoch(date(2026, 9, 12))

    emission_wei = int(result["emission_wei"])
    providers_wei = int(result["split"]["providers_wei"])
    treasury_wei = int(result["split"]["treasury_wei"])
    assert emission_wei == epoch.split_emission(emission_wayz, 0, 0).emission_wei
    assert providers_wei + treasury_wei == emission_wei
    # Same dust rule as before the staker removal: the WAYZ providers leg
    # is not emitted, it is all "dust" that lands in treasury.
    assert providers_wei == 0
    assert int(result["provider_dust_wei"]) == emission_wei * providers_bps // 10000
    assert int(result["dust_wei"]) == int(result["provider_dust_wei"])

    # Uneven USD shares floor to micros too; allocated + dust == the pool.
    assert result["provider_dust_usd_micros"] > 0
    assert result["providers_usd_micros"] + result["provider_dust_usd_micros"] == 100_000_000
    usd_paid = sum(call.args[2] for call in epoch_db["create_emission_earning"].call_args_list)
    assert usd_paid == result["providers_usd_micros"]

    args, _kwargs = epoch_db["create_epoch"].call_args
    assert args[1] == emission_wei
    assert args[2] == providers_wei
    assert args[3] == treasury_wei
    assert args[2] + args[3] == args[1]


def test_run_emission_epoch_no_eligible_providers_dusts_treasury(epoch_db):
    result = epoch.run_emission_epoch(date(2026, 9, 12))

    assert result["providers_scored"] == 0
    emission_wei = int(result["emission_wei"])
    providers_wei = int(result["split"]["providers_wei"])
    treasury_wei = int(result["split"]["treasury_wei"])

    # Zero eligible providers: the whole emission lands in treasury and the
    # whole USD pool goes unallocated.
    assert providers_wei == 0
    assert treasury_wei == emission_wei
    assert int(result["provider_dust_wei"]) == emission_wei * 4100 // 10000
    assert result["providers_usd_micros"] == 0
    assert result["provider_dust_usd_micros"] == 100_000_000
    epoch_db["create_emission_earning"].assert_not_called()


def test_run_emission_epoch_reads_no_stake_data(epoch_db):
    """The job no longer depends on the on-chain stake sync: it runs with
    wallet_stakes unreachable and never raises a stale-sync error."""
    with patch(
        "src.db.wallet_stakes.get_supabase_client", side_effect=AssertionError("stake read")
    ):
        result = epoch.run_emission_epoch(date(2026, 9, 12))
    assert result["epoch_date"] == "2026-09-12"


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
        "treasury_bps",
        "last_epoch",
    }
    assert summary["last_epoch"] == "2026-09-12"
    assert summary["providers_bps"] + summary["treasury_bps"] == 10000
    assert not [key for key in summary if "staker" in key]


@patch("src.services.gpu.payout_views.get_display_eth_usd_price")
@patch("src.services.emission.epoch.list_provider_scores_for_epoch")
@patch("src.services.emission.epoch.get_latest_provider_score")
def test_provider_emission_view_keeps_allocation_wayz_as_an_eth_equivalent(
    mock_latest, mock_epoch_scores, mock_price
):
    """PR #2364 review #3: existing clients read `allocation_wayz` -- it
    stays meaningful (the USD allocation's ETH equivalent at the current
    trusted price), never zeroed or removed."""
    from src.services.chain.eth_payout_client import EthUsdPrice

    mock_price.return_value = EthUsdPrice(answer=3000 * 10**8, decimals=8, updated_at=0)
    mock_latest.return_value = {
        "epoch_date": "2026-09-22",
        "compute": "1",
        "speed": "1",
        "availability": "1",
        "unique_models": "1",
        "raw_score": "1",
        "adjusted_score": "1",
        "share": "1",
        "allocation_wei": "0",
        "allocation_usd_micros": 30_000_000,
    }
    mock_epoch_scores.return_value = [{"provider_id": 1, "share": "1"}]

    view = epoch.get_provider_emission_view(1)

    assert view["allocation_usd"] == "30"
    assert view["allocation_eth"] == "0.01"
    assert view["allocation_wayz"] == "0.01"


@patch("src.services.emission.epoch.list_provider_scores_for_epoch")
@patch("src.services.emission.epoch.get_latest_provider_score")
def test_provider_emission_view_legacy_wayz_epoch_keeps_its_wayz_amount(
    mock_latest, mock_epoch_scores
):
    mock_latest.return_value = {
        "epoch_date": "2026-09-12",
        "compute": "1",
        "speed": "1",
        "availability": "1",
        "unique_models": "1",
        "raw_score": "1",
        "adjusted_score": "1",
        "share": "1",
        "allocation_wei": str(2 * 10**18),
    }
    mock_epoch_scores.return_value = [{"provider_id": 1, "share": "1"}]

    view = epoch.get_provider_emission_view(1)

    assert view["allocation_wayz"] == "2"
    assert view["allocation_usd"] is None
