"""Admin downtime-incident API.

Exposes the pre-existing data layer in `src/db/downtime_incidents.py`, which
had no routes at all -- the admin panel's Downtime & Logs page and its proxy
routes have been calling these paths and getting 404s
(see the vault note "Phantom Column Failures, Admin Dashboard Batch 1").

The URL paths, query params and response field names are fixed by the deployed
panel (`admin-panel/src/app/api/proxy/admin/downtime/**` and
`src/types/downtime.ts`); this module matches them rather than inventing a new
contract.

Two things worth knowing before editing:

* `resolved_at` in the response is the `ended_at` column. There is no
  `resolved_at` column on `downtime_incidents` -- the mapping is done here, in
  `_to_summary`, and nowhere else.
* `/logs` and `/analysis` are derived entirely from the `logs_captured` JSONB
  column. Nothing in this repo writes that column today: the log-capture
  service and health monitor that `docs/DOWNTIME_MONITORING.md` describes
  (`src/services/downtime_log_capture.py`, `scripts/monitoring/health_monitor.py`)
  do not exist. So both endpoints report an explicit, honest "no logs were
  captured" rather than synthesising a plausible-looking post-mortem. If the
  capture service is ever built, these endpoints start returning real data with
  no change here.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db.downtime_incidents import (
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
    DowntimeIncidentsUnavailable,
    count_incidents,
    get_incident,
    get_incident_statistics,
    list_incidents,
)
from src.schemas.downtime import (
    DowntimeStatisticsResponse,
    IncidentAnalysisResponse,
    IncidentDetailResponse,
    IncidentLogsResponse,
    IncidentsListResponse,
    IncidentSummary,
    LogAnalysis,
    LogEntry,
    LogFilters,
)
from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# A log payload big enough to blow the panel's 15s proxy timeout is worse than
# a truncated one; the response flags truncation rather than hiding it.
MAX_LOGS_RETURNED = 1000

MAX_TOP_ERRORS = 10

# Pulls an exception class name out of a log line ("...raised ConnectionError:
# timed out" -> "ConnectionError"). Purely mechanical: anything that does not
# match is bucketed as "Unclassified" rather than guessed at.
_ERROR_TYPE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout))\b")

_NO_CAPTURE_MESSAGE = (
    "No logs were captured for this incident. The downtime log-capture service "
    "described in docs/DOWNTIME_MONITORING.md is not implemented, so "
    "downtime_incidents.logs_captured is empty. This is an absence of data, not "
    "a failed query."
)


def _unavailable(error: Exception) -> HTTPException:
    """503 for a read that failed.

    Deliberately not an empty 200: a broken query and "there have been no
    incidents" must not render as the same calm, healthy-looking page. Same
    reasoning as `staff_roster_unavailable` in src/routes/admin_staff.py.
    """
    logger.error("Downtime incidents read failed: %s", error, exc_info=True)
    return HTTPException(
        status_code=503,
        detail={
            "error": {
                "message": "Downtime incidents are temporarily unavailable.",
                "type": "service_unavailable",
                "code": "downtime_incidents_unavailable",
            }
        },
    )


def _invalid_enum(name: str, value: str, allowed: tuple[str, ...]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": {
                "message": f"{name} must be one of {list(allowed)}.",
                "type": "invalid_request_error",
                "code": f"invalid_{name}",
                "context": {"parameter_value": value},
            }
        },
    )


def _not_found(incident_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "message": "Incident not found.",
                "type": "invalid_request_error",
                "code": "incident_not_found",
                "context": {"parameter_value": incident_id},
            }
        },
    )


def _parse_incident_id(incident_id: str) -> str:
    """The id column is a UUID; handing PostgREST a non-UUID produces a 22P02
    that would otherwise surface as a 503 outage rather than a bad request."""
    try:
        return str(UUID(incident_id))
    except (ValueError, AttributeError, TypeError) as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "message": "incident_id must be a UUID.",
                    "type": "invalid_request_error",
                    "code": "invalid_incident_id",
                    "context": {"parameter_value": incident_id},
                }
            },
        ) from e


def _to_summary(row: dict[str, Any]) -> IncidentSummary:
    """Map a downtime_incidents row onto the panel's incident shape.

    `ended_at` -> `resolved_at` is the only rename; everything else is a
    straight pass-through of a column that exists on the live table.
    """
    return IncidentSummary(
        id=str(row.get("id")),
        status=row.get("status") or "unknown",
        severity=row.get("severity"),
        environment=row.get("environment"),
        started_at=row.get("started_at"),
        detected_at=row.get("detected_at"),
        resolved_at=row.get("ended_at"),
        resolved_by=row.get("resolved_by"),
        duration_seconds=row.get("duration_seconds"),
        health_endpoint=row.get("health_endpoint"),
        error_message=row.get("error_message"),
        http_status_code=row.get("http_status_code"),
        log_count=row.get("log_count"),
        notes=row.get("notes"),
    )


def _normalize_log(entry: Any) -> LogEntry | None:
    """Coerce one captured log entry into the panel's LogEntry shape.

    `logs_captured` is free-form JSONB written by a capture service that does
    not exist yet, so key names are not guaranteed. Missing fields stay empty
    rather than being filled in with a plausible value.
    """
    if not isinstance(entry, dict):
        return None

    timestamp = entry.get("timestamp") or entry.get("ts") or entry.get("time")
    level = entry.get("level") or entry.get("levelname") or ""
    logger_name = entry.get("logger_name") or entry.get("logger") or entry.get("name") or ""
    message = entry.get("message") or entry.get("msg") or ""

    return LogEntry(
        timestamp=str(timestamp) if timestamp is not None else None,
        level=str(level),
        logger_name=str(logger_name),
        message=str(message),
    )


def _captured_logs(incident: dict[str, Any]) -> list[LogEntry]:
    raw = incident.get("logs_captured")
    if not isinstance(raw, list):
        return []
    return [log for log in (_normalize_log(entry) for entry in raw) if log is not None]


def _filter_logs(
    logs: list[LogEntry],
    level: str | None,
    logger_name: str | None,
    search: str | None,
) -> list[LogEntry]:
    result = logs
    if level:
        wanted = level.upper()
        result = [log for log in result if log.level.upper() == wanted]
    if logger_name:
        needle = logger_name.lower()
        result = [log for log in result if needle in log.logger_name.lower()]
    if search:
        needle = search.lower()
        result = [log for log in result if needle in log.message.lower()]
    return result


def _analyze(logs: list[LogEntry]) -> LogAnalysis:
    """Count levels and group error messages. Derivation only -- every number
    here is a tally of lines that are actually in `logs_captured`."""
    error_logs = [log for log in logs if log.level.upper() == "ERROR"]
    warning_logs = [log for log in logs if log.level.upper() in ("WARNING", "WARN")]

    error_types: Counter[str] = Counter()
    for log in error_logs:
        match = _ERROR_TYPE_RE.search(log.message)
        error_types[match.group(1) if match else "Unclassified"] += 1

    top_errors = Counter(log.message for log in error_logs).most_common(MAX_TOP_ERRORS)

    return LogAnalysis(
        total_logs=len(logs),
        error_count=len(error_logs),
        warning_count=len(warning_logs),
        error_types=dict(error_types),
        top_errors=[(message, count) for message, count in top_errors],
    )


def _load_incident_or_404(incident_id: str) -> dict[str, Any]:
    parsed = _parse_incident_id(incident_id)
    try:
        incident = get_incident(parsed)
    except DowntimeIncidentsUnavailable as e:
        raise _unavailable(e) from e
    if incident is None:
        raise _not_found(incident_id)
    return incident


@router.get(
    "/admin/downtime/incidents",
    response_model=IncidentsListResponse,
    tags=["admin", "downtime"],
)
async def list_downtime_incidents(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    severity: str | None = Query(None),
    environment: str | None = Query(None),
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> IncidentsListResponse:
    """One page of incidents, newest first, plus the totals the panel's
    pagination and stat cards need.

    `total_incidents` counts the rows matching *all* the filters (it drives the
    page count). `ongoing` and `resolved` ignore the status filter but honour
    severity/environment, so the "Ongoing" card does not drop to zero the
    moment someone filters the table to resolved incidents.
    """
    if status is not None and status not in INCIDENT_STATUSES:
        raise _invalid_enum("status", status, INCIDENT_STATUSES)
    if severity is not None and severity not in INCIDENT_SEVERITIES:
        raise _invalid_enum("severity", severity, INCIDENT_SEVERITIES)

    try:
        rows, total = list_incidents(
            limit=limit,
            offset=offset,
            status=status,
            severity=severity,
            environment=environment,
        )
        ongoing = count_incidents(status="ongoing", severity=severity, environment=environment)
        resolved = count_incidents(status="resolved", severity=severity, environment=environment)
    except DowntimeIncidentsUnavailable as e:
        raise _unavailable(e) from e

    return IncidentsListResponse(
        total_incidents=total,
        ongoing=ongoing,
        resolved=resolved,
        limit=limit,
        offset=offset,
        incidents=[_to_summary(row) for row in rows],
    )


@router.get(
    "/admin/downtime/statistics",
    response_model=DowntimeStatisticsResponse,
    tags=["admin", "downtime"],
)
async def downtime_statistics(
    days: int = Query(30, ge=1, le=365),
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> DowntimeStatisticsResponse:
    """Aggregate downtime over the last `days` days."""
    try:
        stats = get_incident_statistics(days=days)
    except DowntimeIncidentsUnavailable as e:
        raise _unavailable(e) from e

    return DowntimeStatisticsResponse(period_days=days, statistics=stats)


@router.get(
    "/admin/downtime/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
    tags=["admin", "downtime"],
)
async def get_downtime_incident(
    incident_id: str,
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> IncidentDetailResponse:
    """One incident. `logs_captured` is deliberately not inlined here -- it can
    be large, and the panel fetches it from /logs with filters applied."""
    incident = _load_incident_or_404(incident_id)
    return IncidentDetailResponse(incident=_to_summary(incident))


@router.get(
    "/admin/downtime/incidents/{incident_id}/logs",
    response_model=IncidentLogsResponse,
    tags=["admin", "downtime"],
)
async def get_downtime_incident_logs(
    incident_id: str,
    level: str | None = Query(None),
    logger_name: str | None = Query(None),
    search: str | None = Query(None),
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> IncidentLogsResponse:
    """The incident's captured logs, filtered.

    Backed by the real `logs_captured` column. Nothing writes that column in
    this repo today, so in practice this returns an empty list with `message`
    explaining why -- which is the point: the panel can tell "no logs were ever
    captured" apart from "the query failed" (503) and from "your filters match
    nothing" (empty list, no message).
    """
    incident = _load_incident_or_404(incident_id)

    all_logs = _captured_logs(incident)
    filtered = _filter_logs(all_logs, level, logger_name, search)
    page = filtered[:MAX_LOGS_RETURNED]

    return IncidentLogsResponse(
        incident_id=str(incident.get("id")),
        total_captured=len(all_logs),
        total_logs=len(filtered),
        filters=LogFilters(level=level, logger=logger_name, search=search),
        logs=page,
        truncated=len(filtered) > len(page),
        message=_NO_CAPTURE_MESSAGE if not all_logs else None,
    )


@router.get(
    "/admin/downtime/incidents/{incident_id}/analysis",
    response_model=IncidentAnalysisResponse,
    tags=["admin", "downtime"],
)
async def get_downtime_incident_analysis(
    incident_id: str,
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> IncidentAnalysisResponse:
    """Level counts and error grouping over the incident's captured logs.

    This is a tally, not a diagnosis. With no captured logs there is nothing to
    tally, so it returns `analysis: null` plus a message -- the panel already
    renders that as "No logs captured for this incident." Producing a
    plausible-looking root-cause summary from an empty log array would be worse
    than an empty state.
    """
    incident = _load_incident_or_404(incident_id)

    logs = _captured_logs(incident)
    if not logs:
        return IncidentAnalysisResponse(
            incident_id=str(incident.get("id")),
            analysis=None,
            message=_NO_CAPTURE_MESSAGE,
        )

    return IncidentAnalysisResponse(
        incident_id=str(incident.get("id")),
        analysis=_analyze(logs),
    )
