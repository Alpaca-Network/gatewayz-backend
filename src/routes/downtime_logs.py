"""
Downtime Logs API Routes

Endpoints for viewing and managing downtime incidents and their associated logs.
Admin-only access for security and operational purposes.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db.downtime_incidents import (
    get_incident,
    get_incident_statistics,
    get_ongoing_incidents,
    get_recent_incidents,
    resolve_incident,
)
from src.security.deps import require_admin
from src.services.downtime_log_capture import (
    analyze_logs_for_errors,
    capture_logs_for_ongoing_incident,
    get_filtered_logs,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/downtime/incidents", tags=["admin", "monitoring"])
async def list_downtime_incidents(
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(None, regex="^(ongoing|resolved|investigating)$"),
    severity: str | None = Query(None, regex="^(low|medium|high|critical)$"),
    environment: str | None = Query(None),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    List downtime incidents (admin only).

    Query parameters:
    - limit: Maximum number of incidents to return (1-500, default: 50)
    - status: Filter by status (ongoing, resolved, investigating)
    - severity: Filter by severity (low, medium, high, critical)
    - environment: Filter by environment (production, staging, etc.)

    Returns:
        List of downtime incidents with metadata
    """
    try:
        # Get incidents
        incidents = get_recent_incidents(
            limit=limit,
            status=status,
            severity=severity,
            environment=environment,
        )

        # Calculate summary statistics
        total_incidents = len(incidents)
        ongoing_count = sum(1 for inc in incidents if inc.get("status") == "ongoing")
        resolved_count = sum(1 for inc in incidents if inc.get("status") == "resolved")

        return {
            "status": "success",
            "total_incidents": total_incidents,
            "ongoing": ongoing_count,
            "resolved": resolved_count,
            "incidents": incidents,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing downtime incidents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/downtime/incidents/ongoing", tags=["admin", "monitoring"])
async def list_ongoing_incidents(
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    List all ongoing downtime incidents (admin only).

    Returns:
        List of ongoing incidents
    """
    try:
        # Get ongoing incidents
        incidents = get_ongoing_incidents()

        return {
            "status": "success",
            "count": len(incidents),
            "incidents": incidents,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing ongoing incidents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/downtime/incidents/{incident_id}", tags=["admin", "monitoring"])
async def get_downtime_incident(
    incident_id: str,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get details of a specific downtime incident (admin only).

    Path parameters:
    - incident_id: UUID of the incident

    Returns:
        Incident details including captured logs
    """
    try:
        # Get incident
        incident = get_incident(incident_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        return {
            "status": "success",
            "incident": incident,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting incident {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/downtime/incidents/{incident_id}/logs", tags=["admin", "monitoring"])
async def get_incident_logs(
    incident_id: str,
    level: str | None = Query(None, regex="^(ERROR|WARNING|INFO|DEBUG)$"),
    logger_name: str | None = None,
    search: str | None = None,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get logs for a specific downtime incident (admin only).

    Path parameters:
    - incident_id: UUID of the incident

    Query parameters:
    - level: Filter by log level (ERROR, WARNING, INFO, DEBUG)
    - logger_name: Filter by logger name (e.g., src.routes.chat)
    - search: Search term in log messages

    Returns:
        Filtered logs for the incident
    """
    try:
        # Get incident
        incident = get_incident(incident_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        # Get logs
        logs = incident.get("logs_captured", [])

        if not logs:
            return {
                "status": "success",
                "message": "No logs captured for this incident",
                "total_logs": 0,
                "logs": [],
            }

        # Apply filters
        filtered_logs = get_filtered_logs(
            logs=logs,
            level=level,
            logger_name=logger_name,
            search_term=search,
        )

        return {
            "status": "success",
            "total_logs": len(filtered_logs),
            "total_captured": len(logs),
            "filters": {
                "level": level,
                "logger": logger_name,
                "search": search,
            },
            "logs": filtered_logs,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting logs for incident {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/downtime/incidents/{incident_id}/analysis", tags=["admin", "monitoring"])
async def analyze_incident_logs(
    incident_id: str,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Analyze logs for a downtime incident (admin only).

    Provides error statistics and patterns.

    Path parameters:
    - incident_id: UUID of the incident

    Returns:
        Log analysis including error counts, top errors, etc.
    """
    try:
        # Get incident
        incident = get_incident(incident_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        # Get logs
        logs = incident.get("logs_captured", [])

        if not logs:
            return {
                "status": "success",
                "message": "No logs to analyze",
                "analysis": None,
            }

        # Analyze logs
        analysis = analyze_logs_for_errors(logs)

        return {
            "status": "success",
            "incident_id": incident_id,
            "analysis": analysis,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing incident {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post(
    "/admin/downtime/incidents/{incident_id}/capture-logs", tags=["admin", "monitoring"]
)
async def trigger_log_capture(
    incident_id: str,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Manually trigger log capture for an ongoing incident (admin only).

    Path parameters:
    - incident_id: UUID of the incident

    Returns:
        Log capture results
    """
    try:
        # Get incident
        incident = get_incident(incident_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        if incident.get("status") != "ongoing":
            raise HTTPException(
                status_code=400,
                detail="Can only capture logs for ongoing incidents",
            )

        # Capture logs
        started_at = datetime.fromisoformat(incident["started_at"])

        result = capture_logs_for_ongoing_incident(
            incident_id=incident_id,
            started_at=started_at,
            save_to_file=False,  # Save to database for manual capture
        )

        return {
            "status": "success",
            "message": "Log capture triggered",
            "result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error capturing logs for incident {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/admin/downtime/incidents/{incident_id}/resolve", tags=["admin", "monitoring"])
async def resolve_downtime_incident(
    incident_id: str,
    notes: str | None = None,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Manually resolve a downtime incident (admin only).

    Path parameters:
    - incident_id: UUID of the incident

    Query parameters:
    - notes: Optional resolution notes

    Returns:
        Updated incident
    """
    try:
        # Get incident
        incident = get_incident(incident_id)

        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")

        if incident.get("status") == "resolved":
            raise HTTPException(
                status_code=400,
                detail="Incident is already resolved",
            )

        # Resolve incident
        resolved_by = f"admin:{admin_user.get('email', admin_user.get('id'))}"

        updated_incident = resolve_incident(
            incident_id=incident_id,
            resolved_by=resolved_by,
            notes=notes,
        )

        return {
            "status": "success",
            "message": "Incident resolved",
            "incident": updated_incident,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving incident {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/downtime/statistics", tags=["admin", "monitoring"])
async def get_downtime_statistics(
    days: int = Query(30, ge=1, le=365),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get downtime statistics (admin only).

    Query parameters:
    - days: Number of days to analyze (1-365, default: 30)

    Returns:
        Statistics including incident counts, total downtime, etc.
    """
    try:
        # Get statistics
        stats = get_incident_statistics(days=days)

        return {
            "status": "success",
            "period_days": days,
            "statistics": stats,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting downtime statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
