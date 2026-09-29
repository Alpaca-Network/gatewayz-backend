"""`:free` is only free when the model is genuinely in the free set, and a `:free`
request that fails over to a paid provider is billed at the paid price."""

import pytest

from src.handlers.chat_handler import _loss_proof_cost_split
from src.routes.chat_helpers import is_free_model
from src.services.pricing import calculate_cost, calculate_cost_async, model_has_pricing


@pytest.fixture(autouse=True)
def _known_free_set(monkeypatch):
    """Deterministic free set, independent of DB/cache state left by other tests."""
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model",
        lambda m: m.lower() == "google/gemini-2.0-flash-exp:free",
    )


def test_genuine_free_model_is_free():
    assert calculate_cost("google/gemini-2.0-flash-exp:free", 1000, 1000) == 0.0
    assert is_free_model("google/gemini-2.0-flash-exp:free") is True


def test_arbitrary_vendor_free_suffix_is_charged():
    assert calculate_cost("openai/gpt-4o:free", 1000, 1000) > 0.0
    assert is_free_model("openai/gpt-4o:free") is False


def test_community_free_suffix_cannot_bypass_pricing_gate():
    assert is_free_model("community/x:free") is False
    assert calculate_cost("community/x:free", 1000, 1000) > 0.0 or not model_has_pricing(
        "community/x:free"
    )


async def test_async_spoofed_free_is_charged():
    assert await calculate_cost_async("openai/gpt-4o:free", 1000, 1000) > 0.0


def test_free_failed_over_to_paid_provider_bills_base_model(monkeypatch):
    seen = []

    def fake_split(model, *a, **k):
        seen.append(model)
        return (1.0, 0.5, 0.5)

    monkeypatch.setattr("src.handlers.chat_handler._cache_aware_split", fake_split)
    _loss_proof_cost_split("x/y:free", "x/y:free", 1, 1, 0, 0, "fireworks")
    _loss_proof_cost_split("x/y:free", "x/y:free", 1, 1, 0, 0, "openrouter")
    assert seen == ["x/y", "x/y:free"]
