# tests/services/test_pricing.py
import importlib
import math

import pytest

MODULE_PATH = "src.services.pricing"  # change if your module lives elsewhere


@pytest.fixture
def mod():
    return importlib.import_module(MODULE_PATH)


def _models_fixture():
    return [
        {
            "id": "openai/gpt-4o",
            "slug": "openai/gpt-4o",
            "pricing": {"prompt": "0.000005", "completion": "0.000015"},
        },
        {
            "id": "anthropic/claude-3-opus",
            "slug": "claude-3-opus",  # demonstrate id vs slug
            "pricing": {"prompt": "0.00003", "completion": "0.00006"},
        },
        {
            # bad/missing fields to ensure safe defaults to 0
            "id": "bad/model",
            "slug": "bad/model",
            "pricing": {"prompt": None, "completion": ""},
        },
    ]


# -------------------- get_model_pricing --------------------


def test_get_model_pricing_found_by_id(monkeypatch, mod):
    # Ensure get_cached_models("all") is called
    called = {"args": None}

    def fake_get_cached_models(arg):
        called["args"] = arg
        return _models_fixture()

    # Patch at the source module where it's imported from
    monkeypatch.setattr("src.services.models.get_cached_models", fake_get_cached_models)
    # Mock _is_building_catalog to return False
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    out = mod.get_model_pricing("openai/gpt-4o")
    assert called["args"] == "all"
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.000005)
    assert math.isclose(out["completion"], 0.000015)


def test_get_model_pricing_found_by_slug(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("claude-3-opus")  # matches by slug
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.00003)
    assert math.isclose(out["completion"], 0.00006)


def test_get_model_pricing_model_not_found_uses_default(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("totally/unknown-model")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_empty_cache_uses_default(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("anything")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_handles_missing_prices(monkeypatch, mod):
    # The "bad/model" entry has None/""; code should coerce to 0.0, still found=True
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("bad/model")
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.0)
    assert math.isclose(out["completion"], 0.0)


def test_get_model_pricing_exception_returns_default(monkeypatch, mod):
    def boom(_):
        raise RuntimeError("cache layer down")

    monkeypatch.setattr("src.services.models.get_cached_models", boom)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("openai/gpt-4o")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_normalizes_hf_suffix(monkeypatch, mod):
    """Test that HuggingFace :hf-inference suffix is stripped for pricing lookup"""
    # Create a model with ID without suffix
    hf_models = [
        {
            "id": "meta-llama/Llama-2-7b-chat-hf",
            "slug": "meta-llama/Llama-2-7b-chat-hf",
            "pricing": {"prompt": "0", "completion": "0"},  # Free model
        }
    ]
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: hf_models)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    # Request with :hf-inference suffix should still find the model
    out = mod.get_model_pricing("meta-llama/Llama-2-7b-chat-hf:hf-inference")
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.0)
    assert math.isclose(out["completion"], 0.0)


def test_get_model_pricing_handles_multiple_provider_suffixes(monkeypatch, mod):
    """Test that various provider suffixes are normalized"""
    models = [
        {
            "id": "test/model-1",
            "slug": "test/model-1",
            "pricing": {"prompt": "0.00001", "completion": "0.00002"},
        }
    ]
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: models)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    # Test with :hf-inference suffix
    out_hf = mod.get_model_pricing("test/model-1:hf-inference")
    assert out_hf["found"] is True
    assert math.isclose(out_hf["prompt"], 0.00001)

    # Test with :openai suffix
    out_openai = mod.get_model_pricing("test/model-1:openai")
    assert out_openai["found"] is True
    assert math.isclose(out_openai["prompt"], 0.00001)

    # Test with :anthropic suffix
    out_anthropic = mod.get_model_pricing("test/model-1:anthropic")
    assert out_anthropic["found"] is True
    assert math.isclose(out_anthropic["prompt"], 0.00001)


# -------------------- calculate_cost --------------------


def test_calculate_cost_happy(monkeypatch, mod):
    # Force a specific pricing (pricing is per token)
    # $0.00001 per prompt token, $0.00002 per completion token
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda model_id: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost = mod.calculate_cost("any/model", prompt_tokens=1000, completion_tokens=500)
    # 1000 * 0.00001 + 500 * 0.00002 = 0.01 + 0.01 = 0.02
    assert math.isclose(cost, 0.02)


def test_calculate_cost_zero_tokens(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00003, "completion": 0.00006, "found": True},
    )
    assert mod.calculate_cost("m", 0, 0) == 0.0


def test_calculate_cost_uses_fallback_on_exception(monkeypatch, mod):
    # If pricing lookup explodes, fallback is (prompt+completion)*0.00002
    def boom(_):
        raise RuntimeError("err")

    monkeypatch.setattr(mod, "get_model_pricing", boom)

    cost = mod.calculate_cost("x", prompt_tokens=10, completion_tokens=5)
    # total_tokens = 15; 15 * 0.00002 = 0.0003
    assert math.isclose(cost, 0.0003)


# -------------------- Free Model Pricing Tests --------------------


def test_calculate_cost_free_model_returns_zero(monkeypatch, mod):
    """Test that models ending with :free return $0 cost"""
    # Even if pricing would return non-zero, :free suffix should return $0
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )

    cost = mod.calculate_cost(
        "meta-llama/llama-2-7b:free", prompt_tokens=1000, completion_tokens=500
    )
    assert cost == 0.0


def test_calculate_cost_free_model_openrouter_format(monkeypatch, mod):
    """Test that OpenRouter free models (ending with :free) return $0 cost"""
    # OpenRouter uses :free suffix for free models
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00005, "completion": 0.00010, "found": True},
    )

    cost = mod.calculate_cost(
        "mistralai/mistral-7b-instruct:free", prompt_tokens=2000, completion_tokens=1000
    )
    assert cost == 0.0


def test_calculate_cost_free_model_with_zero_tokens(monkeypatch, mod):
    """Test that free models with zero tokens return $0"""
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )

    cost = mod.calculate_cost("model:free", prompt_tokens=0, completion_tokens=0)
    assert cost == 0.0


def test_calculate_cost_non_free_model_normal_pricing(monkeypatch, mod):
    """Test that non-free models are charged normally"""
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )

    # Model without :free suffix should be charged
    cost = mod.calculate_cost("openai/gpt-4", prompt_tokens=1000, completion_tokens=500)
    # 1000 * 0.00001 + 500 * 0.00002 = 0.01 + 0.01 = 0.02
    assert math.isclose(cost, 0.02)


def test_calculate_cost_free_model_fallback_on_exception(monkeypatch, mod):
    """Test that free models return $0 even in fallback/exception case"""

    # If pricing lookup explodes, free models should still return $0
    def boom(_):
        raise RuntimeError("err")

    monkeypatch.setattr(mod, "get_model_pricing", boom)

    cost = mod.calculate_cost("model:free", prompt_tokens=100, completion_tokens=50)
    assert cost == 0.0


def test_calculate_cost_free_suffix_case_sensitive(monkeypatch, mod):
    """Test that :free suffix detection is case sensitive (lowercase only)"""
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )

    # :FREE (uppercase) should NOT be treated as free
    cost_upper = mod.calculate_cost("model:FREE", prompt_tokens=1000, completion_tokens=500)
    assert cost_upper > 0  # Should be charged

    # :Free (mixed case) should NOT be treated as free
    cost_mixed = mod.calculate_cost("model:Free", prompt_tokens=1000, completion_tokens=500)
    assert cost_mixed > 0  # Should be charged

    # :free (lowercase) should be free
    cost_lower = mod.calculate_cost("model:free", prompt_tokens=1000, completion_tokens=500)
    assert cost_lower == 0.0


def test_calculate_cost_multiple_free_models(monkeypatch, mod):
    """Test various free model formats"""
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )

    free_models = [
        "google/gemma-7b-it:free",
        "nousresearch/nous-hermes-llama2-13b:free",
        "huggingfaceh4/zephyr-7b-beta:free",
        "openchat/openchat-7b:free",
    ]

    for model in free_models:
        cost = mod.calculate_cost(model, prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0, f"Model {model} should return $0 cost"
