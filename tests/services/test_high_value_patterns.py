"""The high-value guard must not drift behind the model generations.

The list it replaces held "claude-sonnet-4" and therefore stopped covering
Anthropic the day claude-sonnet-5 shipped — the OLDER model was protected
from under-billing while the current one was not. It was also copy-pasted in
three places in this module, so widening it meant remembering all three.
"""

from __future__ import annotations

import pytest

from src.services.pricing.pricing import is_high_value_model


@pytest.mark.parametrize(
    "model_id",
    [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-haiku-4-5-20251001",
        "anthropic/claude-3-opus",
        "openai/gpt-5",
        "openai/gpt-4o-mini",
        "openai/o3-mini",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-pro-preview-09-2025",
        "cohere/command-r-plus",
        "mistralai/mixtral-8x22b",
    ],
)
def test_frontier_models_of_every_generation_are_high_value(model_id):
    assert is_high_value_model(model_id) is True


@pytest.mark.parametrize(
    "model_id",
    [
        "meta-llama/llama-3.1-8b-instruct",
        "some-vendor/tiny-7b",
        "",
    ],
)
def test_commodity_models_are_not_high_value(model_id):
    assert is_high_value_model(model_id) is False


def test_free_variants_are_exempt():
    # A :free id has no revenue to lose, so blocking it would only break a
    # legitimately free model.
    assert is_high_value_model("anthropic/claude-sonnet-5:free") is False


def test_normalized_id_is_also_consulted():
    # A provider-transformed id whose vendor identity only shows after
    # normalization must still be protected.
    assert (
        is_high_value_model(
            "accounts/fireworks/models/claude-sonnet-5", "anthropic/claude-sonnet-5"
        )
        is True
    )


def test_the_regression_that_started_this():
    # claude-sonnet-4-6 matched the old list only by the accident of
    # "claude-sonnet-4" being a substring; claude-sonnet-5 matched nothing.
    # Both must be covered, by the same rule.
    assert is_high_value_model("claude-sonnet-4-6") is True
    assert is_high_value_model("claude-sonnet-5") is True
