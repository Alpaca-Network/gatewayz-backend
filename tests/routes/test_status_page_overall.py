"""``GET /v1/status`` must report only what is measured.

Production served ``major_outage`` / ``uptime_percentage: 34.96`` /
``total_models: 123`` while ``/v1/models`` listed ~70 models and inference was
healthy. Every figure came from the ``provider_health_current`` view, which
counts every enabled ``model_health_tracking`` row — delisted models, rows the
prober stopped refreshing, and verdicts days old — as if it were live state.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.routes import catalog, status_page

NOW = datetime.now(UTC)


def _model(model_id: str, **extra) -> dict:
    gateway = model_id.split("/", 1)[0]
    return {"id": model_id, "source_gateway": gateway, "provider_slug": gateway, **extra}


def _row(model: str, status: str = "success", age: timedelta = timedelta(minutes=5), **extra):
    return {
        "provider": model.split("/", 1)[0],
        "model": model,
        "gateway": model.split("/", 1)[0],
        "last_status": status,
        "last_called_at": (NOW - age).isoformat(),
        "circuit_breaker_state": "closed",
        **extra,
    }


def _db(tracking_rows: list[dict], active_incidents: int = 0):
    """Fake PostgREST client: pages ``model_health_tracking`` honouring range()."""

    def table(name):
        q = MagicMock()
        for method in ("select", "eq", "order"):
            getattr(q, method).return_value = q
        bounds = {}

        def _range(start, end):
            bounds["slice"] = (start, end)
            return q

        def _execute():
            if name == "model_health_tracking":
                start, end = bounds.get("slice", (0, len(tracking_rows) - 1))
                return MagicMock(data=tracking_rows[start : end + 1])
            if name == "model_health_incidents":
                return MagicMock(data=[], count=active_incidents)
            raise AssertionError(f"status must not read {name}")

        q.range.side_effect = _range
        q.execute.side_effect = _execute
        return q

    client = MagicMock()
    client.table.side_effect = table
    return client


@pytest.fixture
def wire(monkeypatch):
    def _wire(catalog_models: list[dict], tracking_rows: list[dict], **kw):
        monkeypatch.setattr(catalog, "get_public_catalog_models", lambda: catalog_models)
        monkeypatch.setattr(status_page, "get_db", lambda: _db(tracking_rows, **kw))

    return _wire


@pytest.mark.asyncio
async def test_unknown_models_do_not_count_as_down(wire):
    """Four models, one measured and healthy: operational, 100%, 3 unmonitored."""
    models = [_model(f"openai/m{i}") for i in range(4)]
    wire(models, [_row("openai/m0")])

    data = await status_page.get_overall_status()

    assert data["total_models"] == 4
    assert data["monitored_models"] == 1
    assert data["unmonitored_models"] == 3
    assert data["healthy_models"] == 1
    assert data["offline_models"] == 0
    assert data["uptime_percentage"] == 100.0
    assert data["status"] == "operational"


@pytest.mark.asyncio
async def test_stale_measurements_and_delisted_rows_are_excluded(wire):
    """The production shape: a week-old failure and rows for delisted models."""
    models = [_model("openai/live"), _model("meta/old-probe")]
    rows = [
        _row("openai/live"),
        _row(
            "meta/old-probe",
            status="error",
            age=timedelta(days=11),
            circuit_breaker_state="open",
        ),
        *[
            _row(f"anthropic/claude-3-{i}", status="not_found", circuit_breaker_state="open")
            for i in range(30)
        ],
    ]
    wire(models, rows)

    data = await status_page.get_overall_status()

    assert data["total_models"] == 2
    assert data["monitored_models"] == 1
    assert data["offline_models"] == 0, "stale/delisted breakers are not current outages"
    assert data["excluded_tracking_rows"] == 30
    assert data["status"] == "operational"


@pytest.mark.asyncio
async def test_no_measurements_reports_null_uptime_with_reason(wire):
    wire([_model("openai/a"), _model("xai/b")], [])

    data = await status_page.get_overall_status()

    assert data["uptime_percentage"] is None, "no samples must not read as a percentage"
    assert data["uptime_reason"]
    assert data["status"] == "unknown"
    assert data["total_models"] == 2
    assert data["unmonitored_models"] == 2
    assert data["gateway_health_percentage"] is None
    assert data["total_gateways"] == 2


@pytest.mark.asyncio
async def test_real_measured_outage_is_still_reported(wire):
    """The fix must not swallow a genuine measured failure."""
    models = [_model(f"openai/m{i}") for i in range(3)]
    rows = [_row(f"openai/m{i}", status="error", circuit_breaker_state="open") for i in range(3)]
    wire(models, rows, active_incidents=2)

    data = await status_page.get_overall_status()

    assert data["status"] == "major_outage"
    assert data["uptime_percentage"] == 0.0
    assert data["offline_models"] == 3
    assert data["healthy_models"] == 0
    assert data["active_incidents"] == 2
    assert data["healthy_gateways"] == 0
    assert data["gateway_health_percentage"] == 0.0


@pytest.mark.asyncio
async def test_counts_are_consistent_and_gateways_scoped_to_measured(wire):
    models = [
        _model("openai/a"),
        _model("openai/b"),
        _model("xai/c"),
        _model("moonshot/d"),  # never probed
    ]
    rows = [
        _row("openai/a"),
        _row("openai/b", status="rate_limited"),
        _row("xai/c", status="error", circuit_breaker_state="open"),
        # an older duplicate for the same model must not override the latest check
        {**_row("openai/a", status="error", age=timedelta(hours=3)), "provider": "legacy"},
    ]
    wire(models, rows)

    data = await status_page.get_overall_status()

    assert data["healthy_models"] + data["degraded_models"] + data["offline_models"] == (
        data["monitored_models"]
    )
    assert data["monitored_models"] + data["unmonitored_models"] == data["total_models"]
    assert (data["healthy_models"], data["degraded_models"], data["offline_models"]) == (1, 1, 1)
    assert data["total_gateways"] == 3
    assert data["monitored_gateways"] == 2
    assert data["healthy_gateways"] == 1
    assert data["gateway_health_percentage"] == 50.0
    assert data["total_providers"] == 3


@pytest.mark.asyncio
async def test_bare_tracking_ids_match_their_gateway_prefixed_catalog_id(wire):
    wire([_model("openai/gpt-x")], [{**_row("openai/gpt-x"), "model": "gpt-x"}])

    data = await status_page.get_overall_status()

    assert data["monitored_models"] == 1
    assert data["excluded_tracking_rows"] == 0


@pytest.mark.asyncio
async def test_tracking_rows_are_paged_past_postgrest_row_cap(wire, monkeypatch):
    monkeypatch.setattr(status_page, "_TRACKING_PAGE_SIZE", 2)
    models = [_model(f"openai/m{i}") for i in range(5)]
    wire(models, [_row(f"openai/m{i}") for i in range(5)])

    data = await status_page.get_overall_status()

    assert data["monitored_models"] == 5


@pytest.mark.asyncio
async def test_empty_catalog_is_unknown_not_outage(wire):
    wire([], [_row("openai/a", status="error", circuit_breaker_state="open")])

    data = await status_page.get_overall_status()

    assert data["status"] == "unknown"
    assert data["uptime_percentage"] is None
    assert data["total_models"] == 0


def test_http_shape_keeps_legacy_fields(wire):
    from fastapi.testclient import TestClient

    from src.main import create_app

    wire([_model("openai/a")], [_row("openai/a")])
    response = TestClient(create_app()).get("/v1/status/")

    assert response.status_code == 200
    data = response.json()
    for legacy in (
        "status",
        "status_message",
        "uptime_percentage",
        "total_models",
        "healthy_models",
        "offline_models",
        "total_providers",
        "total_gateways",
        "healthy_gateways",
        "gateway_health_percentage",
        "active_incidents",
        "last_updated",
    ):
        assert legacy in data, legacy


@pytest.mark.asyncio
async def test_total_models_matches_the_v1_models_catalog_total(monkeypatch):
    """``total_models`` and ``/v1/models`` ``total`` come from one source and filter set."""
    from src.config.config import Config

    served = [_model(f"openai/m{i}", health_status="healthy") for i in range(3)]
    cached = [
        *served,
        _model("xai/gone", health_status="down"),  # hidden by health gating
    ]

    monkeypatch.setattr(Config, "HEALTH_GATING_ENABLED", True, raising=False)
    monkeypatch.setattr(
        catalog, "get_cached_models", lambda gw="openrouter", **_: cached if gw == "all" else []
    )
    monkeypatch.setattr(catalog, "get_cached_providers", lambda: [])

    async def _miss(*_a, **_k):
        return None

    async def _noop(*_a, **_k):
        return None

    import src.services.catalog_response_cache as response_cache

    monkeypatch.setattr(response_cache, "get_cached_catalog_response", _miss)
    monkeypatch.setattr(response_cache, "cache_catalog_response", _noop)

    response = await catalog.get_models(
        provider=None, is_private=None, limit=1000, offset=0, gateway="all", unique_models=False
    )
    payload = json.loads(response.body) if hasattr(response, "body") else response

    helper = catalog.get_public_catalog_models()
    assert payload["total"] == len(helper) == 3
    assert {m["id"] for m in helper} == {m["id"] for m in payload["data"]}
