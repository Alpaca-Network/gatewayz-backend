"""Community-GPU models are published in the public catalog, labelled.

The gap this closes: the community GPU network (docs/gpu/) serves open-weight
models on operator-run nodes, and `GET /v1/models` had no way to say so --
community rows were merged into `gateway=all` unlabelled, indistinguishable
from a contracted provider's model except by squinting at the id prefix.

Four properties are load-bearing and each has a test here:

1. a community model is listed and carries `serving_tier == "community"`;
2. `?tier=provider` returns EXACTLY the provider-served set, unchanged by
   anything the community network does -- the value an integration pins;
3. a community model is UNPRICED, and unpriced never renders as `0`
   (a model that bills zero is a defect class this repo has shipped before);
4. resolution is still never substitution -- a bare open-weight name does not
   resolve onto a community id, and an unknown id is still a 400.

Endpoint tests use the same minimal-router + mocked-cache pattern as
tests/routes/test_catalog_community.py.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.routes.catalog as catalog
import src.services.pricing  # noqa: F401 -- put the PACKAGE in sys.modules
from src.config import Config
from src.security import inference_gates
from src.services.gpu.catalog import community_catalog_models
from src.services.model_resolution import ModelResolution

# --- fixtures / data ---------------------------------------------------------

PROVIDER_MODELS = [
    {
        "id": "openai/gpt-4-turbo",
        "name": "GPT-4 Turbo",
        "provider_slug": "openai",
        "source_gateway": "openai",
        "pricing": {"prompt": 0.01, "completion": 0.03},
    },
    {
        "id": "anthropic/claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "provider_slug": "anthropic",
        "source_gateway": "anthropic",
        "pricing": {"prompt": 0.003, "completion": 0.015},
    },
]

ACTIVE_NODE = {
    "id": "node-a",
    "status": "active",
    "models": [{"id": "llama-3.1-8b-instruct", "max_context": 8192, "dtype": "bf16"}],
}
SECOND_ACTIVE_NODE = {
    "id": "node-b",
    "status": "active",
    "models": [{"id": "llama-3.1-8b-instruct", "max_context": 8192, "dtype": "bf16"}],
}
OFFLINE_NODE = {
    "id": "node-c",
    "status": "offline",
    "models": [{"id": "qwen2.5-7b-instruct", "max_context": 32768}],
}

COMMUNITY_ROW = community_catalog_models([ACTIVE_NODE])[0]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(catalog.router, prefix="/v1")
    return TestClient(app)


def _get(params: dict, *, community_enabled: bool = True, community_rows=None):
    """GET /v1/models with the catalog caches stubbed out."""
    rows = [dict(COMMUNITY_ROW)] if community_rows is None else community_rows
    with (
        patch.object(Config, "COMMUNITY_ROUTING_ENABLED", community_enabled),
        patch("src.routes.catalog.get_cached_models", return_value=list(PROVIDER_MODELS)),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch("src.services.gpu.catalog.sync_community_catalog", return_value=rows),
    ):
        return _client().get("/v1/models", params=params)


# --- 1. community models are published, and labelled -------------------------


def test_community_model_is_listed_with_its_tier_label():
    resp = _get({"gateway": "all"})
    assert resp.status_code == 200, resp.text
    rows = {m["id"]: m for m in resp.json()["data"]}

    assert "community/llama-3.1-8b-instruct" in rows
    assert rows["community/llama-3.1-8b-instruct"]["serving_tier"] == "community"


def test_every_provider_row_is_labelled_too():
    """The field is unconditional: a client switches on it, never on absence."""
    resp = _get({"gateway": "all"})
    rows = resp.json()["data"]

    assert rows, "expected a non-empty catalog"
    assert all("serving_tier" in m for m in rows)
    assert rows[0]["serving_tier"] == "provider"


def test_tier_community_returns_only_community_models():
    resp = _get({"tier": "community"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {m["id"] for m in body["data"]} == {"community/llama-3.1-8b-instruct"}
    assert all(m["serving_tier"] == "community" for m in body["data"])
    assert body["total"] == 1
    assert body["tier"] == "community"


def test_response_echoes_the_tier_it_applied():
    assert _get({"gateway": "all"}).json()["tier"] == "all"


def test_unknown_tier_is_a_400_not_a_silently_wider_catalog():
    resp = _get({"tier": "premium"})
    assert resp.status_code == 400
    assert "premium" in resp.json()["detail"]


# --- 2. the provider-only filter is exactly today's set ----------------------


def test_tier_provider_returns_exactly_the_provider_set():
    resp = _get({"tier": "provider"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {m["id"] for m in body["data"]} == {m["id"] for m in PROVIDER_MODELS}
    assert body["total"] == len(PROVIDER_MODELS)
    assert all(m["serving_tier"] == "provider" for m in body["data"])


def test_tier_provider_does_not_even_consult_the_community_projection():
    """A caller who asked not to see community models should not pay for -- or
    be exposed to the failure mode of -- the gpu_nodes lookup."""
    with (
        patch.object(Config, "COMMUNITY_ROUTING_ENABLED", True),
        patch("src.routes.catalog.get_cached_models", return_value=list(PROVIDER_MODELS)),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch(
            "src.services.gpu.catalog.sync_community_catalog", return_value=[dict(COMMUNITY_ROW)]
        ) as mock_sync,
    ):
        resp = _client().get("/v1/models", params={"tier": "provider"})

    assert resp.status_code == 200, resp.text
    mock_sync.assert_not_called()


def test_provider_set_is_identical_whether_or_not_community_is_enabled():
    enabled = _get({"tier": "provider"}, community_enabled=True).json()
    disabled = _get({"tier": "provider"}, community_enabled=False).json()

    assert enabled["data"] == disabled["data"]
    assert enabled["total"] == disabled["total"] == len(PROVIDER_MODELS)


def test_totals_and_paging_describe_the_filtered_set():
    """`total` must count post-filter, or a client pages into rows that the
    filter already removed."""
    body = _get({"tier": "provider", "limit": 1}).json()

    assert body["total"] == len(PROVIDER_MODELS)
    assert body["returned"] == 1
    assert body["has_more"] is True
    assert body["next_offset"] == 1


# --- 3. unpriced is unpriced, never zero -------------------------------------


def test_community_row_is_unpriced_and_never_priced_at_zero():
    row = _get({"tier": "community"}).json()["data"][0]

    assert row["pricing"] is None
    assert row["pricing_status"] == "unpriced"
    # The specific regression: `{"prompt": 0, "completion": 0}` would read as
    # "free to serve" to every downstream cost calculation.
    assert row["pricing"] != {"prompt": 0, "completion": 0}


def test_community_row_is_not_marked_free():
    """is_free is the flag that skips the credit check and admits a model for
    anonymous callers. Unpriced must not borrow it."""
    row = _get({"tier": "community"}).json()["data"][0]
    assert row["is_free"] is False


def test_community_row_is_reported_unservable_by_the_pricing_gate():
    """`servable` mirrors what enforce_model_pricing_gate would do. An unpriced
    row is refused there, so the catalog has to say so rather than advertise a
    model the gate rejects."""
    row = _get({"tier": "community"}).json()["data"][0]
    assert row["servable"] is False


def test_provider_rows_keep_their_real_pricing():
    rows = {m["id"]: m for m in _get({"tier": "provider"}).json()["data"]}
    assert rows["openai/gpt-4-turbo"]["pricing"] == {"prompt": 0.01, "completion": 0.03}
    assert rows["openai/gpt-4-turbo"].get("pricing_status") is None


# --- 4. availability honesty -------------------------------------------------


def test_offline_nodes_contribute_nothing_to_the_catalog():
    """A model whose nodes have all gone offline stops being advertised rather
    than lingering as a model nobody can serve."""
    projected = community_catalog_models([ACTIVE_NODE, OFFLINE_NODE])
    assert {m["id"] for m in projected} == {"community/llama-3.1-8b-instruct"}


def test_available_node_count_reflects_how_many_active_nodes_serve_it():
    one = community_catalog_models([ACTIVE_NODE])[0]
    two = community_catalog_models([ACTIVE_NODE, SECOND_ACTIVE_NODE])[0]

    assert one["available_node_count"] == 1
    assert two["available_node_count"] == 2


def test_available_node_count_is_served_to_clients():
    row = _get({"tier": "community"}).json()["data"][0]
    assert row["available_node_count"] == 1


def test_no_active_nodes_means_no_community_rows_at_all():
    body = _get({"gateway": "all"}, community_rows=[]).json()
    assert {m["id"] for m in body["data"]} == {m["id"] for m in PROVIDER_MODELS}


# --- 5. resolution is never substitution -------------------------------------

_PRICING = sys.modules["src.services.pricing"]


@pytest.fixture
def _require_pricing(monkeypatch):
    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", True, raising=False)


def _stub_gate(monkeypatch, resolution: ModelResolution, priced: set[str]):
    monkeypatch.setattr(inference_gates, "resolve_catalog_model_id", lambda _m: resolution)
    monkeypatch.setattr(inference_gates, "index_is_empty", lambda: False)
    monkeypatch.setattr(_PRICING, "model_has_pricing", lambda m: m in priced)
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
    )


@pytest.mark.usefixtures("_require_pricing")
async def test_unknown_model_id_is_still_a_400(monkeypatch):
    """Publishing the community tier must not soften the unknown-id contract."""
    _stub_gate(monkeypatch, ModelResolution(None, "unresolved"), set())

    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("totally-fake-model-xyz")

    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_found"


def test_community_ids_stay_out_of_the_resolution_index(monkeypatch):
    """The resolver indexes the CACHED catalog (src.services.models.get_cached_models
    and the unique-models cache). Community rows are a serve-time top-up in
    src/routes/catalog.py and never enter either cache -- which is what stops a
    bare `llama-3.1-8b-instruct` from suffix-matching onto
    `community/llama-3.1-8b-instruct` and silently routing a caller's prompt to
    a community node they never asked for.
    """
    import src.services.model_resolution as model_resolution

    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    monkeypatch.setattr("src.services.models.get_cached_models", lambda *_a, **_k: PROVIDER_MODELS)
    monkeypatch.setattr(
        "src.services.cache.model_catalog_cache.get_cached_unique_models", lambda: []
    )
    model_resolution.invalidate_resolution_index()
    try:
        bare = model_resolution.resolve_catalog_model_id("llama-3.1-8b-instruct")
        prefixed = model_resolution.resolve_catalog_model_id("community/llama-3.1-8b-instruct")

        assert bare.canonical_id is None
        assert bare.matched_by == "unresolved"
        assert prefixed.canonical_id is None
    finally:
        model_resolution.invalidate_resolution_index()
