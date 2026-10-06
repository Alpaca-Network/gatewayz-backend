"""GET /v1/status/model?id=<full id> -- public per-model availability.

Exists because the path form /v1/status/models/{provider}/{model_id} cannot
address a slash-containing id: measured 2026-10-05, /v1/status/models/
anthropic/claude-fable-5 -> 404, while /v1/status/models lists that exact id
as operational. model_status_current.model stores the FULL id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes import status_page


def _row(provider, status, *, model="anthropic/claude-fable-5", fresh=True):
    when = datetime.now(UTC) - (timedelta(minutes=5) if fresh else timedelta(days=30))
    return {
        "provider": provider,
        "model": model,
        "gateway": provider,
        "status_indicator": status,
        "last_called_at": when.isoformat(),
        "uptime_percentage_24h": "100.0",
        "uptime_percentage_7d": "100.0",
        "uptime_percentage_30d": "100.0",
        "average_response_time_ms": "1345",
        "circuit_breaker_state": "closed",
        "active_incidents": 0,
    }


@pytest.fixture
def client_with_rows(monkeypatch):
    seen = {}

    def make(rows):
        chain = MagicMock()
        chain.select.return_value = chain

        def _eq(col, val):
            seen[col] = val
            return chain

        chain.eq.side_effect = _eq
        chain.execute.return_value = SimpleNamespace(data=rows)
        db = MagicMock()
        db.table.return_value = chain
        monkeypatch.setattr(status_page, "get_db", lambda: db)
        app = FastAPI()
        app.include_router(status_page.router, prefix="/v1")
        return TestClient(app), seen

    return make


def test_full_slash_id_is_addressable_without_auth(client_with_rows):
    client, seen = client_with_rows([_row("anthropic", "operational")])
    resp = client.get("/v1/status/model", params={"id": "anthropic/claude-fable-5"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen == {"model": "anthropic/claude-fable-5"}  # full id, not split
    assert body["available"] is True
    assert body["providers"][0]["status"] == "operational"


def test_unavailable_when_no_provider_is_serving(client_with_rows):
    client, _ = client_with_rows([_row("openai", "major_outage", model="openai/gpt-4o")])
    body = client.get("/v1/status/model", params={"id": "openai/gpt-4o"}).json()
    assert body["available"] is False


def test_stale_measurement_is_not_available(client_with_rows):
    client, _ = client_with_rows([_row("anthropic", "operational", fresh=False)])
    body = client.get("/v1/status/model", params={"id": "anthropic/claude-fable-5"}).json()
    assert body["providers"][0]["status"] == "unknown"
    assert body["available"] is False


def test_any_serving_provider_makes_it_available(client_with_rows):
    client, _ = client_with_rows(
        [_row("openai", "offline", model="m/x"), _row("azure", "degraded", model="m/x")]
    )
    assert client.get("/v1/status/model", params={"id": "m/x"}).json()["available"] is True


def test_unmonitored_model_is_a_typed_404(client_with_rows):
    client, _ = client_with_rows([])
    resp = client.get("/v1/status/model", params={"id": "google/gemini-2.5-flash"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "model_not_monitored"
