"""The unique-models cache path and catalog lookups for disabled providers.

Production (2026-10-19): ``get_cached_unique_models`` fed unique-shape rows
(``providers`` is a LIST) to the flat ``transform_db_models_batch``, which
raised ``'list' object has no attribute 'get'`` on every row (~264 errors in
17 h) and cached placeholders keyed by display name ("GPT-4") — junk ids in the
model-resolution index. Separately, lookups for providers outside
ENABLED_PROVIDERS hit the database every cache cycle and logged "payload is
empty".
"""

from unittest.mock import MagicMock

import pytest

from src.db.models_catalog_db import (
    transform_db_model_to_api_format,
    transform_unique_model_to_api_format,
)

UNIQUE_ROW = {
    "unique_model_id": 123,
    "model_name": "GPT-4o mini",
    "model_count": 1,
    "sample_model_id": "openai/gpt-4o-mini",
    "providers": [
        {
            "provider_id": 1,
            "provider_slug": "openai",
            "provider_name": "OpenAI",
            "model_id": 456,
            "provider_model_id": "openai/gpt-4o-mini",
            "pricing_prompt": "0.15",
            "pricing_completion": "0.6",
            "context_length": 128000,
            "health_status": "healthy",
            "average_response_time_ms": 900,
            "modality": "text->text",
        }
    ],
}


def test_unique_transform_keeps_the_canonical_id():
    api = transform_unique_model_to_api_format(UNIQUE_ROW)
    # "id" keeps its public, prefix-stripped shape; canonical_id is additive.
    assert api["id"] == "gpt-4o-mini"
    assert api["canonical_id"] == "openai/gpt-4o-mini"
    assert api["providers"][0]["slug"] == "openai"
    assert api["provider_count"] == 1


def test_flat_transform_tolerates_list_shaped_providers(caplog):
    api = transform_db_model_to_api_format(
        {**UNIQUE_ROW, "provider_model_id": "openai/gpt-4o-mini"}
    )
    assert "Error transforming DB model" not in caplog.text
    assert api["id"] == "openai/gpt-4o-mini"
    assert api["source_gateway"] == "openai"


def test_get_cached_unique_models_uses_the_unique_transform(monkeypatch):
    from src.services.cache import model_catalog_cache as mcc

    cache = MagicMock()
    cache.get_unique_models.return_value = None
    monkeypatch.setattr(mcc, "get_model_catalog_cache", lambda: cache)
    monkeypatch.setattr(
        "src.services.cache.local_memory_cache.get_local_catalog", lambda _k: (None, False)
    )
    monkeypatch.setattr("src.services.cache.local_memory_cache.set_local_catalog", lambda *_a: None)
    monkeypatch.setattr(
        "src.db.models_catalog_db.get_all_unique_models_for_catalog",
        lambda include_inactive=False: [UNIQUE_ROW],
    )
    # Pricing enrichment is not under test; keep it from touching a database.
    monkeypatch.setattr(
        "src.db.models_catalog_db.transform_unique_models_batch",
        lambda rows: [transform_unique_model_to_api_format(r) for r in rows],
    )
    flat = MagicMock(side_effect=AssertionError("flat transform must not be used"))
    monkeypatch.setattr("src.db.models_catalog_db.transform_db_models_batch", flat)

    models = mcc.get_cached_unique_models()

    assert [m["canonical_id"] for m in models] == ["openai/gpt-4o-mini"]
    assert isinstance(models[0]["providers"], list)
    cache.set_unique_models.assert_called_once()


def test_resolution_index_prefers_canonical_id(monkeypatch):
    from src.services import model_resolution

    monkeypatch.setattr("src.services.models.get_cached_models", lambda _g: [], raising=False)
    monkeypatch.setattr(
        "src.services.cache.model_catalog_cache.get_cached_unique_models",
        lambda: [{"id": "gpt-4o-mini", "canonical_id": "openai/gpt-4o-mini"}, {"id": "bare"}],
        raising=False,
    )

    ids = model_resolution._load_catalog_ids()

    assert ids == ["openai/gpt-4o-mini", "bare"]


class TestDisabledProviderLookups:
    @pytest.fixture
    def provider_catalog(self, monkeypatch):
        fetch = MagicMock(return_value=[{"id": "x/y"}])
        monkeypatch.setattr("src.services.model_catalog_cache.get_cached_provider_catalog", fetch)
        monkeypatch.setattr(
            "src.services.models.filter_valid_enhanced_models", lambda models: models
        )
        return fetch

    def test_disabled_provider_returns_empty_without_a_lookup(self, monkeypatch, provider_catalog):
        from src.services.models import get_cached_models

        monkeypatch.setattr(
            "src.config.config.Config.ENABLED_PROVIDERS", frozenset({"openai"}), raising=False
        )

        assert get_cached_models("fireworks") == []
        provider_catalog.assert_not_called()

    def test_enabled_provider_still_looked_up(self, monkeypatch, provider_catalog):
        from src.services.models import get_cached_models

        monkeypatch.setattr(
            "src.config.config.Config.ENABLED_PROVIDERS", frozenset({"openai"}), raising=False
        )

        assert get_cached_models("openai") == [{"id": "x/y"}]
        provider_catalog.assert_called_once_with("openai")

    def test_no_restriction_when_unset(self, monkeypatch, provider_catalog):
        from src.services.models import get_cached_models

        monkeypatch.setattr("src.config.config.Config.ENABLED_PROVIDERS", None, raising=False)

        assert get_cached_models("fireworks") == [{"id": "x/y"}]
