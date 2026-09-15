"""A throttled prober must not publish a "major outage".

Production, 2026-09-15: ``GET /v1/status`` reported ``major_outage`` at 30.3%
uptime over 66 monitored models (20 healthy, 31 degraded, 15 offline) while real
customer inference was healthy. 38 of the 123 tracking rows carried
``last_status: rate_limited`` with ``last_error_message: "Rate limit exceeded"``.

The monitor now records 429/auth outcomes as UNMEASURED
(``src/services/monitoring/intelligent_health_monitor.UNMEASURED_STATUSES``).
These tests pin the other half: the status page must read them as "we could not
measure this", not as "the model is broken" — while a model that is genuinely
failing still shows as degraded or offline.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.routes import catalog, status_page

NOW = datetime.now(UTC)


def _model(model_id: str) -> dict:
    gateway = model_id.split("/", 1)[0]
    return {"id": model_id, "source_gateway": gateway, "provider_slug": gateway}


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


def _db(tracking_rows: list[dict]):
    def table(name):
        q = MagicMock()
        for method in ("select", "eq", "order"):
            getattr(q, method).return_value = q
        bounds: dict = {}

        def _range(start, end):
            bounds["slice"] = (start, end)
            return q

        def _execute():
            if name == "model_health_tracking":
                start, end = bounds.get("slice", (0, len(tracking_rows) - 1))
                return MagicMock(data=tracking_rows[start : end + 1])
            if name == "model_health_incidents":
                return MagicMock(data=[], count=0)
            raise AssertionError(f"status must not read {name}")

        q.range.side_effect = _range
        q.execute.side_effect = _execute
        return q

    client = MagicMock()
    client.table.side_effect = table
    return client


@pytest.fixture
def wire(monkeypatch):
    def _wire(catalog_models: list[dict], tracking_rows: list[dict]):
        monkeypatch.setattr(catalog, "get_public_catalog_models", lambda: catalog_models)
        monkeypatch.setattr(status_page, "get_db", lambda: _db(tracking_rows))

    return _wire


class TestThrottledProbesAreNotAnOutage:
    @pytest.mark.asyncio
    async def test_rate_limited_models_are_unmonitored_not_degraded(self, wire):
        models = [_model(f"openai/m{i}") for i in range(4)]
        rows = [_row("openai/m0")] + [
            _row(f"openai/m{i}", status="rate_limited") for i in range(1, 4)
        ]
        wire(models, rows)

        data = await status_page.get_overall_status()

        assert data["degraded_models"] == 0, "a 429 is not model degradation"
        assert data["rate_limited_models"] == 3
        assert data["monitored_models"] == 1
        assert data["unmonitored_models"] == 3
        assert data["uptime_percentage"] == 100.0
        assert data["status"] == "operational"

    @pytest.mark.asyncio
    async def test_the_production_shape_stops_reading_as_a_major_outage(self, wire):
        """20 healthy + 38 throttled + 2 unauthorized, none genuinely broken."""
        models = [_model(f"openai/ok{i}") for i in range(20)]
        models += [_model(f"openai/rl{i}") for i in range(38)]
        models += [_model(f"openai/ua{i}") for i in range(2)]
        rows = [_row(f"openai/ok{i}") for i in range(20)]
        rows += [_row(f"openai/rl{i}", status="rate_limited") for i in range(38)]
        rows += [_row(f"openai/ua{i}", status="unauthorized") for i in range(2)]
        wire(models, rows)

        data = await status_page.get_overall_status()

        assert data["status"] == "operational"
        assert data["uptime_percentage"] == 100.0
        assert data["offline_models"] == 0
        assert data["rate_limited_models"] == 38
        assert data["unauthorized_models"] == 2

    @pytest.mark.asyncio
    async def test_unauthorized_is_surfaced_rather_than_hidden(self, wire):
        """Excluded from the verdict, but still reported — a missing provider
        key must be visible, not silently absorbed into 'unmonitored'."""
        models = [_model("openai/a"), _model("xai/b")]
        wire(models, [_row("openai/a"), _row("xai/b", status="unauthorized")])

        data = await status_page.get_overall_status()

        assert data["unauthorized_models"] == 1
        assert data["degraded_models"] == 0
        assert data["unmonitored_models"] == 1


class TestRealOutagesStillSurface:
    """The anti-over-correction half. If the page can no longer report a real
    outage, the fix traded one lie for another."""

    @pytest.mark.asyncio
    async def test_a_genuine_error_is_still_degraded(self, wire):
        models = [_model("openai/a"), _model("openai/b")]
        wire(models, [_row("openai/a"), _row("openai/b", status="error")])

        data = await status_page.get_overall_status()

        assert data["degraded_models"] == 1
        assert data["monitored_models"] == 2
        assert data["uptime_percentage"] == 50.0

    @pytest.mark.asyncio
    async def test_an_open_breaker_is_still_offline(self, wire):
        models = [_model(f"openai/m{i}") for i in range(4)]
        rows = [_row("openai/m0")] + [
            _row(f"openai/m{i}", status="error", circuit_breaker_state="open") for i in range(1, 4)
        ]
        wire(models, rows)

        data = await status_page.get_overall_status()

        assert data["offline_models"] == 3
        assert data["status"] == "major_outage"

    @pytest.mark.asyncio
    async def test_an_open_breaker_outranks_a_throttled_latest_probe(self, wire):
        """A model that earned an open breaker from real failures and then got a
        429 must stay offline — otherwise a throttled provider could mask a
        genuine outage."""
        models = [_model("openai/a")]
        wire(models, [_row("openai/a", status="rate_limited", circuit_breaker_state="open")])

        data = await status_page.get_overall_status()

        assert data["offline_models"] == 1
        assert data["monitored_models"] == 1
        assert data["rate_limited_models"] == 0
        assert data["status"] == "major_outage"


class TestModelStatusFormatting:
    """GET /v1/status/models returned 500 for every non-empty result set while
    /v1/status/search — the same view, fewer columns — returned 200. The route
    indexed rows with row["col"], so one column missing from the view took down
    the whole endpoint."""

    def test_a_view_missing_columns_degrades_instead_of_raising(self):
        row = {"model": "openai/gpt-4.1", "provider": "openai", "gateway": "openai"}

        out = status_page._format_model_status(row)

        assert out["model_id"] == "openai/gpt-4.1"
        assert out["uptime_24h"] == 0.0
        assert out["active_incidents"] == 0
        assert out["circuit_breaker_state"] is None

    def test_a_complete_row_is_formatted_faithfully(self):
        row = {
            "model": "openai/gpt-4.1",
            "provider": "openai",
            "gateway": "openai",
            "status_indicator": "operational",
            "monitoring_tier": "standard",
            "uptime_percentage_24h": 99.987,
            "uptime_percentage_7d": 99.5,
            "uptime_percentage_30d": 98.0,
            "average_response_time_ms": 1234.56,
            "last_called_at": "2026-09-15T00:00:00+00:00",
            "last_success_at": "2026-09-15T00:00:00+00:00",
            "last_failure_at": None,
            "circuit_breaker_state": "closed",
            "active_incidents_count": 2,
        }

        out = status_page._format_model_status(row)

        assert out["status"] == "operational"
        assert out["uptime_24h"] == 99.99
        assert out["avg_response_time_ms"] == 1235
        assert out["active_incidents"] == 2
        assert out["last_failure"] is None

    def test_a_non_numeric_cell_does_not_raise(self):
        assert status_page._round_or_zero("NaN-ish", 2) == 0.0
        assert status_page._round_or_zero(None, 2) == 0.0
        assert status_page._round_or_zero("12.5", 1) == 12.5

    def test_missing_columns_are_named_in_the_log(self, caplog):
        with caplog.at_level("WARNING"):
            status_page._warn_on_missing_columns(
                [{"model": "m", "provider": "p"}], status_page._MODEL_STATUS_COLUMNS
            )
        assert any("model_status_current is missing column" in r.message for r in caplog.records)

    def test_nothing_is_logged_for_a_complete_row(self, caplog):
        row = dict.fromkeys(status_page._MODEL_STATUS_COLUMNS, None)
        with caplog.at_level("WARNING"):
            status_page._warn_on_missing_columns([row], status_page._MODEL_STATUS_COLUMNS)
        assert not caplog.records
