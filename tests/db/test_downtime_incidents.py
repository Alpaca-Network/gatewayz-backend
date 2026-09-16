"""Tests for src/db/downtime_incidents.py (read paths behind the admin API).

These are fully mocked, but they live under tests/db/ and so are skipped by
tests/conftest.py's autouse `skip_if_no_database` whenever Supabase is
unreachable -- which is most local runs and any CI job without the secret. Do
not read a green line here as coverage without checking they ran.

The column-existence guard that used to live here now sits in
tests/schema/test_downtime_incidents_columns.py, which has no database
dependency and therefore actually executes.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.db.downtime_incidents import (
    DowntimeIncidentsUnavailable,
    count_incidents,
    get_incident,
    get_incident_statistics,
    get_incidents_by_date_range,
    get_recent_incidents,
    list_incidents,
)


def _client_returning(data=None, count=None):
    """A Supabase client mock whose every builder call chains back to itself,
    so the assertion can be about the final execute() rather than the exact
    order of .eq()/.order()/.range() calls."""
    client = MagicMock()
    builder = MagicMock()
    for method in ("select", "eq", "gte", "lte", "order", "limit", "range"):
        getattr(builder, method).return_value = builder
    result = MagicMock()
    result.data = data
    result.count = count
    builder.execute.return_value = result
    client.table.return_value = builder
    return client, builder


class TestListIncidents:
    def test_returns_page_and_total(self):
        client, _ = _client_returning(data=[{"id": "a"}], count=7)

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            rows, total = list_incidents(limit=1, offset=0)

        assert rows == [{"id": "a"}]
        assert total == 7

    def test_offset_past_end_returns_empty_without_a_range_request(self):
        """PostgREST answers an unsatisfiable range with 416, which would look
        like an outage instead of "you paged past the last page"."""
        client, builder = _client_returning(data=None, count=2)

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            rows, total = list_incidents(limit=50, offset=50)

        assert rows == []
        assert total == 2
        builder.range.assert_not_called()

    def test_applies_filters(self):
        client, builder = _client_returning(data=[], count=1)

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            list_incidents(limit=5, offset=0, status="ongoing", severity="critical")

        eq_calls = {call.args for call in builder.eq.call_args_list}
        assert ("status", "ongoing") in eq_calls
        assert ("severity", "critical") in eq_calls

    def test_read_failure_raises_instead_of_returning_empty(self):
        """The whole point: a broken query must not render as "no incidents"."""
        client, builder = _client_returning(count=5)
        builder.execute.side_effect = Exception("PGRST205 downtime_incidents not found")

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            with pytest.raises(DowntimeIncidentsUnavailable):
                list_incidents()


class TestCountIncidents:
    def test_counts_in_postgres_not_by_measuring_rows(self):
        """PostgREST caps responses at 1000 rows, so len(result.data) silently
        caps the total at 1000 too. Use a head count."""
        client, builder = _client_returning(data=None, count=4212)

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            total = count_incidents()

        assert total == 4212
        assert builder.select.call_args.kwargs.get("count") == "exact"
        assert builder.select.call_args.kwargs.get("head") is True

    def test_null_count_is_zero(self):
        client, _ = _client_returning(data=None, count=None)

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            assert count_incidents() == 0

    def test_failure_raises(self):
        client, builder = _client_returning()
        builder.execute.side_effect = Exception("boom")

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            with pytest.raises(DowntimeIncidentsUnavailable):
                count_incidents()


class TestGetIncident:
    def test_missing_incident_returns_none(self):
        client, _ = _client_returning(data=[])

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            assert get_incident("11111111-1111-1111-1111-111111111111") is None

    def test_failed_read_raises_rather_than_looking_like_a_404(self):
        client, builder = _client_returning()
        builder.execute.side_effect = Exception("connection reset")

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            with pytest.raises(DowntimeIncidentsUnavailable):
                get_incident("11111111-1111-1111-1111-111111111111")


class TestRecentAndRange:
    def test_get_recent_incidents_raises_on_failure(self):
        client, builder = _client_returning()
        builder.execute.side_effect = Exception("boom")

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            with pytest.raises(DowntimeIncidentsUnavailable):
                get_recent_incidents()

    def test_get_incidents_by_date_range_raises_on_failure(self):
        client, builder = _client_returning()
        builder.execute.side_effect = Exception("boom")

        with patch("src.config.supabase_config.get_supabase_client", return_value=client):
            with pytest.raises(DowntimeIncidentsUnavailable):
                get_incidents_by_date_range(datetime.now(UTC), datetime.now(UTC))


class TestStatistics:
    def test_aggregates_real_rows(self):
        rows = [
            {"severity": "critical", "status": "resolved", "duration_seconds": 300},
            {"severity": "high", "status": "resolved", "duration_seconds": 100},
            {"severity": "high", "status": "ongoing"},
        ]
        with patch("src.db.downtime_incidents.get_incidents_by_date_range", return_value=rows):
            stats = get_incident_statistics(days=30)

        assert stats["total_incidents"] == 3
        assert stats["total_downtime_seconds"] == 400
        assert stats["average_duration_seconds"] == 133
        assert stats["by_severity"] == {"critical": 1, "high": 2}
        assert stats["by_status"] == {"resolved": 2, "ongoing": 1}

    def test_no_incidents_is_a_real_zero(self):
        with patch("src.db.downtime_incidents.get_incidents_by_date_range", return_value=[]):
            stats = get_incident_statistics(days=7)
        assert stats["total_incidents"] == 0

    def test_null_severity_buckets_as_unknown_not_dropped(self):
        """severity is nullable on the live table (no NOT NULL), so a NULL must
        not silently disappear from the histogram."""
        rows = [{"severity": None, "status": None, "duration_seconds": 60}]
        with patch("src.db.downtime_incidents.get_incidents_by_date_range", return_value=rows):
            stats = get_incident_statistics(days=30)

        assert stats["by_severity"] == {"unknown": 1}
        assert stats["by_status"] == {"unknown": 1}

    def test_broken_read_propagates_instead_of_reporting_zero_downtime(self):
        """Zeroed stats read as a perfect month. That is the failure this
        codebase has shipped repeatedly."""
        with patch(
            "src.db.downtime_incidents.get_incidents_by_date_range",
            side_effect=DowntimeIncidentsUnavailable("boom"),
        ):
            with pytest.raises(DowntimeIncidentsUnavailable):
                get_incident_statistics(days=30)
