"""/health/quick reports the commit serving the request.

Written after gatewayz-backend#2298, where one half of a two-file commit was
live in production and the other half was not. Diagnosing that took a week of
black-box probing -- one model id at a time -- because nothing the service
serves says which build is answering. `SENTRY_RELEASE` and `APP_VERSION` are
static strings baked into the source; they cannot distinguish two deploys.

The rule this encodes: a service that cannot tell you what it is running makes
every deployment question a matter of inference.
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from src.config.config import Config


def _client():
    from src.routes import health

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


def test_quick_health_reports_a_commit():
    r = _client().get("/health/quick")
    assert r.status_code == 200
    assert "commit" in r.json(), "a build with no commit field cannot be identified"


def test_commit_is_short_enough_to_read():
    commit = _client().get("/health/quick").json()["commit"]
    assert len(commit) <= 12


def test_unknown_is_reported_honestly(monkeypatch):
    # Absent host injection the answer is "unknown" -- which says the process
    # cannot tell you, and is not the same as asserting a value.
    monkeypatch.setattr(Config, "BUILD_COMMIT", "unknown", raising=False)
    assert _client().get("/health/quick").json()["commit"] == "unknown"


def test_railway_commit_sha_is_picked_up(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890abcdef")
    import src.config.config as config_module

    importlib.reload(config_module)
    assert config_module.Config.BUILD_COMMIT.startswith("abcdef123456")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    importlib.reload(config_module)


def test_quick_health_still_does_no_io():
    # The endpoint's whole purpose is a zero-I/O uptime probe; the commit is
    # resolved once at import, not looked up per request.
    import inspect

    from src.routes import health

    src = inspect.getsource(health.health_quick)
    for forbidden in ("await ", "get_supabase", "redis", "requests.", "httpx"):
        assert forbidden not in src, f"health_quick must stay I/O-free ({forbidden})"
