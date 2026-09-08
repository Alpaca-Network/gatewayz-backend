"""Tests for src.services.gpu.earnings (gatewayz-backend#2266; PR #2288
review fix round 1, C1)."""

from unittest.mock import patch

import pytest

from src.services.gpu.earnings import (
    compute_amount_wei,
    effective_model_class,
    model_class_for,
    next_tier_min_tokens_7d,
    record_earning_for_verified_work,
    tier_multiplier_bps,
)

# The standard 5-row tier table (m4/spec.md §5's seeded testnet placeholders).
_TIERS = [
    {"min_tokens_7d": 0, "multiplier_bps": 500, "label": "bot"},
    {"min_tokens_7d": 100_000, "multiplier_bps": 2500, "label": "small"},
    {"min_tokens_7d": 1_000_000, "multiplier_bps": 6000, "label": "medium"},
    {"min_tokens_7d": 10_000_000, "multiplier_bps": 10000, "label": "large"},
    {"min_tokens_7d": 100_000_000, "multiplier_bps": 15000, "label": "whale"},
]


@pytest.fixture
def sb():
    return None


# ---------------------------------------------------------------------------
# model_class_for (allow-list only, C1 regression coverage)
# ---------------------------------------------------------------------------


def test_model_class_for_matches_allow_listed_ids(sb):
    assert model_class_for("community/llama-3.1-8b-instruct") == "small"
    assert model_class_for("community/qwen2.5-32b-instruct") == "medium"
    assert model_class_for("community/llama-3.1-70b-instruct") == "large"


def test_model_class_for_returns_none_for_unknown_id(sb):
    """C1 regression: '-70b' in the name of an unknown model must NOT earn
    the large rate -- the old regex-based classifier would have paid this
    at 'large'; the allow-list must return None (not payable) instead."""
    assert model_class_for("community/totally-not-real-70b-model") is None


# ---------------------------------------------------------------------------
# effective_model_class (testnet safety cap)
# ---------------------------------------------------------------------------


def test_effective_class_unknown_model_is_never_payable(sb):
    assert (
        effective_model_class(
            "community/fake-70b", attested=True, reference_provider_configured=True
        )
        is None
    )


def test_effective_class_small_model_always_pays_small(sb):
    assert (
        effective_model_class(
            "community/llama-3.1-8b-instruct", attested=False, reference_provider_configured=False
        )
        == "small"
    )


def test_effective_class_large_model_capped_to_small_when_unattested(sb):
    assert (
        effective_model_class(
            "community/llama-3.1-70b-instruct", attested=False, reference_provider_configured=True
        )
        == "small"
    )


def test_effective_class_large_model_capped_to_small_when_no_reference_provider(sb):
    assert (
        effective_model_class(
            "community/llama-3.1-70b-instruct", attested=True, reference_provider_configured=False
        )
        == "small"
    )


def test_effective_class_large_model_pays_large_when_attested_and_referenced(sb):
    assert (
        effective_model_class(
            "community/llama-3.1-70b-instruct", attested=True, reference_provider_configured=True
        )
        == "large"
    )


# ---------------------------------------------------------------------------
# tier_multiplier_bps
# ---------------------------------------------------------------------------


def test_tier_multiplier_bps_at_zero_volume_is_bottom_tier(sb):
    assert tier_multiplier_bps(0, _TIERS) == 500


def test_tier_multiplier_bps_just_below_a_threshold_stays_in_lower_tier(sb):
    assert tier_multiplier_bps(99_999, _TIERS) == 500


def test_tier_multiplier_bps_exact_threshold_advances_the_tier(sb):
    assert tier_multiplier_bps(100_000, _TIERS) == 2500
    assert tier_multiplier_bps(1_000_000, _TIERS) == 6000
    assert tier_multiplier_bps(10_000_000, _TIERS) == 10000
    assert tier_multiplier_bps(100_000_000, _TIERS) == 15000


def test_tier_multiplier_bps_above_top_tier_stays_at_top(sb):
    assert tier_multiplier_bps(999_999_999_999, _TIERS) == 15000


def test_tier_multiplier_bps_empty_tiers_pays_full_rate(sb):
    """No tiers configured -- pay 1.0x (10000 bps), not zero."""
    assert tier_multiplier_bps(500, []) == 10000


def test_tier_multiplier_bps_order_independent(sb):
    """get_payout_tiers() sorts ascending, but this must not assume that --
    it's a pure function over whatever list it's handed."""
    shuffled = list(reversed(_TIERS))
    assert tier_multiplier_bps(1_500_000, shuffled) == 6000


# ---------------------------------------------------------------------------
# next_tier_min_tokens_7d
# ---------------------------------------------------------------------------


def test_next_tier_min_tokens_7d_reports_the_next_threshold(sb):
    assert next_tier_min_tokens_7d(0, _TIERS) == 100_000
    assert next_tier_min_tokens_7d(99_999, _TIERS) == 100_000
    assert next_tier_min_tokens_7d(100_000, _TIERS) == 1_000_000


def test_next_tier_min_tokens_7d_is_none_at_top_tier(sb):
    assert next_tier_min_tokens_7d(100_000_000, _TIERS) is None
    assert next_tier_min_tokens_7d(999_999_999, _TIERS) is None


def test_next_tier_min_tokens_7d_empty_tiers_is_none(sb):
    assert next_tier_min_tokens_7d(0, []) is None


# ---------------------------------------------------------------------------
# compute_amount_wei
# ---------------------------------------------------------------------------


def test_compute_amount_wei_integer_math(sb):
    assert compute_amount_wei(1000, 500, 1000) == 1500


def test_compute_amount_wei_floors_the_remainder(sb):
    assert compute_amount_wei(500, 499, 7) == (999 * 7) // 1000
    assert compute_amount_wei(500, 499, 7) == 6


def test_compute_amount_wei_handles_wei_scale_rates(sb):
    rate = 500_000_000_000_000_000  # 0.5 WAYZ (wei) per 1k tokens
    assert compute_amount_wei(2000, 0, rate) == 1_000_000_000_000_000_000


def test_compute_amount_wei_defaults_to_full_multiplier(sb):
    """No multiplier_bps passed -- behaves exactly like the pre-tier
    signature (1.0x), so every existing caller/test above is unaffected."""
    assert compute_amount_wei(1000, 500, 1000, multiplier_bps=10000) == 1500


def test_compute_amount_wei_applies_a_fractional_multiplier(sb):
    # base = (1000 * 1000) // 1000 = 1000; 0.25x -> 250
    assert compute_amount_wei(1000, 0, 1000, multiplier_bps=2500) == 250


def test_compute_amount_wei_applies_a_bonus_multiplier_above_1x(sb):
    # base = (1000 * 1000) // 1000 = 1000; 1.5x -> 1500
    assert compute_amount_wei(1000, 0, 1000, multiplier_bps=15000) == 1500


def test_compute_amount_wei_floors_the_multiplier_step_too(sb):
    # base = (999 * 7) // 1000 = 6 (from test_compute_amount_wei_floors_the_remainder);
    # 6 * 500 // 10000 = 0.3 -> floors to 0, not rounded up.
    assert compute_amount_wei(500, 499, 7, multiplier_bps=500) == 0


# ---------------------------------------------------------------------------
# record_earning_for_verified_work
# ---------------------------------------------------------------------------


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_computes_and_creates_at_small_rate_by_default(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    """No attestation, no reference provider configured (the default) --
    even though the model is 'small' anyway here, this exercises the
    common unattested path end to end. No tiers configured -- full 1.0x
    multiplier."""
    mock_get_rate.return_value = 1000
    mock_volume.return_value = 0
    mock_tiers.return_value = []
    mock_create.return_value = ({"id": 1, "status": "accrued"}, "created")

    work = {
        "id": 10,
        "provider_id": 5,
        "model": "community/llama-3.1-8b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "attested": False,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        result = record_earning_for_verified_work(work)

    assert result.outcome == "created"
    assert result.earning == {"id": 1, "status": "accrued"}
    mock_get_rate.assert_called_once_with("small")
    # volume_7d excludes this work item, then the caller adds its own
    # tokens back in -- see get_provider_verified_volume_7d's docstring.
    mock_volume.assert_called_once_with(5, exclude_work_id=10)
    mock_create.assert_called_once_with(
        5, 10, 1500, multiplier_bps=10000, volume_7d_at_accrual=1500
    )


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_caps_large_model_to_small_rate_when_unattested(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    """C1 safety-cap regression: a known LARGE model, unattested work item
    -- must be rated at 'small' even though a reference provider IS
    configured, because attestation is also required."""
    mock_get_rate.return_value = 1000
    mock_volume.return_value = 0
    mock_tiers.return_value = []
    mock_create.return_value = ({"id": 1}, "created")

    work = {
        "id": 11,
        "provider_id": 5,
        "model": "community/llama-3.1-70b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 0,
        "attested": False,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = "together"
        record_earning_for_verified_work(work)

    mock_get_rate.assert_called_once_with("small")


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_pays_large_rate_when_attested_and_referenced(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    """C1 safety-cap: attested work + a configured reference provider
    unlocks the model's real (large) class."""
    mock_get_rate.return_value = 250_000_000_000_000_000
    mock_volume.return_value = 0
    mock_tiers.return_value = []
    mock_create.return_value = ({"id": 1}, "created")

    work = {
        "id": 12,
        "provider_id": 5,
        "model": "community/llama-3.1-70b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 0,
        "attested": True,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = "together"
        record_earning_for_verified_work(work)

    mock_get_rate.assert_called_once_with("large")


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_applies_the_tier_multiplier_for_high_volume_providers(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    """A provider with a large trailing-7d verified volume gets the higher
    tier's multiplier applied on top of the model-class rate."""
    mock_get_rate.return_value = 1000
    mock_volume.return_value = 999_999  # excluding this row
    mock_tiers.return_value = _TIERS
    mock_create.return_value = ({"id": 1}, "created")

    work = {
        "id": 20,
        "provider_id": 7,
        "model": "community/llama-3.1-8b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 0,
        "attested": False,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        record_earning_for_verified_work(work)

    # total volume including this row = 999_999 + 1000 = 1_000_999 -> medium tier (6000 bps).
    # base amount = (1000 * 1000) // 1000 = 1000; 1000 * 6000 // 10000 = 600.
    mock_create.assert_called_once_with(
        7, 20, 600, multiplier_bps=6000, volume_7d_at_accrual=1_000_999
    )


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_volume_lookup_is_scoped_to_provider_not_node(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    """Splitting traffic across many nodes under one provider must not
    change the tier lookup -- it's keyed by provider_id, and node_id is
    never passed to get_provider_verified_volume_7d."""
    mock_get_rate.return_value = 1000
    mock_volume.return_value = 0
    mock_tiers.return_value = []
    mock_create.return_value = ({"id": 1}, "created")

    work = {
        "id": 21,
        "provider_id": 8,
        "node_id": 999,
        "model": "community/llama-3.1-8b-instruct",
        "prompt_tokens": 100,
        "completion_tokens": 0,
        "attested": False,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        record_earning_for_verified_work(work)

    mock_volume.assert_called_once_with(8, exclude_work_id=21)


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_unknown_model_earns_nothing(mock_get_rate, mock_create, sb):
    """C1 regression: '-70b' in the name of an UNKNOWN model id earns
    nothing at all -- not even at the small rate -- and never reaches the
    rate lookup or the DB insert."""
    work = {
        "id": 13,
        "provider_id": 5,
        "model": "community/definitely-a-70b-model",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "attested": True,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = "together"
        result = record_earning_for_verified_work(work)

    assert result.outcome == "not_payable"
    assert result.earning is None
    mock_get_rate.assert_not_called()
    mock_create.assert_not_called()


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_skips_when_rate_unseeded(mock_get_rate, mock_create, sb):
    mock_get_rate.return_value = None

    work = {
        "id": 10,
        "provider_id": 5,
        "model": "community/llama-3.1-8b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        result = record_earning_for_verified_work(work)

    assert result.outcome == "rate_unseeded"
    assert result.earning is None
    mock_create.assert_not_called()


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_tiers")
@patch("src.services.gpu.earnings.get_provider_verified_volume_7d")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_treats_missing_token_counts_as_zero(
    mock_get_rate, mock_volume, mock_tiers, mock_create, sb
):
    mock_get_rate.return_value = 1000
    mock_volume.return_value = 0
    mock_tiers.return_value = []
    mock_create.return_value = ({"id": 1}, "created")

    work = {"id": 10, "provider_id": 5, "model": "community/llama-3.1-8b-instruct"}
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        record_earning_for_verified_work(work)

    mock_create.assert_called_once_with(5, 10, 0, multiplier_bps=10000, volume_7d_at_accrual=0)
