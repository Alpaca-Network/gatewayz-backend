"""Regression test for the is_free_model() ":free" suffix bypass.

is_free_model() used to return True for ANY model_id ending in ":free",
regardless of whether it was an actual cataloged free model. That's a real
security boundary: it's what exempts a request from the credit check in
chat.py (src/routes/chat.py) and gates the anonymous model allowlist in
anonymous_rate_limiter.py (is_model_allowed_for_anonymous). A caller could
name any real paid model "<paid-model>:free" and skip both checks.

The fix requires membership in the DB-backed free-model set (or its
hardcoded fallback) — no bare suffix match.
"""

import time

from src.services.cache import model_capabilities_cache as cache


def _reset_cache(free_models: set[str]) -> None:
    cache._free_models = set(free_models)
    cache._cache_loaded = True
    cache._cache_loaded_at = time.monotonic()  # fresh — _ensure_loaded() won't reload


def test_known_free_model_is_free():
    _reset_cache({"google/gemini-2.0-flash-exp:free"})
    assert cache.is_free_model("google/gemini-2.0-flash-exp:free") is True
    assert cache.is_free_model("Google/Gemini-2.0-Flash-Exp:free") is True  # case-insensitive


def test_unknown_model_with_free_suffix_is_not_free():
    _reset_cache({"google/gemini-2.0-flash-exp:free"})
    # Fabricated ":free" suffix on a real, unrelated paid model — must NOT pass.
    assert cache.is_free_model("openai/gpt-4o:free") is False
    assert cache.is_free_model("anthropic/claude-opus-4.5:free") is False


def test_paid_model_without_suffix_is_not_free():
    _reset_cache({"google/gemini-2.0-flash-exp:free"})
    assert cache.is_free_model("openai/gpt-4o") is False


def test_falls_back_to_hardcoded_set_when_cache_empty():
    _reset_cache(set())
    fallback_model = next(iter(cache._FREE_MODELS_FALLBACK))
    assert cache.is_free_model(fallback_model) is True
    assert cache.is_free_model("openai/gpt-4o:free") is False
