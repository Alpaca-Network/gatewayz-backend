"""xAI reasoning params must reach the client the way that client accepts them.

Found in production on 2026-09-15 by the standing benchmark harness: four of
six xAI models returned HTTP 502 with

    Provider 'xai' returned an error for model 'grok-4':
    Completions.create() got an unexpected keyword argument 'reasoning'

That is our bug wearing the provider's clothes -- the same family as every
other defect this month. `xai_sdk` is declared in pyproject.toml but is NOT
installed, so get_xai_client() always falls back to the OpenAI-compatible
client, whose create() validates keyword arguments and raises TypeError on
anything it does not know. Vendor parameters belong in `extra_body`.

The tell was which models worked: grok-3 served fine because it is not a
reasoning model, so no `reasoning` kwarg was ever added. Every
reasoning-capable model failed.
"""

from __future__ import annotations

from src.services.providers.xai_client import _merge_reasoning, _uses_openai_fallback


class _OpenAIish:
    """Stands in for the OpenAI SDK client: identified by its module root."""


_OpenAIish.__module__ = "openai._client"


class _OfficialXai:
    pass


_OfficialXai.__module__ = "xai_sdk.client"


def test_the_fallback_client_is_recognised():
    assert _uses_openai_fallback(_OpenAIish()) is True
    assert _uses_openai_fallback(_OfficialXai()) is False


def test_reasoning_goes_in_extra_body_on_the_openai_fallback():
    # The actual production fix: a top-level `reasoning=` kwarg is what raised
    # TypeError and surfaced as a 502.
    out = _merge_reasoning(_OpenAIish(), {"max_tokens": 5}, {"reasoning": {"enabled": True}})
    assert "reasoning" not in out, "a top-level reasoning kwarg breaks the OpenAI SDK"
    assert out["extra_body"] == {"reasoning": {"enabled": True}}
    assert out["max_tokens"] == 5, "unrelated kwargs must survive untouched"


def test_reasoning_stays_top_level_on_the_official_sdk():
    out = _merge_reasoning(_OfficialXai(), {}, {"reasoning": {"enabled": True}})
    assert out["reasoning"] == {"enabled": True}
    assert "extra_body" not in out


def test_an_existing_extra_body_is_preserved():
    out = _merge_reasoning(
        _OpenAIish(), {"extra_body": {"foo": 1}}, {"reasoning": {"enabled": False}}
    )
    assert out["extra_body"] == {"foo": 1, "reasoning": {"enabled": False}}


def test_an_explicit_caller_reasoning_still_wins():
    # Documented precedence: if the caller set reasoning themselves, leave it.
    out = _merge_reasoning(_OpenAIish(), {"reasoning": "caller"}, {"reasoning": {"enabled": True}})
    assert out["reasoning"] == "caller"
    assert "extra_body" not in out


def test_a_non_reasoning_model_is_untouched():
    # grok-3's path: no reasoning params, nothing added. This is why it kept
    # working while every reasoning model 502'd.
    out = _merge_reasoning(_OpenAIish(), {"max_tokens": 5}, {})
    assert out == {"max_tokens": 5}
