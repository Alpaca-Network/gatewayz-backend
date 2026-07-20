"""Sync must not re-normalize pricing that enrichment already returned per-token.

North Star §4.2 unit-error landmine: enriched pricing (pricing_source in
manual/database/cross_reference) is already per-token; only inline raw provider
pricing (e.g. xai per-1M, no pricing_source) should be normalized here.
"""

from decimal import Decimal

from src.services.model_catalog_sync import transform_normalized_model_to_db_schema


def _pricing_raw(model):
    rec = transform_normalized_model_to_db_schema(model, provider_id=1, provider_slug=model["provider_slug"])
    assert rec is not None
    return rec["metadata"]["pricing_raw"]


def test_enriched_per_token_pricing_is_not_renormalized():
    # openai: enrichment produced per-token 2.5e-6; must survive unchanged.
    model = {
        "id": "openai/gpt-4o",
        "provider_model_id": "gpt-4o",
        "provider_slug": "openai",
        "source_gateway": "openai",
        "pricing_source": "database",
        "pricing": {"prompt": "2.5e-06", "completion": "1e-05", "request": "0", "image": "0"},
    }
    pr = _pricing_raw(model)
    assert Decimal(pr["prompt"]) == Decimal("2.5e-06"), pr
    assert Decimal(pr["completion"]) == Decimal("1e-05"), pr


def test_inline_per_1m_pricing_is_normalized_once():
    # xai: hardcoded raw per-1M "5"/"15", no pricing_source -> normalize to per-token.
    model = {
        "id": "grok-2",
        "provider_model_id": "grok-2",
        "provider_slug": "xai",
        "source_gateway": "xai",
        "pricing": {"prompt": "5", "completion": "15", "request": "0", "image": "0"},
    }
    pr = _pricing_raw(model)
    assert Decimal(pr["prompt"]) == Decimal("0.000005"), pr
    assert Decimal(pr["completion"]) == Decimal("0.000015"), pr
