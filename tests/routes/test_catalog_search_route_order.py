"""Regression tests: /v1/models/search must not be shadowed by the dynamic
/models/{developer_name} catch-all route.

Bug: FastAPI/Starlette matches routes in registration order. When the static
`/models/search` route is registered *after* the dynamic
`/models/{developer_name}` route, a request to `GET /v1/models/search?q=...`
gets captured by the developer-catch-all handler (with developer_name="search")
instead of the dedicated search handler — silently returning the wrong (empty)
response shape.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routes.catalog as catalog

MOCK_MODELS = [
    {
        "id": "openai/gpt-4-turbo",
        "name": "GPT-4 Turbo",
        "description": "OpenAI flagship model",
        "provider": "openai",
        "provider_slug": "openai",
        "source_gateway": "openai",
        "health_status": "healthy",
        "context_length": 128000,
        "pricing": {"prompt": 0.01, "completion": 0.03},
    }
]


def _make_client() -> TestClient:
    """Build a minimal app that mounts the real catalog router the same way
    src/main.py does (v1 router with '/v1' prefix), so route registration
    order is exactly what production sees."""
    app = FastAPI()
    app.include_router(catalog.router, prefix="/v1")
    return TestClient(app)


class TestModelsSearchRouteOrder:
    def test_search_route_registered_before_developer_catchall(self):
        """Static /models/search must appear before the dynamic
        /models/{developer_name} catch-all in the router's route list —
        otherwise Starlette will match 'search' as a developer_name."""
        paths = [r.path for r in catalog.router.routes if hasattr(r, "path")]

        search_idx = paths.index("/models/search")
        developer_idx = paths.index("/models/{developer_name}")

        assert search_idx < developer_idx, (
            "/models/search is registered AFTER /models/{developer_name}; "
            "requests to /models/search will be shadowed by the developer "
            "catch-all route."
        )

    def test_other_static_models_routes_precede_developer_catchall(self):
        """/models/trending, /models/low-latency, /models/unique, and
        /models/by-category/{category} are also static-ish siblings of the
        dynamic catch-all and must stay registered ahead of it."""
        paths = [r.path for r in catalog.router.routes if hasattr(r, "path")]
        developer_idx = paths.index("/models/{developer_name}")

        for static_path in (
            "/models/by-category/{category}",
            "/models/trending",
            "/models/low-latency",
            "/models/unique",
            "/models/search",
        ):
            assert (
                paths.index(static_path) < developer_idx
            ), f"{static_path} must be registered before /models/{{developer_name}}"

    def test_models_search_endpoint_returns_search_handler_shape(self):
        """End-to-end proof via the real router mount order: GET
        /v1/models/search?q=gpt must hit search_models (success/data/meta
        shape), never get_developer_models_api (developer/models/total shape)."""
        client = _make_client()

        with (
            patch(
                "src.routes.catalog.get_cached_models", return_value=MOCK_MODELS
            ) as get_cached_models,
            patch(
                "src.services.cache.catalog_response_cache.get_redis_client",
                return_value=None,
            ),
        ):
            resp = client.get("/v1/models/search", params={"q": "gpt"})

        assert resp.status_code == 200
        body = resp.json()

        # search_models() response shape
        assert body.get("success") is True
        assert "data" in body
        assert "meta" in body
        assert body["meta"]["filters_applied"]["query"] == "gpt"
        get_cached_models.assert_called_once_with("all")

        # Must NOT be the get_developer_models_api() shadowed shape, which
        # has a "developer" key and no "success"/"meta" keys.
        assert "developer" not in body

    def test_models_search_does_not_read_disabled_provider_cache(self):
        client = _make_client()

        with (
            patch(
                "src.routes.catalog.get_enabled_providers",
                return_value=frozenset({"openai"}),
            ),
            patch("src.routes.catalog.is_provider_enabled", return_value=False),
            patch("src.routes.catalog.get_cached_models") as get_cached_models,
        ):
            resp = client.get(
                "/v1/models/search",
                params={"q": "command", "gateway": "openrouter"},
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []
        get_cached_models.assert_not_called()
