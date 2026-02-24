#!/usr/bin/env python3
"""
Downtime Incidents Database Module
Handles storage and retrieval of application downtime incidents with associated logs
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.config.supabase_config import execute_with_retry

logger = logging.getLogger(__name__)

_missing_table_warning_logged = False


def _maybe_log_missing_table_hint(error: Exception) -> None:
    """
    Emit a single actionable warning when the downtime_incidents table
    is missing from the Supabase schema cache so operators know to run migrations.
    """
    global _missing_table_warning_logged

    if _missing_table_warning_logged:
        return

    message = str(error)
    if "downtime_incidents" in message or "PGRST205" in message:
        logger.warning(
            "downtime_incidents table is unavailable in Supabase (likely migrations not applied "
            "or schema cache stale). Apply migration "
            "20260212000000_create_downtime_incidents_table.sql, then run "
            "NOTIFY pgrst, 'reload schema'; to refresh PostgREST."
        )
        _missing_table_warning_logged = True


def create_incident(
    started_at: datetime,
    health_endpoint: str = "/health",
    error_message: str | None = None,
    http_status_code: int | None = None,
    response_body: str | None = None,
    severity: str = "high",
    environment: str = "production",
    server_info: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Create a new downtime incident record

    Args:
        started_at: When the downtime started
        health_endpoint: The health check endpoint that failed
        error_message: Error message from the health check
        http_status_code: HTTP status code received (if any)
        response_body: Response body from failed health check
        severity: Incident severity (low, medium, high, critical)
        environment: Environment where incident occurred
        server_info: Additional server metadata

    Returns:
        Created incident record or None on failure
    """
    try:

        def _create_incident(client):
            return (
                client.table("downtime_incidents")
                .insert(
                    {
                        "started_at": started_at.isoformat(),
                        "detected_at": datetime.now(UTC).isoformat(),
                        "health_endpoint": health_endpoint,
                        "error_message": error_message,
                        "http_status_code": http_status_code,
                        "response_body": response_body,
                        "status": "ongoing",
                        "severity": severity,
                        "environment": environment,
                        "server_info": server_info or {},
                    }
                )
                .execute()
            )

        result = execute_with_retry(_create_incident, max_retries=2, retry_delay=0.2)

        if result.data:
            incident = result.data[0]
            logger.warning(
                f"Created downtime incident {incident['id']} - "
                f"Started: {started_at}, Severity: {severity}"
            )
            return incident
        else:
            logger.error("Failed to create downtime incident")
            return None

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error creating downtime incident: {e}", exc_info=True)
        return None


def update_incident(
    incident_id: str | UUID,
    ended_at: datetime | None = None,
    logs_captured: list[dict[str, Any]] | None = None,
    logs_file_path: str | None = None,
    status: str | None = None,
    resolved_by: str | None = None,
    notes: str | None = None,
    metrics_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Update an existing downtime incident

    Args:
        incident_id: UUID of the incident
        ended_at: When the downtime ended (None if still ongoing)
        logs_captured: Array of log entries
        logs_file_path: Path to log file if stored externally
        status: New status (ongoing, resolved, investigating)
        resolved_by: Who/what resolved the incident
        notes: Additional notes
        metrics_snapshot: Prometheus metrics at time of failure

    Returns:
        Updated incident record or None on failure
    """
    try:
        update_data: dict[str, Any] = {}

        if ended_at is not None:
            update_data["ended_at"] = ended_at.isoformat()
        if logs_captured is not None:
            update_data["logs_captured"] = logs_captured
            update_data["log_count"] = len(logs_captured)
        if logs_file_path is not None:
            update_data["logs_file_path"] = logs_file_path
        if status is not None:
            update_data["status"] = status
        if resolved_by is not None:
            update_data["resolved_by"] = resolved_by
        if notes is not None:
            update_data["notes"] = notes
        if metrics_snapshot is not None:
            update_data["metrics_snapshot"] = metrics_snapshot

        if not update_data:
            logger.warning("No data provided to update incident")
            return None

        def _update_incident(client):
            return (
                client.table("downtime_incidents")
                .update(update_data)
                .eq("id", str(incident_id))
                .execute()
            )

        result = execute_with_retry(_update_incident, max_retries=2, retry_delay=0.2)

        if result.data:
            incident = result.data[0]
            logger.info(f"Updated downtime incident {incident_id}: {list(update_data.keys())}")
            return incident
        else:
            logger.error(f"Failed to update downtime incident {incident_id}")
            return None

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error updating downtime incident: {e}", exc_info=True)
        return None


def get_incident(incident_id: str | UUID) -> dict[str, Any] | None:
    """
    Get details of a specific downtime incident

    Args:
        incident_id: UUID of the incident

    Returns:
        Incident details if found, None otherwise
    """
    try:

        def _get_incident(client):
            return (
                client.table("downtime_incidents").select("*").eq("id", str(incident_id)).execute()
            )

        result = execute_with_retry(_get_incident, max_retries=2, retry_delay=0.2)

        if result.data:
            return result.data[0]
        return None

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error getting downtime incident: {e}", exc_info=True)
        return None


def get_ongoing_incidents() -> list[dict[str, Any]]:
    """
    Get all ongoing downtime incidents

    Returns:
        List of ongoing incidents
    """
    try:

        def _get_ongoing(client):
            return (
                client.table("downtime_incidents")
                .select("*")
                .eq("status", "ongoing")
                .order("started_at", desc=True)
                .execute()
            )

        result = execute_with_retry(_get_ongoing, max_retries=2, retry_delay=0.2)

        return result.data if result.data else []

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error getting ongoing incidents: {e}", exc_info=True)
        return []


def get_recent_incidents(
    limit: int = 50,
    status: str | None = None,
    severity: str | None = None,
    environment: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get recent downtime incidents with optional filtering

    Args:
        limit: Maximum number of incidents to return
        status: Filter by status (ongoing, resolved, investigating)
        severity: Filter by severity (low, medium, high, critical)
        environment: Filter by environment

    Returns:
        List of incidents
    """
    try:

        def _get_recent(client):
            query = client.table("downtime_incidents").select("*")

            if status:
                query = query.eq("status", status)
            if severity:
                query = query.eq("severity", severity)
            if environment:
                query = query.eq("environment", environment)

            return query.order("started_at", desc=True).limit(limit).execute()

        result = execute_with_retry(_get_recent, max_retries=2, retry_delay=0.2)

        return result.data if result.data else []

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error getting recent incidents: {e}", exc_info=True)
        return []


def get_incidents_by_date_range(start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
    """
    Get incidents within a specific date range

    Args:
        start_date: Start of date range
        end_date: End of date range

    Returns:
        List of incidents in the date range
    """
    try:

        def _get_by_range(client):
            return (
                client.table("downtime_incidents")
                .select("*")
                .gte("started_at", start_date.isoformat())
                .lte("started_at", end_date.isoformat())
                .order("started_at", desc=True)
                .execute()
            )

        result = execute_with_retry(_get_by_range, max_retries=2, retry_delay=0.2)

        return result.data if result.data else []

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error getting incidents by date range: {e}", exc_info=True)
        return []


def resolve_incident(
    incident_id: str | UUID, resolved_by: str = "auto", notes: str | None = None
) -> dict[str, Any] | None:
    """
    Mark an incident as resolved

    Args:
        incident_id: UUID of the incident
        resolved_by: Who/what resolved it
        notes: Additional resolution notes

    Returns:
        Updated incident or None on failure
    """
    return update_incident(
        incident_id=incident_id,
        ended_at=datetime.now(UTC),
        status="resolved",
        resolved_by=resolved_by,
        notes=notes,
    )


def get_incident_statistics(days: int = 30) -> dict[str, Any]:
    """
    Get statistics about downtime incidents

    Args:
        days: Number of days to analyze

    Returns:
        Dictionary with incident statistics
    """
    try:
        cutoff = datetime.now(UTC).timestamp() - (days * 24 * 60 * 60)
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=UTC)

        incidents = get_incidents_by_date_range(cutoff_dt, datetime.now(UTC))

        if not incidents:
            return {
                "total_incidents": 0,
                "total_downtime_seconds": 0,
                "average_duration_seconds": 0,
                "by_severity": {},
                "by_status": {},
            }

        total_downtime = sum(
            inc.get("duration_seconds", 0) for inc in incidents if inc.get("duration_seconds")
        )

        severity_counts = {}
        status_counts = {}

        for inc in incidents:
            sev = inc.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            stat = inc.get("status", "unknown")
            status_counts[stat] = status_counts.get(stat, 0) + 1

        return {
            "total_incidents": len(incidents),
            "total_downtime_seconds": total_downtime,
            "average_duration_seconds": (total_downtime // len(incidents) if incidents else 0),
            "by_severity": severity_counts,
            "by_status": status_counts,
        }

    except Exception as e:
        logger.error(f"Error getting incident statistics: {e}", exc_info=True)
        return {
            "total_incidents": 0,
            "total_downtime_seconds": 0,
            "average_duration_seconds": 0,
            "by_severity": {},
            "by_status": {},
        }


def cleanup_old_incidents(days: int = 90, keep_critical: bool = True) -> int:
    """
    Clean up old downtime incidents

    Args:
        days: Number of days to keep incidents (default 90)
        keep_critical: If True, never delete critical incidents

    Returns:
        Number of incidents deleted
    """
    try:
        cutoff = datetime.now(UTC).timestamp() - (days * 24 * 60 * 60)
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()

        def _cleanup_incidents(client):
            query = client.table("downtime_incidents").delete().lt("started_at", cutoff_dt)

            if keep_critical:
                query = query.neq("severity", "critical")

            return query.execute()

        result = execute_with_retry(_cleanup_incidents, max_retries=2, retry_delay=0.2)

        count = len(result.data) if result.data else 0
        logger.info(
            f"Cleaned up {count} old downtime incidents "
            f"(older than {days} days, keep_critical={keep_critical})"
        )

        return count

    except Exception as e:
        _maybe_log_missing_table_hint(e)
        logger.error(f"Error cleaning up old incidents: {e}", exc_info=True)
        return 0
