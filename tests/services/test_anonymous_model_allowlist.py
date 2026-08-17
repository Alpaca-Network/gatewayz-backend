"""Regression test: anonymous model allowlist must not hard-require a ":free" suffix.

is_model_allowed_for_anonymous() used to reject any model_id that didn't end in
":free" before even checking is_free_model(). Production's actual free-model
catalog (is_free=true rows) doesn't reliably carry that suffix — e.g.
"unsloth/gemma-2-9b-it" — so the suffix check rejected every real free model
and made anonymous chat fully unusable, not just paid-model-restricted.

is_free_model() is the real gate (see test_is_free_model.py); this suffix
requirement was a redundant, wrong precondition on top of it.
"""

from unittest.mock import patch

from src.services.anonymous_rate_limiter import (
    ANONYMOUS_ALLOWED_MODELS,
    get_anonymous_allowed_models_sample,
    is_model_allowed_for_anonymous,
)


def test_free_model_without_free_suffix_is_allowed():
    with patch(
        "src.services.model_capabilities_cache.is_free_model",
        return_value=True,
    ):
        assert is_model_allowed_for_anonymous("unsloth/gemma-2-9b-it") is True


def test_free_model_with_free_suffix_is_allowed():
    with patch(
        "src.services.model_capabilities_cache.is_free_model",
        return_value=True,
    ):
        assert is_model_allowed_for_anonymous("google/gemini-2.0-flash-exp:free") is True


def test_paid_model_is_rejected_regardless_of_suffix():
    with patch(
        "src.services.model_capabilities_cache.is_free_model",
        return_value=False,
    ):
        assert is_model_allowed_for_anonymous("openai/gpt-4o") is False
        assert is_model_allowed_for_anonymous("openai/gpt-4o:free") is False


def test_empty_model_id_is_rejected():
    assert is_model_allowed_for_anonymous("") is False
    assert is_model_allowed_for_anonymous(None) is False  # type: ignore[arg-type]


def test_falls_back_to_hardcoded_whitelist_when_cache_import_fails():
    with patch(
        "src.services.model_capabilities_cache.is_free_model",
        side_effect=ImportError("cache unavailable"),
    ):
        known = ANONYMOUS_ALLOWED_MODELS[0]
        assert is_model_allowed_for_anonymous(known) is True
        assert is_model_allowed_for_anonymous("openai/gpt-4o") is False


def test_allowed_models_sample_prefers_live_free_catalog():
    with patch(
        "src.services.model_capabilities_cache.get_free_models",
        return_value={"unsloth/gemma-2-9b-it", "unsloth/llama-3.2-3b-instruct"},
    ):
        sample = get_anonymous_allowed_models_sample(5)
        assert set(sample) == {"unsloth/gemma-2-9b-it", "unsloth/llama-3.2-3b-instruct"}


def test_allowed_models_sample_falls_back_when_catalog_empty():
    with patch(
        "src.services.model_capabilities_cache.get_free_models",
        return_value=set(),
    ):
        sample = get_anonymous_allowed_models_sample(3)
        assert sample == ANONYMOUS_ALLOWED_MODELS[:3]
