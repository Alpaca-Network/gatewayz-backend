"""CORS_EXTRA_ORIGINS adds exact origins outside production only."""

import pytest
from fastapi.testclient import TestClient

from src.config.config import Config
from src.main import create_app

PREVIEW = "https://gatewayz-frontend-git-demo-alpaca-network.vercel.app"


def _preflight(app, origin):
    return TestClient(app).options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


@pytest.mark.parametrize("is_production, allowed", [(False, True), (True, False)])
def test_extra_origin_is_allowed_only_outside_production(monkeypatch, is_production, allowed):
    monkeypatch.setenv("CORS_EXTRA_ORIGINS", f" {PREVIEW}/ , ")
    monkeypatch.setattr(Config, "IS_PRODUCTION", is_production)
    resp = _preflight(create_app(), PREVIEW)
    assert (resp.headers.get("access-control-allow-origin") == PREVIEW) is allowed
