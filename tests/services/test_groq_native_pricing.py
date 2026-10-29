"""Groq's /models returns per-token prices; the sync expects Groq prices per 1M.

The normalizer must hand the sync per-1M values so that the sync's per-1M ->
per-token conversion lands on Groq's real per-token price.
"""

from decimal import Decimal

from src.services.model_catalog_sync import transform_normalized_model_to_db_schema
from src.services.providers.groq_catalog import normalize_groq_model

QWEN = {
    "id": "qwen/qwen3.8-27b",
    "owned_by": "Alibaba Cloud",
    "pricing": {"prompt": "0.0000008", "completion": "0.000004", "image": "0", "request": "0"},
}


def test_groq_per_token_prices_are_converted_to_per_million():
    model = normalize_groq_model(dict(QWEN))
    assert Decimal(model["pricing"]["prompt"]) == Decimal("0.8")
    assert Decimal(model["pricing"]["completion"]) == Decimal("4")


def test_tts_models_without_completion_price_stay_unpriced(monkeypatch):
    import src.services.pricing.pricing_lookup as lookup

    # No fallback tier prices it either -> enrichment drops it (pre-existing behaviour).
    monkeypatch.setattr(lookup, "enrich_model_with_pricing", lambda m, g, **k: m)
    model = normalize_groq_model(
        {"id": "canopylabs/orpheus-v1-english", "pricing": {"prompt": "0.000022"}}
    )
    assert model["pricing"]["prompt"] is None
    assert model["pricing"]["completion"] is None


def test_audio_output_models_are_not_listed():
    model = normalize_groq_model(
        {
            "id": "canopylabs/orpheus-v1-english",
            "output_modalities": ["audio"],
            "pricing": {"prompt": "0.000022"},
        }
    )
    assert model is None


def test_sync_stores_groq_real_per_token_price(monkeypatch):
    import src.services.model_catalog_sync as sync

    monkeypatch.setattr(sync, "get_provider_format", lambda slug: "per_1m")
    row = transform_normalized_model_to_db_schema(normalize_groq_model(dict(QWEN)), 1, "groq")
    raw = row["metadata"]["pricing_raw"]
    assert Decimal(raw["prompt"]) == Decimal("0.0000008")
    assert Decimal(raw["completion"]) == Decimal("0.000004")
    assert row["is_active"] is True
