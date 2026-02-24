"""
Analytics API Routes
Server-side endpoint for logging analytics events to Statsig and PostHog
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.security.deps import get_current_user
from src.services.posthog_service import posthog_service
from src.services.statsig_service import statsig_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


class AnalyticsEvent(BaseModel):
    """Analytics event model"""

    event_name: str = Field(..., description="Event name (e.g., 'chat_message_sent')")
    user_id: str | None = Field(
        None, description="User ID (optional, will use authenticated user if not provided)"
    )
    value: str | None = Field(None, description="Optional event value")
    metadata: dict[str, Any] | None = Field(None, description="Optional event metadata")


class SessionStartEvent(BaseModel):
    """Session start event model for DAU/WAU/MAU tracking"""

    platform: str = Field(
        default="web",
        description="Platform identifier (web, ios, android, desktop)",
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Optional session metadata (version, referrer, etc.)"
    )


@router.post("/events")
async def log_event(event: AnalyticsEvent, current_user: dict | None = Depends(get_current_user)):
    """
    Log an analytics event to both Statsig and PostHog via backend

    This endpoint allows the frontend to send analytics events to the backend,
    which then forwards them to both analytics platforms. This avoids ad-blocker issues.

    Args:
        event: The analytics event to log
        current_user: Authenticated user (from auth middleware)

    Returns:
        Success message
    """
    try:
        # Determine user ID (prefer authenticated user, fallback to provided user_id or 'anonymous')
        user_id = "anonymous"

        if current_user:
            user_id = str(current_user.get("user_id", "anonymous"))
        elif event.user_id:
            user_id = event.user_id

        # Log event to Statsig
        statsig_service.log_event(
            user_id=user_id, event_name=event.event_name, value=event.value, metadata=event.metadata
        )

        # Log event to PostHog
        posthog_service.capture(
            distinct_id=user_id, event=event.event_name, properties=event.metadata
        )

        return {"success": True, "message": f"Event '{event.event_name}' logged successfully"}

    except Exception as e:
        logger.error(f"Failed to log analytics event: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to log analytics event: {str(e)}"
        ) from e


@router.post("/batch")
async def log_batch_events(
    events: list[AnalyticsEvent], current_user: dict | None = Depends(get_current_user)
):
    """
    Log multiple analytics events in batch to both Statsig and PostHog

    Args:
        events: List of analytics events to log
        current_user: Authenticated user (from auth middleware)

    Returns:
        Success message with count
    """
    try:
        # Determine user ID
        user_id = "anonymous"
        if current_user:
            user_id = str(current_user.get("user_id", "anonymous"))

        # Log each event to both platforms
        for event in events:
            event_user_id = event.user_id or user_id

            # Log to Statsig
            statsig_service.log_event(
                user_id=event_user_id,
                event_name=event.event_name,
                value=event.value,
                metadata=event.metadata,
            )

            # Log to PostHog
            posthog_service.capture(
                distinct_id=event_user_id, event=event.event_name, properties=event.metadata
            )

        return {"success": True, "message": f"{len(events)} events logged successfully"}

    except Exception as e:
        logger.error(f"Failed to log batch analytics events: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to log batch analytics events: {str(e)}"
        ) from e


@router.post("/session/start")
async def log_session_start(
    session: SessionStartEvent, current_user: dict | None = Depends(get_current_user)
):
    """
    Log a session start event for DAU/WAU/MAU tracking.

    This endpoint should be called when:
    - User opens the app/website
    - User logs in
    - User returns after being idle

    The session_start event is used by Statsig to compute Product Growth metrics
    including Daily Active Users (DAU), Weekly Active Users (WAU),
    Monthly Active Users (MAU), stickiness, and retention rates.

    Args:
        session: Session start event with platform and optional metadata
        current_user: Authenticated user (from auth middleware)

    Returns:
        Success message
    """
    try:
        # Determine user ID
        user_id = "anonymous"
        if current_user:
            user_id = str(current_user.get("user_id", "anonymous"))

        # Log session start to Statsig (for DAU/WAU/MAU)
        statsig_service.log_session_start(
            user_id=user_id,
            platform=session.platform,
            metadata=session.metadata,
        )

        # Log session start to PostHog
        posthog_service.capture(
            distinct_id=user_id,
            event="session_start",
            properties={"platform": session.platform, **(session.metadata or {})},
        )

        logger.debug(f"Session start logged for user {user_id} on {session.platform}")
        return {"success": True, "message": "Session start logged successfully"}

    except Exception as e:
        logger.error(f"Failed to log session start: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to log session start: {str(e)}") from e
