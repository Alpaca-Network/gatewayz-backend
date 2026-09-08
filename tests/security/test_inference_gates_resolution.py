"""enforce_model_pricing_gate resolves bare ids and grades errors correctly.

The status code matters as much as the rejection here: a caller's unknown
model must never come back as 503 "contact support", which reads as our
outage and is what production returned for every vendor-native Anthropic id.
"""

from __future__ import annotations

import sys

import pytest
from fastapi import HTTPException

from src.security import inference_gates
from src.services.model_resolution import ModelResolution
import src.services.pricing  # noqa: F401 -- put the PACKAGE in sys.modules

# HARNESS TRAP: `src/services/pricing/` is a package that CONTAINS a
# `pricing.py`, so the usual string form —
#     monkeypatch.setattr("src.services.pricing.model_has_pricing", fake)
# — resolves to the SUBMODULE and leaves the PACKAGE attribute untouched. The
# package attribute is the one `from src.services.pricing import
# model_has_pricing` actually reads, so that patch silently does nothing and
# the test then passes (or fails) against real pricing data. Patch the package
# object out of sys.modules instead.
_PRICING = sys.modules["src.services.pricing"]


@pytest.fixture(autouse=True)
def _require_pricing(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", True, raising=False)


def _stub(monkeypatch, resolution: ModelResolution, priced: set[str], index_empty: bool = False):
    monkeypatch.setattr(inference_gates, "resolve_catalog_model_id", lambda _m: resolution)
    monkeypatch.setattr(inference_gates, "index_is_empty", lambda: index_empty)
    monkeypatch.setattr(_PRICING, "model_has_pricing", lambda m: m in priced)
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
    )


async def test_bare_id_is_admitted_under_its_canonical_id(monkeypatch):
    _stub(
        monkeypatch,
        ModelResolution("anthropic/claude-sonnet-4-6", "suffix"),
        {"anthropic/claude-sonnet-4-6"},
    )
    admitted = await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6")
    assert admitted == "anthropic/claude-sonnet-4-6"


async def test_unresolvable_id_is_a_400_not_a_503(monkeypatch):
    _stub(monkeypatch, ModelResolution(None, "unresolved"), set())
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("totally-fake-model-xyz")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_found"
    assert "contact support" not in exc.value.detail["error"]["message"].lower()


async def test_ambiguous_id_is_a_400_that_names_every_candidate(monkeypatch):
    _stub(
        monkeypatch,
        ModelResolution(
            None,
            "ambiguous",
            ("community/llama-3.1-8b-instruct", "meta-llama/llama-3.1-8b-instruct"),
        ),
        set(),
    )
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("llama-3.1-8b-instruct")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_ambiguous"
    msg = exc.value.detail["error"]["message"]
    assert "meta-llama/llama-3.1-8b-instruct" in msg
    assert "community/llama-3.1-8b-instruct" in msg


async def test_resolved_but_unpriced_model_stays_a_503_operator_alarm(monkeypatch):
    def _raise(_m):
        raise ValueError("HIGH_VALUE_MODEL_PRICING_MISSING")

    monkeypatch.setattr(
        inference_gates,
        "resolve_catalog_model_id",
        lambda _m: ModelResolution("anthropic/claude-sonnet-4-6", "exact"),
    )
    monkeypatch.setattr(_PRICING, "model_has_pricing", _raise)
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
    )
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("anthropic/claude-sonnet-4-6")
    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "pricing_not_configured"


async def test_catalogued_but_deliberately_unpriced_model_is_a_400(monkeypatch):
    _stub(monkeypatch, ModelResolution("some-vendor/tiny-7b", "exact"), set())
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("some-vendor/tiny-7b")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_priced"
    # The canonical id, not the raw input, is what the caller is told about.
    assert "some-vendor/tiny-7b" in exc.value.detail["error"]["message"]


async def test_free_model_is_admitted_under_its_canonical_id(monkeypatch):
    monkeypatch.setattr(
        inference_gates,
        "resolve_catalog_model_id",
        lambda _m: ModelResolution("meta-llama/llama-3.1-8b-instruct:free", "suffix"),
    )
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: True
    )
    admitted = await inference_gates.enforce_model_pricing_gate("llama-3.1-8b-instruct:free")
    assert admitted == "meta-llama/llama-3.1-8b-instruct:free"


async def test_unresolved_fully_qualified_id_falls_through_to_pricing(monkeypatch):
    # Resolution must never invent a rejection. A qualified id the index
    # doesn't know keeps its pre-resolution treatment: priced -> admitted.
    _stub(monkeypatch, ModelResolution(None, "unresolved"), {"provider/paid-model"})
    assert (
        await inference_gates.enforce_model_pricing_gate("provider/paid-model")
        == "provider/paid-model"
    )


async def test_cold_catalog_does_not_reject_a_bare_id(monkeypatch):
    # A cache outage must not answer "that model does not exist" to the whole
    # API — with an empty index every id falls through to the pricing checks.
    _stub(
        monkeypatch,
        ModelResolution(None, "unresolved"),
        {"claude-sonnet-4-6"},
        index_empty=True,
    )
    assert (
        await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6")
        == "claude-sonnet-4-6"
    )


async def test_gate_disabled_returns_the_input_unchanged(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", False, raising=False)
    assert (
        await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6")
        == "claude-sonnet-4-6"
    )
