"""The public status page must not report absence of data as failure.

``/v1/status/stats`` is public and unauthenticated. It computed
``success_rate`` as ``0`` whenever no health checks existed in the last 24h,
which reads as "this gateway failed 100% of its checks". Nothing writes
``model_health_history`` today, so that was the branch production served.
"""

from unittest.mock import MagicMock

import pytest

from src.routes import status_page


def _db(check_rows: list[dict]):
    """Fake PostgREST client returning `check_rows` for model_health_history."""
    rows_by_table = {
        "model_health_tracking": [{"monitoring_tier": "standard"}],
        "model_health_incidents": [],
        "model_health_history": check_rows,
    }

    def table(name):
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.gte.return_value = q
        q.execute.return_value = MagicMock(
            data=rows_by_table.get(name, []), count=len(rows_by_table.get(name, []))
        )
        return q

    client = MagicMock()
    client.table.side_effect = table
    return client


@pytest.mark.asyncio
async def test_no_checks_reports_null_not_zero(monkeypatch):
    monkeypatch.setattr(status_page, "get_db", lambda: _db([]))

    stats = await status_page.get_stats()

    assert stats["checks_24h"]["total"] == 0
    assert stats["checks_24h"]["success_rate"] is None, "no samples must not read as 0%"
    assert stats["checks_24h"]["monitoring_active"] is False


@pytest.mark.asyncio
async def test_real_checks_still_compute_a_rate(monkeypatch):
    rows = [{"status": "success"}, {"status": "success"}, {"status": "failure"}]
    monkeypatch.setattr(status_page, "get_db", lambda: _db(rows))

    stats = await status_page.get_stats()

    assert stats["checks_24h"]["total"] == 3
    assert stats["checks_24h"]["successful"] == 2
    assert stats["checks_24h"]["failed"] == 1
    assert stats["checks_24h"]["success_rate"] == pytest.approx(66.67)
    assert stats["checks_24h"]["monitoring_active"] is True


@pytest.mark.asyncio
async def test_all_failing_is_distinguishable_from_no_data(monkeypatch):
    """A real 0% must still be reportable — the fix must not swallow it."""
    monkeypatch.setattr(status_page, "get_db", lambda: _db([{"status": "failure"}]))

    stats = await status_page.get_stats()

    assert stats["checks_24h"]["success_rate"] == 0.0
    assert stats["checks_24h"]["monitoring_active"] is True
