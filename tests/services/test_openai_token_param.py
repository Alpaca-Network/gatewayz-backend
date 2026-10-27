"""OpenAI's reasoning generations (o-series, GPT-5 and later) reject max_tokens."""

import pytest

from src.services.providers.reasoning_effort import (
    normalize_token_limit,
    uses_max_completion_tokens,
)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5.4-nano",
        "gpt-6-astra",
        "gpt-6-luna",
        "gpt-6-sol",
        "gpt-6.1-sol",
        "openai/gpt-6-sol",
        "o3",
        "gpt-10",
    ],
)
def test_reasoning_generations_use_max_completion_tokens(model):
    assert uses_max_completion_tokens("openai", model)
    assert normalize_token_limit({"max_tokens": 5}, "openai", model) == {"max_completion_tokens": 5}


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"])
def test_older_models_keep_max_tokens(model):
    assert not uses_max_completion_tokens("openai", model)
    assert normalize_token_limit({"max_tokens": 5}, "openai", model) == {"max_tokens": 5}


def test_other_gateways_untouched():
    assert not uses_max_completion_tokens("groq", "openai/gpt-oss-120b")
