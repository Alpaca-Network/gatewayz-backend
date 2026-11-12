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
        "src.services.model_capabilities_cache.get_servable_free_models",
        return_value={"unsloth/gemma-2-9b-it", "unsloth/llama-3.2-3b-instruct"},
    ):
        sample = get_anonymous_allowed_models_sample(5)
        assert set(sample) == {"unsloth/gemma-2-9b-it", "unsloth/llama-3.2-3b-instruct"}


def test_allowed_models_sample_never_advertises_the_hardcoded_fallback():
    # The fallback ids are OpenRouter ``:free`` models; advertising them sent
    # keyless callers into an upstream credential error (2026-11-12).
    with patch(
        "src.services.model_capabilities_cache.get_servable_free_models",
        return_value=set(),
    ):
        assert get_anonymous_allowed_models_sample(3) == []


def test_restricted_message_without_free_models_points_to_signup():
    from src.services.anonymous_rate_limiter import SIGNUP_URL, validate_anonymous_request

    with patch(
        "src.services.model_capabilities_cache.get_servable_free_models",
        return_value=set(),
    ), patch(
        "src.services.anonymous_rate_limiter.is_model_allowed_for_anonymous",
        return_value=False,
    ):
        result = validate_anonymous_request("1.2.3.4", "openai/gpt-5")
    assert result["allowed"] is False
    assert SIGNUP_URL in result["reason"]
    assert ":free" not in result["reason"]


def test_loader_drops_free_rows_of_a_disabled_provider():
    import src.services.cache.model_capabilities_cache as cache

    rows = [
        {"provider_model_id": "unsloth/gemma-2-9b-it", "is_free": True, "providers": {"slug": "chutes"}},
        {"provider_model_id": "groq/openai/gpt-oss-20b", "is_free": True, "providers": {"slug": "groq"}},
        {"provider_model_id": "openai/gpt-5", "is_free": False, "providers": {"slug": "openai"}},
    ]
    with patch("src.db.model_capabilities_db.get_all_model_capability_flags", return_value=rows), patch(
        "src.db.model_capabilities_db.get_all_quality_scores", return_value=[]
    ), patch("src.utils.provider_filter.is_provider_enabled", side_effect=lambda s: s != "chutes"):
        cache.load_model_capabilities_cache(force=True)
        assert cache.get_servable_free_models() == {"groq/openai/gpt-oss-20b"}
        assert cache.is_free_model("unsloth/gemma-2-9b-it") is False
    cache.invalidate_model_capabilities_cache()
