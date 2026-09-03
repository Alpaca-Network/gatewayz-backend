"""Tests for src.services.gpu.earnings (gatewayz-backend#2266)."""

from unittest.mock import patch

import pytest

from src.services.gpu.earnings import (
    compute_amount_wei,
    model_class_for,
    record_earning_for_verified_work,
)


@pytest.fixture
def sb():
    return None


# ---------------------------------------------------------------------------
# model_class_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("community/llama-3.1-8b-instruct", "small"),
        ("community/qwen2.5-13b-instruct", "small"),
        ("community/qwen2.5-32b-instruct", "medium"),
        ("community/mixtral-34b", "medium"),
        ("community/llama-3.1-70b-instruct", "large"),
        ("community/deepseek-405b", "large"),
    ],
)
def test_model_class_for_buckets_by_param_count(sb, model_id, expected):
    assert model_class_for(model_id) == expected


def test_model_class_for_defaults_to_medium_when_unparseable(sb):
    assert model_class_for("community/mystery-model") == "medium"


def test_model_class_for_strips_the_community_prefix(sb):
    assert model_class_for("community/llama-3.1-8b-instruct") == model_class_for(
        "llama-3.1-8b-instruct"
    )


# ---------------------------------------------------------------------------
# compute_amount_wei
# ---------------------------------------------------------------------------


def test_compute_amount_wei_integer_math(sb):
    # 1500 tokens * 1000 wei/1k = 1500 wei, exactly.
    assert compute_amount_wei(1000, 500, 1000) == 1500


def test_compute_amount_wei_floors_the_remainder(sb):
    # 999 tokens * 1000 / 1000 = 999 exactly; 999 tokens * 7 / 1000 must floor.
    assert compute_amount_wei(500, 499, 7) == (999 * 7) // 1000
    assert compute_amount_wei(500, 499, 7) == 6  # 6993 // 1000 == 6, not 6.993 rounded


def test_compute_amount_wei_handles_wei_scale_rates(sb):
    rate = 500_000_000_000_000_000  # 0.5 WAYZ (wei) per 1k tokens
    assert compute_amount_wei(2000, 0, rate) == 1_000_000_000_000_000_000


# ---------------------------------------------------------------------------
# record_earning_for_verified_work
# ---------------------------------------------------------------------------


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_computes_and_creates(mock_get_rate, mock_create, sb):
    mock_get_rate.return_value = 1000
    mock_create.return_value = {"id": 1, "status": "accrued"}

    work = {
        "id": 10,
        "provider_id": 5,
        "model": "community/llama-3.1-8b-instruct",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    }
    result = record_earning_for_verified_work(work)

    assert result == {"id": 1, "status": "accrued"}
    mock_get_rate.assert_called_once_with("small")
    mock_create.assert_called_once_with(5, 10, 1500)


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
    result = record_earning_for_verified_work(work)

    assert result is None
    mock_create.assert_not_called()


@patch("src.services.gpu.earnings.create_earning")
@patch("src.services.gpu.earnings.get_payout_rate_wei_per_1k")
def test_record_earning_treats_missing_token_counts_as_zero(mock_get_rate, mock_create, sb):
    mock_get_rate.return_value = 1000
    mock_create.return_value = {"id": 1}

    work = {"id": 10, "provider_id": 5, "model": "community/llama-3.1-8b-instruct"}
    record_earning_for_verified_work(work)

    mock_create.assert_called_once_with(5, 10, 0)
