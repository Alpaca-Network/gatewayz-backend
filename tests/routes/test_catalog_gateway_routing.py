"""Catalog-listed models route to the provider the catalog lists them under.

Production 2026-10-27: every DeepInfra model failed through Gatewayz. Bare ids
('Qwen/Qwen3-235B-...') fell through prefix detection to OpenRouter, and
'openai/gpt-oss-120b' (a DeepInfra listing) was sent to OpenAI.
"""

import pytest

from src.routes import chat_request

CATALOG = [
    {"id": "Qwen/Qwen3-235B-A22B-Instruct-2507", "source_gateway": "deepinfra"},
    {"id": "openai/gpt-oss-120b", "source_gateway": "deepinfra"},
    {"id": "openai/gpt-4o-mini", "source_gateway": "openai"},
    {"id": "shared/model", "source_gateway": "deepinfra"},
    {"id": "shared/model", "source_gateway": "groq"},
    {"id": "off/model", "source_gateway": "zai"},
]


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    import src.services.models as models
    import src.utils.provider_filter as pf

    monkeypatch.setattr(models, "get_cached_models", lambda gateway="all", **k: CATALOG)
    monkeypatch.setattr(pf, "is_provider_enabled", lambda slug: slug != "zai")


def test_bare_mixed_case_id_routes_to_its_catalog_provider():
    assert chat_request.catalog_gateway_for("Qwen/Qwen3-235B-A22B-Instruct-2507") == "deepinfra"
    assert chat_request.catalog_gateway_for("qwen/qwen3-235b-a22b-instruct-2507") == "deepinfra"


def test_catalog_beats_the_org_prefix():
    assert chat_request.catalog_gateway_for("openai/gpt-oss-120b") == "deepinfra"


def test_first_party_models_keep_their_provider():
    assert chat_request.catalog_gateway_for("openai/gpt-4o-mini") == "openai"


def test_listed_under_several_providers_defers_to_detection():
    assert chat_request.catalog_gateway_for("shared/model") is None


def test_disabled_or_unknown_is_none():
    assert chat_request.catalog_gateway_for("off/model") is None
    assert chat_request.catalog_gateway_for("nope/nope") is None
    assert chat_request.catalog_gateway_for(None) is None
