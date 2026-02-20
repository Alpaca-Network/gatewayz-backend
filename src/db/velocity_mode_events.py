"""
Database operations for velocity mode events tracking

This module handles CRUD operations for the velocity_mode_events table,
which tracks when and why velocity mode protection is activated.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def create_velocity_event(
    error_rate: float,
    total_requests: int,
    error_count: int,
    error_details: dict[str, int] | None = None,
    trigger_reason: str = "error_threshold_exceeded",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Create a new velocity mode activation event.

    Args:
        error_rate: Error rate that triggered velocity mode (e.g., 0.25 for 25%)
        total_requests: Total requests in the window
        error_count: Number of errors in the window
        error_details: Breakdown of errors by status code (e.g., {"499": 45, "500": 12})
        trigger_reason: Reason for activation
        metadata: Additional context data

    Returns:
        Created event record or None if failed
    """
    try:
        supabase = get_supabase_client()

        event_data = {
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "error_rate": float(error_rate),
            "total_requests": total_requests,
            "error_count": error_count,
            "error_details": error_details or {},
            "trigger_reason": trigger_reason,
            "metadata": metadata or {},
        }

        result = supabase.table("velocity_mode_events").insert(event_data).execute()

        if result.data:
            logger.info(
                f"Created velocity mode event: {result.data[0]['id']} "
                f"(error_rate={error_rate:.2%}, errors={error_count}/{total_requests})"
            )
            return result.data[0]
        else:
            logger.error("Failed to create velocity mode event: No data returned")
            return None

    except Exception as e:
        logger.error(f"Error creating velocity mode event: {e}")
        return None


def deactivate_velocity_event(event_id: str | UUID) -> dict[str, Any] | None:
    """
    Mark a velocity mode event as deactivated.

    Args:
        event_id: UUID of the event to deactivate

    Returns:
        Updated event record or None if failed
    """
    try:
        supabase = get_supabase_client()

        # Convert UUID to string if necessary
        event_id_str = str(event_id) if isinstance(event_id, UUID) else event_id

        now = datetime.now(timezone.utc)

        # First, get the event to calculate duration
        event_result = supabase.table("velocity_mode_events").select("*").eq("id", event_id_str).execute()

        if not event_result.data:
            logger.warning(f"Velocity mode event not found: {event_id_str}")
            return None

        event = event_result.data[0]
        activated_at = datetime.fromisoformat(event["activated_at"].replace("Z", "+00:00"))
        duration = int((now - activated_at).total_seconds())

        # Update the event
        update_data = {
            "deactivated_at": now.isoformat(),
            "duration_seconds": duration,
            "updated_at": now.isoformat(),
        }

        result = (
            supabase.table("velocity_mode_events").update(update_data).eq("id", event_id_str).execute()
        )

        if result.data:
            logger.info(f"Deactivated velocity mode event: {event_id_str} (duration={duration}s)")
            return result.data[0]
        else:
            logger.error(f"Failed to deactivate velocity mode event: {event_id_str}")
            return None

    except Exception as e:
        logger.error(f"Error deactivating velocity mode event {event_id}: {e}")
        return None


def get_active_velocity_event() -> dict[str, Any] | None:
    """
    Get the currently active velocity mode event (if any).

    Returns:
        Active event record or None if no active event
    """
    try:
        supabase = get_supabase_client()

        result = (
            supabase.table("velocity_mode_events")
            .select("*")
            .is_("deactivated_at", "null")
            .order("activated_at", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting active velocity mode event: {e}")
        return None


def get_recent_velocity_events(limit: int = 50) -> list[dict[str, Any]]:
    """
    Get recent velocity mode events.

    Args:
        limit: Maximum number of events to return

    Returns:
        List of event records
    """
    try:
        supabase = get_supabase_client()

        result = (
            supabase.table("velocity_mode_events")
            .select("*")
            .order("activated_at", desc=True)
            .limit(limit)
            .execute()
        )

        return result.data if result.data else []

    except Exception as e:
        logger.error(f"Error getting recent velocity mode events: {e}")
        return []


def get_velocity_event_by_id(event_id: str | UUID) -> dict[str, Any] | None:
    """
    Get a specific velocity mode event by ID.

    Args:
        event_id: UUID of the event

    Returns:
        Event record or None if not found
    """
    try:
        supabase = get_supabase_client()

        event_id_str = str(event_id) if isinstance(event_id, UUID) else event_id

        result = supabase.table("velocity_mode_events").select("*").eq("id", event_id_str).execute()

        if result.data:
            return result.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting velocity mode event {event_id}: {e}")
        return None


def get_velocity_event_stats(hours: int = 24) -> dict[str, Any]:
    """
    Get statistics about velocity mode events in the last N hours.

    Args:
        hours: Number of hours to look back

    Returns:
        Dictionary with statistics
    """
    try:
        supabase = get_supabase_client()

        # Calculate cutoff time
        cutoff = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        # Get events in the time window
        result = (
            supabase.table("velocity_mode_events")
            .select("*")
            .gte("activated_at", f"now() - interval '{hours} hours'")
            .execute()
        )

        events = result.data if result.data else []

        if not events:
            return {
                "total_activations": 0,
                "avg_error_rate": 0.0,
                "avg_duration_seconds": 0,
                "total_time_in_velocity_mode": 0,
            }

        total_activations = len(events)
        avg_error_rate = sum(float(e["error_rate"]) for e in events) / total_activations

        # Calculate average duration (only for deactivated events)
        deactivated = [e for e in events if e["duration_seconds"] is not None]
        avg_duration = (
            sum(e["duration_seconds"] for e in deactivated) / len(deactivated)
            if deactivated
            else 0
        )

        total_time = sum(e["duration_seconds"] or 0 for e in events)

        return {
            "total_activations": total_activations,
            "avg_error_rate": round(avg_error_rate, 4),
            "avg_duration_seconds": round(avg_duration, 1),
            "total_time_in_velocity_mode": total_time,
            "time_window_hours": hours,
        }

    except Exception as e:
        logger.error(f"Error getting velocity mode event stats: {e}")
        return {
            "total_activations": 0,
            "avg_error_rate": 0.0,
            "avg_duration_seconds": 0,
            "total_time_in_velocity_mode": 0,
            "error": str(e),
        }
