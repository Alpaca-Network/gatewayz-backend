"""Unit tests for the model→provider offers projection (Phase 1 pipeline)."""

from __future__ import annotations

import pytest

from src.services.model_offers_projection import (
    build_offer_rows,
    normalized_cost_per_1k,
    offer_summary,
)

PROVIDERS = {
    "98": {"slug": "onerouter"},
    "110": {"slug": "openrouter"},
    "88": {"slug": "chutes"},
}


def _model(**kw):
    base = {
        "id": "1",
        "provider_id": "98",
        "provider_model_id": "meta/llama-3.1-8b",
        "pricing_original_prompt": "0.0000005",  # per-token
        "success_rate": None,
        "average_response_time_ms": None,
        "is_active": True,
        "modality": "text",
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# normalized_cost_per_1k — self-calibrating units
# --------------------------------------------------------------------------- #

def test_per_token_price_normalized_to_per_1k():
    # 5e-7 per token → $0.50/1M → per-1k = 0.0005
    assert normalized_cost_per_1k("0.0000005") == pytest.approx(0.0005)


def test_per_million_price_normalized_to_per_1k():
    # 0.8 detected as per-1M → 0.8/1M per token → per-1k = 0.0008
    assert normalized_cost_per_1k("0.8") == pytest.approx(0.0008)


def test_none_and_zero_and_garbage_return_none():
    assert normalized_cost_per_1k(None) is None
    assert normalized_cost_per_1k("None") is None
    assert normalized_cost_per_1k("0") is None
    assert normalized_cost_per_1k("") is None
    assert normalized_cost_per_1k("abc") is None


# --------------------------------------------------------------------------- #
# build_offer_rows
# --------------------------------------------------------------------------- #

def test_basic_offer_built():
    rows = build_offer_rows([_model()], PROVIDERS)
    assert len(rows) == 1
    o = rows[0]
    assert o["canonical_id"] == "meta/llama-3.1-8b"
    assert o["provider_slug"] == "onerouter"
    assert o["upstream_cost"] == pytest.approx(0.0005)
    assert o["quality_prior"] == 0.5
    assert o["is_active"] is True


def test_skips_inactive_nonchat_and_unpriced():
    models = [
        _model(id="a", is_active=False),
        _model(id="b", modality="image"),
        _model(id="c", pricing_original_prompt=None),
        _model(id="d", provider_id="9999"),  # unknown provider → no slug
        _model(id="e", provider_model_id=None),
    ]
    assert build_offer_rows(models, PROVIDERS) == []


def test_multi_provider_grouping():
    models = [
        _model(id="1", provider_id="98", pricing_original_prompt="0.0000005"),
        _model(id="2", provider_id="110", pricing_original_prompt="0.0000007"),
        _model(id="3", provider_id="88", pricing_original_prompt="0.0000003"),
    ]
    rows = build_offer_rows(models, PROVIDERS)
    assert len(rows) == 3
    summary = offer_summary(rows)
    assert summary["distinct_models"] == 1
    assert summary["multi_provider_models"] == 1
    assert summary["max_providers_for_one_model"] == 3


def test_dedup_keeps_cheapest():
    # same (canonical_id, provider_slug) twice → keep the cheaper cost
    models = [
        _model(id="1", provider_id="98", pricing_original_prompt="0.0000009"),
        _model(id="2", provider_id="98", pricing_original_prompt="0.0000004"),
    ]
    rows = build_offer_rows(models, PROVIDERS)
    assert len(rows) == 1
    assert rows[0]["upstream_cost"] == pytest.approx(0.0004)


def test_quality_from_success_rate_percent_and_fraction():
    r_pct = build_offer_rows([_model(success_rate="95")], PROVIDERS)[0]
    assert r_pct["quality_prior"] == pytest.approx(0.95)
    r_frac = build_offer_rows([_model(success_rate=0.8)], PROVIDERS)[0]
    assert r_frac["quality_prior"] == pytest.approx(0.8)


def test_p50_from_response_time():
    r = build_offer_rows([_model(average_response_time_ms="320")], PROVIDERS)[0]
    assert r["p50_ms"] == 320
    assert r["p95_ms"] is None


def test_provider_id_int_key_resolves():
    # provider_id may come back as int; resolution tries str() too
    rows = build_offer_rows([_model(provider_id=98)], PROVIDERS)
    assert len(rows) == 1
    assert rows[0]["provider_slug"] == "onerouter"


def test_summary_empty():
    assert offer_summary([]) == {
        "total_offers": 0,
        "distinct_models": 0,
        "multi_provider_models": 0,
        "max_providers_for_one_model": 0,
    }
