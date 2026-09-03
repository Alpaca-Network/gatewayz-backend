"""Community GPU marketplace catalog top-up (gatewayz-backend#2262, M4 spec
§4 item 3): ``community/<model>`` must appear in the ``/v1/models`` listing
when an active node of an approved provider declares it, flag-gated and
gateway-scoped like every other provider.

Unit tests exercise ``_append_community_models`` directly (pure, mocked
``sync_community_catalog``) -- the same convention this file's directory
already uses for other catalog.py internals (see
tests/routes/test_catalog_servability.py's ``_row_is_servable``/
``_annotate_servability`` tests). The endpoint-level test proves the real
wiring: GET /v1/models actually includes a community row, using the same
minimal-router + mocked-cache pattern as
tests/routes/test_catalog_search_route_order.py.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routes.catalog as catalog
from src.config import Config
from src.routes.catalog import _append_community_models

COMMUNITY_MODEL = {
    "id": "community/llama-3.1-8b-instruct",
    "name": "llama-3.1-8b-instruct",
    "source_gateway": "community",
    "provider_slug": "community",
    "context_length": 8192,
}

MOCK_MODELS = [
    {
        "id": "openai/gpt-4-turbo",
        "name": "GPT-4 Turbo",
        "provider_slug": "openai",
        "source_gateway": "openai",
        "pricing": {"prompt": 0.01, "completion": 0.03},
    }
]


# --- _append_community_models (unit, pure) -----------------------------------


def test_noop_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
    with patch(
        "src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]
    ) as mock_sync:
        result = _append_community_models(list(MOCK_MODELS), "all")

    assert result == MOCK_MODELS
    mock_sync.assert_not_called()


def test_appends_community_model_for_gateway_all(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    with patch("src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]):
        result = _append_community_models(list(MOCK_MODELS), "all")

    ids = {m["id"] for m in result}
    assert "community/llama-3.1-8b-instruct" in ids
    assert "openai/gpt-4-turbo" in ids


def test_appends_community_model_for_gateway_community(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    with patch("src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]):
        result = _append_community_models([], "community")

    assert result == [COMMUNITY_MODEL]


def test_does_not_contribute_to_other_specific_gateways(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    with patch(
        "src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]
    ) as mock_sync:
        result = _append_community_models(list(MOCK_MODELS), "openai")

    assert result == MOCK_MODELS
    mock_sync.assert_not_called()


def test_no_duplicate_when_id_already_present(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    already_present = list(MOCK_MODELS) + [dict(COMMUNITY_MODEL)]
    with patch("src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]):
        result = _append_community_models(already_present, "all")

    assert len([m for m in result if m["id"] == COMMUNITY_MODEL["id"]]) == 1


def test_empty_community_catalog_is_a_noop(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    with patch("src.services.gpu.catalog.sync_community_catalog", return_value=[]):
        result = _append_community_models(list(MOCK_MODELS), "all")

    assert result == MOCK_MODELS


def test_fails_open_on_projection_error(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    with patch(
        "src.services.gpu.catalog.sync_community_catalog", side_effect=RuntimeError("db down")
    ):
        result = _append_community_models(list(MOCK_MODELS), "all")

    assert result == MOCK_MODELS


def test_does_not_mutate_input_list(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    original = list(MOCK_MODELS)
    with patch("src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]):
        result = _append_community_models(original, "all")

    assert original == MOCK_MODELS  # unchanged
    assert result is not original


# --- Endpoint-level: real GET /v1/models --------------------------------------


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(catalog.router, prefix="/v1")
    return TestClient(app)


def test_community_model_appears_in_v1_models_when_enabled(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    client = _make_client()

    with (
        patch("src.routes.catalog.get_cached_models", return_value=list(MOCK_MODELS)),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch(
            "src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]
        ) as mock_sync,
    ):
        resp = client.get("/v1/models", params={"gateway": "all"})

    assert resp.status_code == 200, resp.text
    ids = {m["id"] for m in resp.json()["data"]}
    assert "community/llama-3.1-8b-instruct" in ids
    mock_sync.assert_called()


def test_community_model_absent_from_v1_models_when_flag_off(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
    client = _make_client()

    with (
        patch("src.routes.catalog.get_cached_models", return_value=list(MOCK_MODELS)),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch(
            "src.services.gpu.catalog.sync_community_catalog", return_value=[COMMUNITY_MODEL]
        ) as mock_sync,
    ):
        resp = client.get("/v1/models", params={"gateway": "all"})

    assert resp.status_code == 200, resp.text
    ids = {m["id"] for m in resp.json()["data"]}
    assert "community/llama-3.1-8b-instruct" not in ids
    mock_sync.assert_not_called()
