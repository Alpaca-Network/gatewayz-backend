"""PROVIDER_MODEL_ALLOWLIST must hold on the combined (gateway=all) catalog.

Production 2026-10-27: deepinfra was restricted to 15 models and
?gateway=deepinfra showed 15, but bare /v1/models listed 280 deepinfra rows.
"""

import json

import pytest

import src.config.config as config_module
from src.utils.provider_filter import filter_catalog_by_allowlist


@pytest.fixture
def restrict_deepinfra(monkeypatch):
    # Resolve Config at fixture time: other tests reload src.config.config, and
    # get_model_allowlist reads the current module's Config, not a stale import.
    monkeypatch.setattr(
        config_module.Config,
        "PROVIDER_MODEL_ALLOWLIST",
        json.dumps({"deepinfra": ["deepseek-ai/DeepSeek-V3.2"]}),
    )


def test_restricted_provider_rows_are_trimmed(restrict_deepinfra):
    rows = [
        {
            "id": "deepseek-ai/DeepSeek-V3.2",
            "source_gateway": "deepinfra",
            "provider_slug": "deepseek-ai",
        },
        {
            "id": "canopylabs/orpheus-3b-0.1-ft",
            "source_gateway": "deepinfra",
            "provider_slug": "canopylabs",
        },
        {"id": "gpt-4o", "source_gateway": "openai", "provider_slug": "openai"},
    ]
    kept = [r["id"] for r in filter_catalog_by_allowlist(rows)]
    assert kept == ["deepseek-ai/DeepSeek-V3.2", "gpt-4o"]


def test_raw_db_rows_use_nested_provider_slug(restrict_deepinfra):
    rows = [
        {"provider_model_id": "deepseek-ai/DeepSeek-V3.2", "providers": {"slug": "deepinfra"}},
        {"provider_model_id": "Qwen/Other", "providers": {"slug": "deepinfra"}},
    ]
    assert len(filter_catalog_by_allowlist(rows)) == 1


def test_no_allowlist_returns_same_object(monkeypatch):
    monkeypatch.setattr(config_module.Config, "PROVIDER_MODEL_ALLOWLIST", "")
    rows = [{"id": "x", "source_gateway": "deepinfra"}]
    assert filter_catalog_by_allowlist(rows) is rows


def test_get_cached_models_all_applies_allowlist(restrict_deepinfra, monkeypatch):
    import src.services.model_catalog_cache as cache_mod
    from src.services import models as models_mod

    rows = [
        {"id": "deepseek-ai/DeepSeek-V3.2", "source_gateway": "deepinfra"},
        {"id": "canopylabs/orpheus-3b-0.1-ft", "source_gateway": "deepinfra"},
    ]
    monkeypatch.setattr(cache_mod, "get_cached_full_catalog", lambda: list(rows))
    monkeypatch.setattr(models_mod, "filter_valid_enhanced_models", lambda m: m)
    assert [m["id"] for m in models_mod.get_cached_models("all")] == ["deepseek-ai/DeepSeek-V3.2"]
