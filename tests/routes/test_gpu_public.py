"""Tests for src.routes.gpu_public (gatewayz-backend#2263 #2264, spec §6).

No auth on these routes -- TestClient calls need no api key/header setup.
Every DB call is mocked at the src.routes.gpu_public import site (same
convention as tests/routes/test_staking.py); caching and rate limiting are
exercised against the route module's own cache/limiter primitives so tests
don't depend on a real Redis.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.gpu_public as gpu_public
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_local_cache():
    """The in-process fallback cache is a module-level dict; tests must not
    leak cached values into each other."""
    gpu_public._local_cache.clear()
    yield
    gpu_public._local_cache.clear()


@pytest.fixture(autouse=True)
def _bypass_rate_limit(monkeypatch):
    """Rate limiting is tested explicitly in its own tests below; everywhere
    else, force-allow so unrelated tests aren't flaky against shared state."""
    monkeypatch.setattr(
        gpu_public, "sliding_window_check", lambda key, limit, window: (True, limit, None)
    )


_SUMMARY = {
    "active_nodes": 3,
    "approved_providers": 2,
    "regions": [{"region": "us-east", "nodes": 3}],
    "models": [{"id": "llama-3.1-8b-instruct", "nodes": 3}],
    "last_hour": {"requests": 100, "tokens": 5000, "avg_latency_ms": 250, "error_rate": 0.01},
    "updated_at": "2026-09-03T18:00:00+00:00",
}

_NODES = [
    {
        "name": "node-1",
        "region": "us-east",
        "gpu_model": "A100",
        "vram_gb": 80,
        "status": "active",
        "uptime_24h_pct": 95.5,
        "models": ["llama-3.1-8b-instruct"],
    }
]

_UTIL_ROWS = [
    {
        "hour": "2026-09-03T17:00:00+00:00",
        "key": "us-east",
        "requests": 10,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "avg_latency_ms": 200,
        "error_rate": 0.0,
        "active_nodes": 1,
    }
]


# --- /gpu/public/summary -----------------------------------------------------


def test_summary_returns_data_and_cache_header():
    with patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY) as mock_summary:
        response = client.get("/gpu/public/summary")

    assert response.status_code == 200
    assert response.json() == _SUMMARY
    assert response.headers["cache-control"] == "public, max-age=30"
    mock_summary.assert_called_once()


def test_summary_is_cached_across_requests():
    with patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY) as mock_summary:
        client.get("/gpu/public/summary")
        client.get("/gpu/public/summary")

    mock_summary.assert_called_once()


# --- /gpu/public/nodes --------------------------------------------------------


def test_nodes_returns_bare_array():
    with patch("src.routes.gpu_public.get_public_nodes", return_value=_NODES):
        response = client.get("/gpu/public/nodes")

    assert response.status_code == 200
    assert response.json() == _NODES
    assert isinstance(response.json(), list)


def test_nodes_response_never_contains_forbidden_keys():
    with patch("src.routes.gpu_public.get_public_nodes", return_value=_NODES):
        response = client.get("/gpu/public/nodes")

    for node in response.json():
        assert set(node.keys()) == {
            "name",
            "region",
            "gpu_model",
            "vram_gb",
            "status",
            "uptime_24h_pct",
            "models",
        }


# --- /gpu/public/utilization ---------------------------------------------------


def test_utilization_default_window_and_group():
    with patch("src.routes.gpu_public.get_utilization", return_value=_UTIL_ROWS) as mock_util:
        response = client.get("/gpu/public/utilization")

    assert response.status_code == 200
    body = response.json()
    assert body["window"] == "24h"
    assert body["group"] == "region"
    assert body["series"] == _UTIL_ROWS
    mock_util.assert_called_once_with("24h", "region")


def test_utilization_accepts_7d_and_model_group():
    with patch("src.routes.gpu_public.get_utilization", return_value=[]) as mock_util:
        response = client.get("/gpu/public/utilization", params={"window": "7d", "group": "model"})

    assert response.status_code == 200
    mock_util.assert_called_once_with("7d", "model")


def test_utilization_rejects_invalid_window():
    response = client.get("/gpu/public/utilization", params={"window": "1h"})
    assert response.status_code == 422


def test_utilization_rejects_invalid_group():
    response = client.get("/gpu/public/utilization", params={"group": "provider"})
    assert response.status_code == 422


# --- /gpu/public/schema --------------------------------------------------------


def test_schema_endpoint_is_draft_2020_12_with_three_definitions():
    response = client.get("/gpu/public/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(body["definitions"].keys()) == {"summary", "nodes", "utilization"}


def test_schema_matches_committed_file():
    from src.routes.gpu_public import build_public_feed_schema

    live = build_public_feed_schema()
    with open("docs/gpu/public-feed.schema.json") as f:
        import json

        committed = json.load(f)

    assert live == committed


# --- caching: Redis available vs. in-process fallback --------------------------


def test_uses_redis_when_available():
    fake_redis_store: dict[str, str] = {}

    class _FakeRedisConfig:
        def is_available(self):
            return True

        def get_cache(self, key):
            return fake_redis_store.get(key)

        def set_cache(self, key, value, ttl=300):
            fake_redis_store[key] = value
            return True

    with (
        patch("src.routes.gpu_public.get_redis_config", return_value=_FakeRedisConfig()),
        patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY) as mock_summary,
    ):
        client.get("/gpu/public/summary")
        client.get("/gpu/public/summary")

    mock_summary.assert_called_once()
    assert fake_redis_store  # something was actually written to the fake Redis


def test_falls_back_to_in_process_cache_when_redis_unavailable():
    class _DownRedisConfig:
        def is_available(self):
            return False

    with (
        patch("src.routes.gpu_public.get_redis_config", return_value=_DownRedisConfig()),
        patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY) as mock_summary,
    ):
        client.get("/gpu/public/summary")
        client.get("/gpu/public/summary")

    mock_summary.assert_called_once()


# --- rate limiting: 60/min/IP --------------------------------------------------


def test_rate_limit_exceeded_returns_429_with_retry_after():
    with patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY):
        with patch.object(gpu_public, "sliding_window_check", return_value=(False, 0, 17)):
            response = client.get("/gpu/public/summary")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"


def test_rate_limit_allowed_passes_through():
    with patch("src.routes.gpu_public.get_summary", return_value=_SUMMARY):
        with patch.object(gpu_public, "sliding_window_check", return_value=(True, 59, None)):
            response = client.get("/gpu/public/summary")

    assert response.status_code == 200
