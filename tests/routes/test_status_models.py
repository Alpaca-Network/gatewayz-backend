"""
GET /v1/status/models returned 500 for every request in production.

Two defects, both invisible because the route wraps everything in
`except Exception -> HTTPException(500, "Failed to retrieve model status")`:

  1. it read `active_incidents_count`; the `model_status_current` view has
     `active_incidents` -> KeyError on the first row;
  2. PostgREST serialises Postgres `numeric` as a JSON string ("0.0"), and
     `round("0.0", 2)` raises TypeError.

The fixture below is a real row captured from the production view on
2026-09-15, strings and column names untouched, so it reproduces both.
"""

import pytest

from src.routes.status_page import _format_model_status, _num

# Verbatim from `select * from public.model_status_current limit 1` (prod).
LIVE_ROW = {
    "provider": "openai",
    "model": "openai/gpt-4-turbo-2024-04-09",
    "gateway": "openai",
    "monitoring_tier": "on_demand",
    "last_status": "rate_limited",
    "uptime_percentage_24h": "0.0",
    "uptime_percentage_7d": "12.3456",
    "uptime_percentage_30d": "99.999",
    "average_response_time_ms": "1402.2073906335047",
    "last_called_at": "2026-09-15 16:01:17.860466+00",
    "last_success_at": "2026-08-10 08:20:12.55105+00",
    "last_failure_at": "2026-09-15 16:01:17.860466+00",
    "circuit_breaker_state": "open",
    "consecutive_failures": 7,
    "usage_count_24h": 0,
    "is_enabled": True,
    "status_indicator": "major_outage",
    "active_incidents": 0,
}


def test_formats_a_real_view_row_without_raising():
    out = _format_model_status(LIVE_ROW)
    assert out["model_id"] == "openai/gpt-4-turbo-2024-04-09"
    assert out["status"] == "major_outage"
    assert out["circuit_breaker_state"] == "open"


def test_numeric_strings_become_rounded_numbers():
    out = _format_model_status(LIVE_ROW)
    assert out["uptime_24h"] == 0.0
    assert out["uptime_7d"] == 12.35
    assert out["uptime_30d"] == 100.0
    assert out["avg_response_time_ms"] == 1402


def test_reads_the_column_the_view_actually_has():
    """`active_incidents_count` does not exist; reading it 500'd the route."""
    assert _format_model_status(LIVE_ROW)["active_incidents"] == 0
    assert "active_incidents_count" not in _format_model_status(LIVE_ROW)


def test_missing_columns_degrade_to_none_not_an_exception():
    """A view change should cost one field, not the endpoint."""
    out = _format_model_status({"model": "m", "provider": "p"})
    assert out["model_id"] == "m"
    assert out["status"] is None
    assert out["uptime_24h"] == 0.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.0", 0.0), ("1402.21", 1402.21), (5, 5.0), (None, 0.0), ("", 0.0), ("junk", 0.0)],
)
def test_num_coercion(value, expected):
    assert _num(value) == expected
