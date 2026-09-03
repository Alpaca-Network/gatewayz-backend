"""Tests for src.services.gpu.earnings (gatewayz-backend#2266; PR #2288
review fix round 1, C1)."""

from unittest.mock import patch

import pytest

from src.services.gpu.earnings import (
    compute_amount_wei,
    effective_model_class,
    model_class_for,
    record_earning_for_verified_work,
)


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


# ---------------------------------------------------------------------------
# record_earning_for_verified_work
# ---------------------------------------------------------------------------


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_computes_and_creates_at_small_rate_by_default(
    mock_get_rate, mock_create, sb
):
    """No attestation, no reference provider configured (the default) --
    even though the model is 'small' anyway here, this exercises the
    common unattested path end to end."""
    mock_get_rate.return_value = 1000
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
    mock_create.assert_called_once_with(5, 10, 1500)


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_caps_large_model_to_small_rate_when_unattested(
    mock_get_rate, mock_create, sb
):
    """C1 safety-cap regression: a known LARGE model, unattested work item
    -- must be rated at 'small' even though a reference provider IS
    configured, because attestation is also required."""
    mock_get_rate.return_value = 1000
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
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_pays_large_rate_when_attested_and_referenced(
    mock_get_rate, mock_create, sb
):
    """C1 safety-cap: attested work + a configured reference provider
    unlocks the model's real (large) class."""
    mock_get_rate.return_value = 250_000_000_000_000_000
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
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_treats_missing_token_counts_as_zero(mock_get_rate, mock_create, sb):
    mock_get_rate.return_value = 1000
    mock_create.return_value = ({"id": 1}, "created")

    work = {"id": 10, "provider_id": 5, "model": "community/llama-3.1-8b-instruct"}
    with patch("src.services.gpu.earnings.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER = None
        record_earning_for_verified_work(work)

    mock_create.assert_called_once_with(5, 10, 0)
