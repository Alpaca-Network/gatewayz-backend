"""Response schemas for the admin downtime API (src/routes/admin_downtime.py).

The shapes here are not free inventions: they are the contract the admin panel
already ships against -- `admin-panel/src/types/downtime.ts` plus the proxy
routes under `admin-panel/src/app/api/proxy/admin/downtime/`. Changing a field
name here breaks a page that is already deployed.

Two boundary rules this module exists to enforce:

1. `resolved_at` is the panel's name for the `downtime_incidents.ended_at`
   column. There is no `resolved_at` column -- selecting one is the phantom
   column bug class. The mapping happens in the route, once.
2. Every column that is nullable in Postgres is Optional here. Supabase returns
   every column explicitly, including `None`, so a non-Optional field with a
   default does not rescue a NULL -- the default only fires when the key is
   absent. Per the live prod schema only id/started_at/detected_at/
   health_endpoint/status are NOT NULL; severity and environment are not.
"""

from __future__ import annotations

from pydantic import BaseModel


class IncidentSummary(BaseModel):
    """One incident as rendered in the list and detail headers."""

    id: str
    status: str
    severity: str | None = None
    environment: str | None = None
    started_at: str
    detected_at: str | None = None
    resolved_at: str | None = None  # <- downtime_incidents.ended_at
    resolved_by: str | None = None
    duration_seconds: int | None = None
    health_endpoint: str | None = None
    error_message: str | None = None
    http_status_code: int | None = None
    log_count: int | None = None
    notes: str | None = None


class IncidentsListResponse(BaseModel):
    status: str = "success"
    total_incidents: int
    ongoing: int
    resolved: int
    limit: int
    offset: int
    incidents: list[IncidentSummary]


class IncidentDetailResponse(BaseModel):
    status: str = "success"
    incident: IncidentSummary


class LogEntry(BaseModel):
    timestamp: str | None = None
    level: str = ""
    logger_name: str = ""
    message: str = ""


class LogFilters(BaseModel):
    level: str | None = None
    logger: str | None = None
    search: str | None = None


class IncidentLogsResponse(BaseModel):
    status: str = "success"
    incident_id: str
    total_captured: int
    total_logs: int
    filters: LogFilters
    logs: list[LogEntry]
    truncated: bool = False
    # Set only when there is nothing to show, to say *why* rather than leaving
    # an empty table that could equally mean "the query broke".
    message: str | None = None


class LogAnalysis(BaseModel):
    total_logs: int
    error_count: int
    warning_count: int
    error_types: dict[str, int]
    top_errors: list[tuple[str, int]]


class IncidentAnalysisResponse(BaseModel):
    status: str = "success"
    incident_id: str
    analysis: LogAnalysis | None = None
    message: str | None = None


class DowntimeStatistics(BaseModel):
    total_incidents: int
    total_downtime_seconds: int
    average_duration_seconds: int
    by_severity: dict[str, int]
    by_status: dict[str, int]


class DowntimeStatisticsResponse(BaseModel):
    status: str = "success"
    period_days: int
    statistics: DowntimeStatistics
