import pytest

from src.services.multi_provider_registry import MultiProviderRegistry
from src.services.provider_selector import ProviderSelector


def _vertex_model():
    return {
        "id": "gemini-2.0-flash",
        "slug": "google/gemini-2.0-flash",
        "canonical_slug": "google/gemini-2.0-flash",
        "name": "Gemini 2.0 Flash",
        "description": "Fast multimodal model",
        "context_length": 1000000,
        "architecture": {"modality": "text"},
        "pricing": {"prompt": "0.1", "completion": "0.4"},
        "provider_slug": "google-vertex",
        "source_gateway": "google-vertex",
    }


def _openrouter_model():
    return {
        "id": "google/gemini-2.0-flash",
        "slug": "google/gemini-2.0-flash",
        "canonical_slug": "google/gemini-2.0-flash",
        "name": "Gemini 2.0 Flash",
        "description": "Proxy model",
        "context_length": 8192,
        "architecture": {"modality": "text"},
        "pricing": {"prompt": "0.12", "completion": "0.48"},
        "provider_slug": "openrouter",
        "source_gateway": "openrouter",
    }


def test_registry_merges_providers_by_canonical_slug():
    registry = MultiProviderRegistry()
    registry.sync_provider_catalog("google-vertex", [_vertex_model()])
    registry.sync_provider_catalog("openrouter", [_openrouter_model()])

    model = registry.get_model("google/gemini-2.0-flash")
    assert model is not None
    provider_names = [p.name for p in model.providers]
    assert provider_names == ["google-vertex", "openrouter"]
    assert model.aliases  # contains canonical + provider ids


def test_provider_selector_plan_orders_by_priority(monkeypatch):
    registry = MultiProviderRegistry()
    registry.sync_provider_catalog("google-vertex", [_vertex_model()])
    registry.sync_provider_catalog("openrouter", [_openrouter_model()])

    # Ensure ProviderSelector uses our temporary registry instance
    monkeypatch.setattr(
        "src.services.provider_selector.get_registry",
        lambda: registry,
    )

    selector = ProviderSelector()
    plan = selector.build_attempt_plan("google/gemini-2.0-flash")

    assert [attempt.provider for attempt in plan][:2] == ["google-vertex", "openrouter"]
    assert plan[0].provider_model_id == "gemini-2.0-flash"
