"""Regression tests for the 2026-11-27 security audit fixes.

* /api/monitoring/* and /analytics/transactions* are admin-only.
* /analytics/transactions only forwards an allowlisted `window`.
* /sentry-debug is not reachable in production.
* /health/database never returns raw exception text.
* Self-service rate limit updates are clamped and type-checked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_current_user, require_admin

ADMIN = {"id": 1, "role": "admin", "is_admin": True}
REGULAR_USER = {"id": 2, "role": "user", "is_admin": False}

MONITORING_GETS = [
    "/api/monitoring/health",
    "/api/monitoring/health/openrouter",
    "/api/monitoring/errors/openrouter",
    "/api/monitoring/stats/realtime",
    "/api/monitoring/stats/hourly/openrouter",
    "/api/monitoring/circuit-breakers",
    "/api/monitoring/circuit-breakers/openrouter",
    "/api/monitoring/providers/comparison",
    "/api/monitoring/latency/openrouter/gpt-4",
    "/api/monitoring/anomalies",
    "/api/monitoring/trial-analytics",
    "/api/monitoring/cost-analysis",
    "/api/monitoring/latency-trends/openrouter",
    "/api/monitoring/error-rates",
    "/api/monitoring/token-efficiency/openrouter/gpt-4",
    "/api/monitoring/chat-requests/providers",
    "/api/monitoring/chat-requests/counts",
    "/api/monitoring/chat-requests/models",
    "/api/monitoring/chat-requests",
    "/api/monitoring/chat-requests/plot-data",
]

ANALYTICS_GETS = ["/analytics/transactions", "/analytics/transactions/summary"]


@pytest.fixture
def client():
    app.dependency_overrides = {}
    yield TestClient(app)
    app.dependency_overrides = {}


def _registered_get_paths() -> set[str]:
    return {r.path for r in app.routes if "GET" in (getattr(r, "methods", None) or set())}


def test_every_monitoring_route_is_covered_here():
    """A new /api/monitoring GET must be added to MONITORING_GETS (and stay gated)."""
    registered = {p for p in _registered_get_paths() if p.startswith("/api/monitoring")}
    templated = {
        p.replace("{provider}", "openrouter").replace("{model}", "gpt-4") for p in registered
    }
    assert templated == set(MONITORING_GETS)


@pytest.mark.parametrize("path", MONITORING_GETS + ANALYTICS_GETS)
def test_anonymous_caller_is_rejected(client, path):
    response = client.get(path)
    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize("path", MONITORING_GETS + ANALYTICS_GETS)
def test_non_admin_key_is_rejected(client, path):
    app.dependency_overrides[get_current_user] = lambda: REGULAR_USER
    response = client.get(path, headers={"Authorization": "Bearer gw_test_regular"})
    assert response.status_code == 403, response.text


def test_sentry_tunnel_stays_public():
    """POST /monitoring (Sentry tunnel) is a separate router and must not need admin."""
    paths = {r.path for r in app.routes}
    assert "/monitoring" in paths


# ---------------------------------------------------------------------------
# /analytics/transactions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window", ["7d", "1d&admin=true", "1d#", ""])
def test_transactions_rejects_unlisted_window(client, window):
    app.dependency_overrides[require_admin] = lambda: ADMIN
    response = client.get("/analytics/transactions", params={"window": window})
    assert response.status_code == 400


def test_transactions_forwards_window_as_encoded_param(client):
    app.dependency_overrides[require_admin] = lambda: ADMIN
    upstream = MagicMock(status_code=200)
    upstream.json.return_value = {"data": {"data": []}}
    http = MagicMock()
    http.get = AsyncMock(return_value=upstream)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.routes.transaction_analytics.Config.OPENROUTER_COOKIE", "fake-cookie"),
        patch("src.routes.transaction_analytics.httpx.AsyncClient", return_value=http),
    ):
        response = client.get("/analytics/transactions", params={"window": "1mo"})

    assert response.status_code == 200
    args, kwargs = http.get.call_args
    assert "?" not in args[0]
    assert kwargs["params"] == {"window": "1mo"}


# ---------------------------------------------------------------------------
# /sentry-debug
# ---------------------------------------------------------------------------


def _sentry_debug_route(test_app):
    return next((r for r in test_app.routes if r.path == "/sentry-debug"), None)


def _create_app(is_production: bool):
    from src import main as main_module

    with (
        patch.object(main_module.Config, "SENTRY_ENABLED", True),
        patch.object(main_module.Config, "SENTRY_DSN", "https://public@o0.ingest.example/0"),
        patch.object(main_module.Config, "IS_PRODUCTION", is_production),
    ):
        return main_module.create_app()


def test_sentry_debug_not_registered_in_production():
    assert _sentry_debug_route(_create_app(is_production=True)) is None


def test_sentry_debug_returns_404_if_production_at_request_time():
    from src import main as main_module

    route = _sentry_debug_route(_create_app(is_production=False))
    assert route is not None
    with patch.object(main_module.Config, "IS_PRODUCTION", True):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route.endpoint())
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# /health/database
# ---------------------------------------------------------------------------


def test_database_health_hides_raw_exception_text():
    from src.routes import health

    secret_detail = "connect to " + "db.internal" + ":5432 password=" + "hunter" + "2"
    db = MagicMock()
    db.table.side_effect = ValueError(secret_detail)
    init_status = {"initialized": False, "has_error": True, "error_message": secret_detail}

    with (
        patch.object(health, "get_db", return_value=db),
        patch.object(health, "get_initialization_status", return_value=init_status),
        patch.object(health, "_is_transient_error", return_value=False),
    ):
        body = asyncio.run(health.database_health())

    assert body["status"] == "unhealthy"
    assert secret_detail not in repr(body)
    assert "error_message" not in body["initialization"]
    assert body["error_type"] == "ValueError"


# ---------------------------------------------------------------------------
# PUT /user/rate-limits/{key_id} clamp
# ---------------------------------------------------------------------------


def test_rate_limit_clamp_caps_drops_unknown_and_rejects_bad_types():
    from src.routes.rate_limits import _clamp_rate_limit_config
    from src.services.rate_limiting import RateLimitConfig

    maxima = RateLimitConfig()
    clamped = _clamp_rate_limit_config(
        {
            "requests_per_minute": 10**9,
            "tokens_per_day": 5,
            "burst_limit": 10**9,
            "window_size_seconds": 1,
        },
        REGULAR_USER,
    )
    assert clamped == {
        "requests_per_minute": maxima.requests_per_minute,
        "tokens_per_day": 5,
        "burst_limit": maxima.burst_limit,
    }

    for bad in (1e12, True, "999999", -1):
        with pytest.raises(HTTPException) as exc:
            _clamp_rate_limit_config({"concurrency_limit": bad}, REGULAR_USER)
        assert exc.value.status_code == 400


def test_rate_limit_clamp_exempts_admins():
    from src.routes.rate_limits import _clamp_rate_limit_config

    config = {"requests_per_minute": 10**9, "window_size_seconds": 1}
    assert _clamp_rate_limit_config(config, ADMIN) == config
