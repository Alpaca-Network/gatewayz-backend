"""Tests for src/routes/admin_downtime.py (admin downtime incident API).

The assertions are written against the contract the deployed admin panel
already expects -- `admin-panel/src/types/downtime.ts` and the proxy routes
under `admin-panel/src/app/api/proxy/admin/downtime/`.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.db.downtime_incidents import DowntimeIncidentsUnavailable
from src.main import app
from src.security.deps import require_admin

client = TestClient(app)

ADMIN = {"id": 2, "email": "admin@example.com", "role": "admin"}

INCIDENT_ID = "11111111-1111-1111-1111-111111111111"

# Shaped like a real row: `ended_at`, not `resolved_at`.
ROW = {
    "id": INCIDENT_ID,
    "status": "resolved",
    "severity": "critical",
    "environment": "production",
    "started_at": "2026-09-01T10:00:00+00:00",
    "detected_at": "2026-09-01T10:01:00+00:00",
    "ended_at": "2026-09-01T10:30:00+00:00",
    "duration_seconds": 1800,
    "health_endpoint": "/health",
    "error_message": "connection refused",
    "http_status_code": 502,
    "resolved_by": "auto",
    "log_count": 0,
    "notes": None,
}


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around every
    test here -- popping only our own keys is not enough under xdist, where
    another module's never-restored override can interleave with these."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: ADMIN


class TestAuth:
    def test_list_requires_admin(self):
        assert client.get("/admin/downtime/incidents").status_code in (401, 403)

    def test_statistics_requires_admin(self):
        assert client.get("/admin/downtime/statistics").status_code in (401, 403)

    def test_detail_requires_admin(self):
        assert client.get(f"/admin/downtime/incidents/{INCIDENT_ID}").status_code in (401, 403)

    def test_logs_requires_admin(self):
        response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")
        assert response.status_code in (401, 403)

    def test_analysis_requires_admin(self):
        response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")
        assert response.status_code in (401, 403)


class TestListIncidents:
    def test_returns_panel_shape(self, admin_override):
        with (
            patch("src.routes.admin_downtime.list_incidents", return_value=([ROW], 1)),
            patch("src.routes.admin_downtime.count_incidents", return_value=0),
        ):
            response = client.get("/admin/downtime/incidents?limit=50&offset=0")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["total_incidents"] == 1
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert {"ongoing", "resolved", "incidents"} <= set(body)

    def test_maps_ended_at_to_resolved_at(self, admin_override):
        """The panel reads `resolved_at`; the column is `ended_at`. There is no
        resolved_at column -- selecting one is the phantom-column bug class."""
        with (
            patch("src.routes.admin_downtime.list_incidents", return_value=([ROW], 1)),
            patch("src.routes.admin_downtime.count_incidents", return_value=0),
        ):
            response = client.get("/admin/downtime/incidents")

        incident = response.json()["incidents"][0]
        assert incident["resolved_at"] == ROW["ended_at"]
        assert "ended_at" not in incident

    def test_forwards_pagination_and_filters(self, admin_override):
        with (
            patch("src.routes.admin_downtime.list_incidents", return_value=([], 0)) as mock_list,
            patch("src.routes.admin_downtime.count_incidents", return_value=0),
        ):
            client.get(
                "/admin/downtime/incidents"
                "?limit=25&offset=50&status=ongoing&severity=high&environment=staging"
            )

        kwargs = mock_list.call_args.kwargs
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 50
        assert kwargs["status"] == "ongoing"
        assert kwargs["severity"] == "high"
        assert kwargs["environment"] == "staging"

    def test_ongoing_count_ignores_the_status_filter(self, admin_override):
        """Filtering the table to resolved incidents must not make the
        "Ongoing" stat card read zero."""
        with (
            patch("src.routes.admin_downtime.list_incidents", return_value=([], 4)),
            patch("src.routes.admin_downtime.count_incidents", return_value=3) as mock_count,
        ):
            response = client.get("/admin/downtime/incidents?status=resolved")

        assert response.json()["ongoing"] == 3
        statuses = [call.kwargs["status"] for call in mock_count.call_args_list]
        assert statuses == ["ongoing", "resolved"]

    def test_rejects_unknown_status(self, admin_override):
        response = client.get("/admin/downtime/incidents?status=exploded")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_status"

    def test_rejects_unknown_severity(self, admin_override):
        response = client.get("/admin/downtime/incidents?severity=apocalyptic")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_severity"

    def test_rejects_out_of_range_limit(self, admin_override):
        assert client.get("/admin/downtime/incidents?limit=5000").status_code == 422

    def test_broken_read_is_503_not_an_empty_list(self, admin_override):
        """An empty incidents table and a failed query must not render the same
        calm, healthy-looking page."""
        with patch(
            "src.routes.admin_downtime.list_incidents",
            side_effect=DowntimeIncidentsUnavailable("PGRST205"),
        ):
            response = client.get("/admin/downtime/incidents")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "downtime_incidents_unavailable"

    def test_empty_table_is_a_200_with_zero_incidents(self, admin_override):
        with (
            patch("src.routes.admin_downtime.list_incidents", return_value=([], 0)),
            patch("src.routes.admin_downtime.count_incidents", return_value=0),
        ):
            response = client.get("/admin/downtime/incidents")

        assert response.status_code == 200
        assert response.json()["incidents"] == []
        assert response.json()["total_incidents"] == 0


class TestStatistics:
    STATS = {
        "total_incidents": 2,
        "total_downtime_seconds": 900,
        "average_duration_seconds": 450,
        "by_severity": {"high": 2},
        "by_status": {"resolved": 2},
    }

    def test_returns_panel_shape(self, admin_override):
        with patch("src.routes.admin_downtime.get_incident_statistics", return_value=self.STATS):
            response = client.get("/admin/downtime/statistics?days=30")

        assert response.status_code == 200
        body = response.json()
        assert body["period_days"] == 30
        assert body["statistics"] == self.STATS

    def test_defaults_to_30_days(self, admin_override):
        with patch(
            "src.routes.admin_downtime.get_incident_statistics", return_value=self.STATS
        ) as mock_stats:
            client.get("/admin/downtime/statistics")

        assert mock_stats.call_args.kwargs["days"] == 30

    def test_broken_read_is_503_not_zeroed_stats(self, admin_override):
        """Zeroed downtime stats read as a perfect month."""
        with patch(
            "src.routes.admin_downtime.get_incident_statistics",
            side_effect=DowntimeIncidentsUnavailable("boom"),
        ):
            response = client.get("/admin/downtime/statistics")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "downtime_incidents_unavailable"

    def test_rejects_absurd_window(self, admin_override):
        assert client.get("/admin/downtime/statistics?days=0").status_code == 422


class TestIncidentDetail:
    def test_returns_incident(self, admin_override):
        with patch("src.routes.admin_downtime.get_incident", return_value=ROW):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}")

        assert response.status_code == 200
        body = response.json()
        assert body["incident"]["id"] == INCIDENT_ID
        assert body["incident"]["resolved_at"] == ROW["ended_at"]

    def test_missing_incident_is_404(self, admin_override):
        with patch("src.routes.admin_downtime.get_incident", return_value=None):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "incident_not_found"

    def test_non_uuid_is_422_not_503(self, admin_override):
        """Handing PostgREST a non-UUID produces a 22P02 that would otherwise
        surface as an outage rather than a bad request."""
        response = client.get("/admin/downtime/incidents/not-a-uuid")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_incident_id"

    def test_broken_read_is_503_not_404(self, admin_override):
        with patch(
            "src.routes.admin_downtime.get_incident",
            side_effect=DowntimeIncidentsUnavailable("boom"),
        ):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}")

        assert response.status_code == 503

    def test_detail_does_not_inline_the_log_payload(self, admin_override):
        row = dict(ROW, logs_captured=[{"level": "ERROR", "message": "x"}])
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}")

        assert "logs_captured" not in response.json()["incident"]


LOGS = [
    {
        "timestamp": "2026-09-01T10:00:01+00:00",
        "level": "ERROR",
        "logger_name": "src.routes.chat",
        "message": "ConnectionError: upstream refused",
    },
    {
        "timestamp": "2026-09-01T10:00:02+00:00",
        "level": "ERROR",
        "logger_name": "src.db.users",
        "message": "ConnectionError: upstream refused",
    },
    {
        "timestamp": "2026-09-01T10:00:03+00:00",
        "level": "WARNING",
        "logger_name": "src.routes.chat",
        "message": "retrying",
    },
    {
        "timestamp": "2026-09-01T10:00:04+00:00",
        "level": "INFO",
        "logger_name": "src.main",
        "message": "recovered",
    },
]


class TestIncidentLogs:
    def test_returns_captured_logs(self, admin_override):
        row = dict(ROW, logs_captured=LOGS)
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")

        body = response.json()
        assert body["total_captured"] == 4
        assert body["total_logs"] == 4
        assert len(body["logs"]) == 4
        assert body["message"] is None

    def test_filters_by_level_logger_and_search(self, admin_override):
        row = dict(ROW, logs_captured=LOGS)
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            by_level = client.get(
                f"/admin/downtime/incidents/{INCIDENT_ID}/logs?level=ERROR"
            ).json()
            by_logger = client.get(
                f"/admin/downtime/incidents/{INCIDENT_ID}/logs?logger_name=src.db"
            ).json()
            by_search = client.get(
                f"/admin/downtime/incidents/{INCIDENT_ID}/logs?search=recovered"
            ).json()

        assert by_level["total_logs"] == 2
        assert by_level["total_captured"] == 4
        assert by_logger["total_logs"] == 1
        assert by_search["total_logs"] == 1
        assert by_search["filters"] == {"level": None, "logger": None, "search": "recovered"}

    def test_no_captured_logs_says_why(self, admin_override):
        """Nothing writes logs_captured in this repo, so the empty state has to
        distinguish "never captured" from "your filters matched nothing"."""
        with patch("src.routes.admin_downtime.get_incident", return_value=dict(ROW)):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")

        body = response.json()
        assert body["logs"] == []
        assert body["total_captured"] == 0
        assert body["message"]

    def test_filters_matching_nothing_carries_no_message(self, admin_override):
        row = dict(ROW, logs_captured=LOGS)
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs?level=DEBUG")

        body = response.json()
        assert body["logs"] == []
        assert body["total_captured"] == 4
        assert body["message"] is None

    def test_tolerates_alternate_and_malformed_entries(self, admin_override):
        """logs_captured is free-form JSONB written by a capture service that
        does not exist yet. Unknown shapes must not 500, and missing fields are
        left empty rather than invented."""
        row = dict(
            ROW,
            logs_captured=[
                {"ts": "2026-09-01T10:00:00Z", "levelname": "ERROR", "logger": "x", "msg": "boom"},
                "not-a-dict",
                {},
            ],
        )
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")

        body = response.json()
        assert response.status_code == 200
        assert body["total_captured"] == 2  # the string is dropped, the {} is kept
        assert body["logs"][0]["level"] == "ERROR"
        assert body["logs"][0]["logger_name"] == "x"
        assert body["logs"][0]["message"] == "boom"
        assert body["logs"][1] == {
            "timestamp": None,
            "level": "",
            "logger_name": "",
            "message": "",
        }

    def test_truncates_huge_payloads_and_says_so(self, admin_override):
        row = dict(ROW, logs_captured=[dict(LOGS[0]) for _ in range(1500)])
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")

        body = response.json()
        assert body["total_logs"] == 1500
        assert len(body["logs"]) == 1000
        assert body["truncated"] is True

    def test_broken_read_is_503(self, admin_override):
        with patch(
            "src.routes.admin_downtime.get_incident",
            side_effect=DowntimeIncidentsUnavailable("boom"),
        ):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/logs")

        assert response.status_code == 503


class TestIncidentAnalysis:
    def test_counts_levels_and_groups_errors(self, admin_override):
        row = dict(ROW, logs_captured=LOGS)
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")

        analysis = response.json()["analysis"]
        assert analysis["total_logs"] == 4
        assert analysis["error_count"] == 2
        assert analysis["warning_count"] == 1
        assert analysis["error_types"] == {"ConnectionError": 2}
        assert analysis["top_errors"] == [["ConnectionError: upstream refused", 2]]

    def test_unrecognised_error_text_is_unclassified_not_guessed(self, admin_override):
        row = dict(ROW, logs_captured=[{"level": "ERROR", "message": "something went sideways"}])
        with patch("src.routes.admin_downtime.get_incident", return_value=row):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")

        assert response.json()["analysis"]["error_types"] == {"Unclassified": 1}

    def test_no_logs_returns_null_analysis_with_a_reason(self, admin_override):
        """Inventing a plausible-looking post-mortem from an empty log array is
        worse than an honest empty state; the panel already renders this."""
        with patch("src.routes.admin_downtime.get_incident", return_value=dict(ROW)):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")

        body = response.json()
        assert response.status_code == 200
        assert body["analysis"] is None
        assert body["message"]

    def test_missing_incident_is_404(self, admin_override):
        with patch("src.routes.admin_downtime.get_incident", return_value=None):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")

        assert response.status_code == 404

    def test_broken_read_is_503(self, admin_override):
        with patch(
            "src.routes.admin_downtime.get_incident",
            side_effect=DowntimeIncidentsUnavailable("boom"),
        ):
            response = client.get(f"/admin/downtime/incidents/{INCIDENT_ID}/analysis")

        assert response.status_code == 503
