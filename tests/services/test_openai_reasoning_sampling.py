"""OpenAI reasoning models reject non-default sampling values.

On gpt-5.5 (2026-11-26) temperature 0 or 0.5, top_p 0.9, presence_penalty and
frequency_penalty each failed with a 400, which the failover then reported as
"Model not found". Dropping the values lets the request succeed.
"""

import pytest

from src.services.providers.openai_client import _prepare_openai_kwargs
from src.services.providers.reasoning_effort import normalize_sampling_params

REJECTED = {
    "temperature": 0,
    "top_p": 0.9,
    "presence_penalty": 0.5,
    "frequency_penalty": 0.5,
}


@pytest.mark.parametrize("model", ["gpt-5.5", "openai/gpt-5", "gpt-5.4-mini", "o3", "gpt-6-sol"])
def test_reasoning_models_drop_rejected_values(model):
    out = normalize_sampling_params({**REJECTED, "max_tokens": 5}, "openai", model)
    assert out == {"max_tokens": 5}


def test_default_values_are_kept():
    out = normalize_sampling_params({"temperature": 1, "top_p": 1}, "openai", "gpt-5.5")
    assert out == {"temperature": 1, "top_p": 1}


@pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o-mini-2024-07-18", "openai/gpt-4.1"])
def test_non_reasoning_models_keep_every_value(model):
    out = normalize_sampling_params(dict(REJECTED), "openai", model)
    assert out == REJECTED


def test_openai_client_applies_it():
    out = _prepare_openai_kwargs("gpt-5.5", {**REJECTED, "max_tokens": 50})
    assert "temperature" not in out
    assert "top_p" not in out
    assert "presence_penalty" not in out
    assert "frequency_penalty" not in out
    # max_tokens is still renamed for reasoning models, as before
    assert "max_tokens" not in out
    assert out.get("max_completion_tokens") == 50


def test_openai_client_leaves_older_models_alone():
    out = _prepare_openai_kwargs("gpt-4.1", {**REJECTED, "max_tokens": 50})
    assert out["temperature"] == 0
    assert out["max_tokens"] == 50
