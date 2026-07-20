"""Manual pricing loader must actually load the JSON seed so closed providers
(OpenAI/Anthropic) get pricing during model sync instead of None."""

import src.services.pricing.pricing_lookup as pl


def _reset_cache():
    pl._pricing_cache = None
    pl._pricing_cache_timestamp = None


def test_load_manual_pricing_is_not_empty():
    _reset_cache()
    data = pl.load_manual_pricing()
    assert isinstance(data, dict) and data, "manual pricing must load real entries, not {}"
    assert "openai" in data and "anthropic" in data


def test_openai_and_anthropic_chat_models_priced():
    _reset_cache()
    p = pl.get_model_pricing("openai", "openai/gpt-4o")
    assert p and float(p["prompt"]) > 0 and float(p["completion"]) > 0
    a = pl.get_model_pricing("anthropic", "anthropic/claude-sonnet-4")
    assert a and float(a["prompt"]) > 0 and float(a["completion"]) > 0


def test_pricing_is_per_token_scale():
    """gpt-4o is ~$2.5 / 1M input tokens => ~2.5e-6 per token (sanity vs 1e6 unit errors)."""
    _reset_cache()
    p = pl.get_model_pricing("openai", "openai/gpt-4o")
    assert 1e-7 < float(p["prompt"]) < 1e-4
