"""
The status surface reported stale rows as healthy.

#2325 gave `GET /v1/status` a 24h measurement window, but the routes *under* it
kept serving the view raw: on 2026-09-15 production's `/v1/status/models` showed
40 of 100 models as `operational` at `uptime_24h: 100.0` whose newest health
check was 50 days old (circuit breaker `half_open`, `last_success: null`).

The fixture is that real row.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.routes.status_page import (
    MEASUREMENT_MAX_AGE,
    _format_model_status,
    _is_missing_table,
    _is_stale,
)

NOW = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)

# Verbatim from production /v1/status/models, 2026-09-15.
STALE_ROW = {
    "provider": "anthropic",
    "model": "anthropic/claude-3-haiku",
    "gateway": "anthropic",
    "monitoring_tier": "on_demand",
    "uptime_percentage_24h": "100.00",
    "uptime_percentage_7d": "100.00",
    "uptime_percentage_30d": "100.00",
    "average_response_time_ms": "258.0",
    "last_called_at": "2026-07-26T18:04:32.423812+00:00",
    "last_success_at": None,
    "last_failure_at": "2026-07-26T18:04:32.423812+00:00",
    "circuit_breaker_state": "half_open",
    "status_indicator": "operational",
    "active_incidents": 0,
}

FRESH_ROW = {
    **STALE_ROW,
    "last_called_at": (NOW - timedelta(hours=1)).isoformat(),
    "last_success_at": (NOW - timedelta(hours=1)).isoformat(),
    "circuit_breaker_state": "closed",
}


def test_a_50_day_old_check_is_not_operational():
    out = _format_model_status(STALE_ROW, NOW)
    assert out["status"] == "unknown"
    assert out["monitored"] is False
    assert out["uptime_24h"] is None
    assert "24h" in out["unmonitored_reason"]


def test_the_last_known_reading_is_kept_but_labelled():
    """Callers can still show the old figure — dated, and not as current health."""
    out = _format_model_status(STALE_ROW, NOW)
    assert out["last_measured_uptime_24h"] == 100.0
    assert out["last_measured_status"] == "operational"
    assert out["last_checked"] == STALE_ROW["last_called_at"]


def test_a_fresh_row_still_reports_its_measurement():
    out = _format_model_status(FRESH_ROW, NOW)
    assert out["monitored"] is True
    assert out["status"] == "operational"
    assert out["uptime_24h"] == 100.0
    assert out["avg_response_time_ms"] == 258


def test_a_measured_failure_is_not_hidden_by_the_window():
    """The window must not turn a real, current outage into "unknown"."""
    down = {**FRESH_ROW, "status_indicator": "major_outage", "uptime_percentage_24h": "0.0"}
    out = _format_model_status(down, NOW)
    assert out["monitored"] is True
    assert out["status"] == "major_outage"
    assert out["uptime_24h"] == 0.0


@pytest.mark.parametrize(
    ("last_called_at", "stale"),
    [
        (None, True),
        ("", True),
        ("not-a-timestamp", True),
        ((NOW - MEASUREMENT_MAX_AGE - timedelta(minutes=1)).isoformat(), True),
        ((NOW - MEASUREMENT_MAX_AGE + timedelta(minutes=1)).isoformat(), False),
    ],
)
def test_staleness_boundaries(last_called_at, stale):
    assert _is_stale({"last_called_at": last_called_at}, NOW) is stale


class _PgError(Exception):
    code = "42P01"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_PgError("relation does not exist"), True),
        (Exception('relation "model_health_aggregates" does not exist'), True),
        (Exception("connection reset by peer"), False),
    ],
)
def test_missing_table_detection(error, expected):
    """A missing table is permanent; a dropped connection is not. Don't conflate."""
    assert _is_missing_table(error) is expected
