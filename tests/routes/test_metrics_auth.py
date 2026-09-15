"""
/metrics and /api/metrics/parsed publish request volume per route and
credits_used_total per model. They used to be open to anyone; these tests pin
the gate (src/main.py::_require_metrics_auth) in both directions.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import create_app

TOKEN = "metrics-token-abcdef123456"
ADMIN = "admin-key-abcdef123456"
PATHS = ["/metrics", "/api/metrics/parsed"]


@pytest.fixture
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_ambient_admin_key(monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)


@pytest.mark.parametrize("path", PATHS)
def test_token_configured_rejects_anonymous(client, path, monkeypatch):
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", TOKEN)
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_token_configured_rejects_wrong_token(client, path, monkeypatch):
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", TOKEN)
    r = client.get(path, headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", PATHS)
def test_correct_token_is_served(client, path, monkeypatch):
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", TOKEN)
    r = client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_admin_key_also_works(client, path, monkeypatch):
    """A scraper keeps its own token; admin tooling needs no second secret."""
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", None)
    with patch.dict(os.environ, {"ADMIN_API_KEY": ADMIN}):
        assert client.get(path, headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_unconfigured_production_fails_closed(client, path, monkeypatch):
    """No token in production must not mean "serve it to everyone"."""
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", None)
    monkeypatch.setattr("src.main.Config.IS_PRODUCTION", True)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", PATHS)
def test_unconfigured_non_production_stays_open(client, path, monkeypatch):
    """curl localhost:8000/metrics while developing is the point."""
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", None)
    monkeypatch.setattr("src.main.Config.IS_PRODUCTION", False)
    assert client.get(path).status_code == 200


def test_parsed_metrics_reads_the_registry_not_its_own_endpoint(client, monkeypatch):
    """
    The parsed route used to fetch http://localhost:8000/metrics. That would now
    have to authenticate to itself (and assumed port 8000). It must not make an
    outbound call at all.
    """
    monkeypatch.setattr("src.main.Config.METRICS_TOKEN", TOKEN)

    async def explode(*_a, **_k):  # pragma: no cover - only runs on regression
        raise AssertionError("parsed metrics made an HTTP request to itself")

    monkeypatch.setattr("httpx.AsyncClient.get", explode)
    r = client.get("/api/metrics/parsed", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert set(r.json()) >= {"latency", "requests", "errors"}
