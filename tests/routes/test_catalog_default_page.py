"""A bare GET /v1/models lists the whole catalog, whatever else is enabled.

Production regression (2026-10-20): enabling DeepInfra (11 allowlisted rows)
took the combined catalog from 96 to 107 rows. The route's default page size
was 100 and the combined list is assembled in provider-discovery order with
Anthropic last, so the last 7 rows -- all Anthropic -- moved to page 2. Every
Opus and Sonnet model vanished from the default listing, while ``?limit=500``
and ``?gateway=anthropic`` still showed all 14. OpenAI-compatible clients
(``client.models.list()``) read one page and never follow ``next_offset``.

Also covered: the startup warm-up writes under the same cache key a bare
request reads, and a cache hit that loses rows to health gating keeps
``total`` / ``returned`` consistent with ``data``.

Endpoint tests use the minimal-router + mocked-cache pattern of
tests/routes/test_catalog_serving_tier.py.
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routes.catalog as catalog
from src.config import Config
from src.services.cache import catalog_response_cache


def _rows(provider: str, count: int, prefix: str = "") -> list[dict]:
    return [
        {
            "id": f"{provider}/{prefix}model-{i:02d}",
            "name": f"{provider} {prefix}model {i}",
            "provider_slug": provider,
            "source_gateway": provider,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
        for i in range(count)
    ]


# Shape of the prod catalog before DeepInfra: 96 rows, Anthropic assembled last.
BASE = _rows("openai", 49) + _rows("groq", 11) + _rows("zai", 11) + _rows("xai", 6)
BASE += _rows("moonshot", 3) + _rows("meta", 2) + _rows("anthropic", 14, "claude-")
DEEPINFRA = _rows("deepinfra", 11)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(catalog.router, prefix="/v1")
    return TestClient(app)


def _get(catalog_rows: list[dict], params: dict | None = None):
    """GET /v1/models over *catalog_rows* with every cache layer stubbed out."""
    with (
        patch("src.routes.catalog.get_cached_models", return_value=list(catalog_rows)),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch.object(Config, "COMMUNITY_ROUTING_ENABLED", False),
    ):
        return _client().get("/v1/models", params=params or {})


def _per_provider(resp) -> Counter:
    return Counter(m["source_gateway"] for m in resp.json()["data"])


# --- the regression ------------------------------------------------------------


def test_bare_request_lists_every_row_of_a_catalog_over_100():
    resp = _get(BASE + DEEPINFRA)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["data"]) == 107
    assert body["total"] == 107
    assert body["has_more"] is False
    assert _per_provider(resp)["anthropic"] == 14


def test_enabling_a_provider_leaves_every_other_providers_rows_unchanged():
    before = _per_provider(_get(BASE))
    after = _per_provider(_get(BASE + DEEPINFRA))

    assert after.pop("deepinfra") == 11
    assert after == before


def test_default_and_explicit_limit_and_gateway_all_agree():
    rows = BASE + DEEPINFRA
    default_ids = [m["id"] for m in _get(rows).json()["data"]]
    limit_ids = [m["id"] for m in _get(rows, {"limit": 500}).json()["data"]]
    all_ids = [m["id"] for m in _get(rows, {"gateway": "all"}).json()["data"]]

    assert default_ids == limit_ids == all_ids
    assert len(default_ids) == 107


def test_an_explicit_small_limit_still_paginates():
    body = _get(BASE + DEEPINFRA, {"limit": 10}).json()

    assert len(body["data"]) == 10
    assert body["total"] == 107
    assert body["has_more"] is True
    assert body["next_offset"] == 10


# --- warm-up writes the entry a bare request reads ------------------------------


def test_startup_warmup_params_hit_the_bare_request_cache_key():
    seen: dict = {}

    async def _capture(gateway, params):
        seen["key"] = catalog_response_cache.get_catalog_cache_key(gateway, params)
        return None

    with (
        patch("src.routes.catalog.get_cached_models", return_value=list(BASE)),
        patch(
            "src.services.catalog_response_cache.get_cached_catalog_response",
            new=AsyncMock(side_effect=_capture),
        ),
        patch("src.services.cache.catalog_response_cache.get_redis_client", return_value=None),
        patch.object(Config, "COMMUNITY_ROUTING_ENABLED", False),
    ):
        assert _client().get("/v1/models").status_code == 200

    warm_key = catalog_response_cache.get_catalog_cache_key(
        "all", catalog.default_models_cache_params()
    )
    assert seen["key"] == warm_key


def test_warmup_params_page_holds_the_whole_default_catalog():
    params = catalog.default_models_cache_params()
    assert params["limit"] == catalog.DEFAULT_MODELS_LIST_LIMIT
    assert catalog.DEFAULT_MODELS_LIST_LIMIT >= 1000


# --- a cache hit that loses rows to health gating keeps its envelope honest ------


def test_cache_hit_gating_updates_total_and_returned():
    cached_rows = _rows("openai", 5)
    cached_rows[1]["health_status"] = "down"
    cached = {
        "object": "list",
        "data": cached_rows,
        "total": 5,
        "returned": 5,
        "offset": 0,
        "limit": catalog.DEFAULT_MODELS_LIST_LIMIT,
        "has_more": False,
        "gateway": "all",
    }

    with (
        patch(
            "src.services.catalog_response_cache.get_cached_catalog_response",
            new=AsyncMock(return_value=cached),
        ),
        patch.object(Config, "HEALTH_GATING_ENABLED", True, create=True),
    ):
        body = _client().get("/v1/models").json()

    assert len(body["data"]) == 4
    assert body["returned"] == 4
    assert body["total"] == 4
