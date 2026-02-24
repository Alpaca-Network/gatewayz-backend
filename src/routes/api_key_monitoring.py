"""
API Key Monitoring Endpoints
Provides monitoring and alerting for API key tracking quality.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from src.config.supabase_config import get_supabase_client
from src.security.deps import get_admin_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/monitoring", tags=["Admin", "Monitoring"])


@router.get("/api-key-tracking-quality")
async def get_api_key_tracking_quality(
    hours: int = Query(24, ge=1, le=168, description="Time window in hours (default: 24)"),
    _: str = Depends(get_admin_key),
) -> dict[str, Any]:
    """
    Get API key tracking quality metrics.

    Provides insights into how well we're tracking API keys in chat completion requests,
    including percentage of NULL api_key_id values and breakdown by scenario.

    **Admin authentication required.**

    Args:
        hours: Time window to analyze (1-168 hours, default: 24)

    Returns:
        Dictionary containing:
        - total_requests: Total chat completion requests in time window
        - requests_with_api_key: Requests with valid api_key_id
        - requests_without_api_key: Requests with NULL api_key_id
        - tracking_rate: Percentage of requests with api_key_id (0-100)
        - breakdown: Detailed breakdown by user_id presence
        - time_window: Analysis time window info
        - alert_status: Health status (ok, warning, critical)
        - recommendations: List of actionable recommendations
    """
    try:
        client = get_supabase_client()

        # Calculate time window
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(hours=hours)

        # Get total requests in time window
        total_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .gte("created_at", start_time.isoformat())
            .lte("created_at", end_time.isoformat())
            .execute()
        )
        total_requests = total_result.count if hasattr(total_result, "count") else 0

        # Get requests with api_key_id
        with_key_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .gte("created_at", start_time.isoformat())
            .lte("created_at", end_time.isoformat())
            .not_.is_("api_key_id", "null")
            .execute()
        )
        requests_with_key = with_key_result.count if hasattr(with_key_result, "count") else 0

        # Get requests without api_key_id
        without_key_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .gte("created_at", start_time.isoformat())
            .lte("created_at", end_time.isoformat())
            .is_("api_key_id", "null")
            .execute()
        )
        requests_without_key = (
            without_key_result.count if hasattr(without_key_result, "count") else 0
        )

        # Get requests with NULL api_key_id but valid user_id (potential issues)
        null_key_valid_user_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .gte("created_at", start_time.isoformat())
            .lte("created_at", end_time.isoformat())
            .is_("api_key_id", "null")
            .not_.is_("user_id", "null")
            .execute()
        )
        null_key_valid_user = (
            null_key_valid_user_result.count if hasattr(null_key_valid_user_result, "count") else 0
        )

        # Get requests with both NULL (likely anonymous)
        both_null_result = (
            client.table("chat_completion_requests")
            .select("*", count="exact")
            .gte("created_at", start_time.isoformat())
            .lte("created_at", end_time.isoformat())
            .is_("api_key_id", "null")
            .is_("user_id", "null")
            .execute()
        )
        both_null = both_null_result.count if hasattr(both_null_result, "count") else 0

        # Calculate tracking rate
        tracking_rate = (
            round((requests_with_key / total_requests) * 100, 2) if total_requests > 0 else 0
        )

        # Determine alert status
        if tracking_rate >= 90:
            alert_status = "ok"
        elif tracking_rate >= 70:
            alert_status = "warning"
        else:
            alert_status = "critical"

        # Generate recommendations
        recommendations = []
        if null_key_valid_user > 0:
            pct = (
                round((null_key_valid_user / total_requests) * 100, 2) if total_requests > 0 else 0
            )
            recommendations.append(
                f"Found {null_key_valid_user} ({pct}%) authenticated requests without api_key_id. "
                "This suggests API key lookup failures. Check logs for errors."
            )

        if both_null > total_requests * 0.2:  # More than 20% anonymous
            pct = round((both_null / total_requests) * 100, 2) if total_requests > 0 else 0
            recommendations.append(
                f"High percentage of anonymous requests: {both_null} ({pct}%). "
                "Consider if this is expected for your use case."
            )

        if tracking_rate < 90:
            recommendations.append(
                "Tracking rate below 90%. Review API key lookup logic and retry mechanisms."
            )

        if not recommendations:
            recommendations.append("API key tracking quality is good. No action needed.")

        return {
            "total_requests": total_requests,
            "requests_with_api_key": requests_with_key,
            "requests_without_api_key": requests_without_key,
            "tracking_rate_percent": tracking_rate,
            "breakdown": {
                "null_key_with_valid_user": null_key_valid_user,
                "both_null_likely_anonymous": both_null,
                "null_key_with_valid_user_percent": (
                    round((null_key_valid_user / total_requests) * 100, 2)
                    if total_requests > 0
                    else 0
                ),
                "both_null_percent": (
                    round((both_null / total_requests) * 100, 2) if total_requests > 0 else 0
                ),
            },
            "time_window": {
                "hours": hours,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            },
            "alert_status": alert_status,
            "recommendations": recommendations,
        }

    except Exception as e:
        logger.error(f"Error getting API key tracking quality: {e}", exc_info=True)
        return {
            "error": str(e),
            "total_requests": 0,
            "requests_with_api_key": 0,
            "requests_without_api_key": 0,
            "tracking_rate_percent": 0,
            "alert_status": "error",
            "recommendations": ["Failed to retrieve tracking quality metrics. Check logs."],
        }


@router.get("/api-key-tracking-trend")
async def get_api_key_tracking_trend(
    days: int = Query(7, ge=1, le=30, description="Number of days to analyze (default: 7)"),
    _: str = Depends(get_admin_key),
) -> dict[str, Any]:
    """
    Get API key tracking quality trend over time.

    Provides daily breakdown of tracking quality to identify trends and patterns.

    **Admin authentication required.**

    Args:
        days: Number of days to analyze (1-30, default: 7)

    Returns:
        Dictionary containing:
        - trend_data: List of daily metrics
        - summary: Overall statistics for the period
    """
    try:
        client = get_supabase_client()

        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(days=days)

        # Get daily breakdown
        trend_data = []

        for day_offset in range(days):
            day_start = start_time + timedelta(days=day_offset)
            day_end = day_start + timedelta(days=1)

            # Total for this day
            total_result = (
                client.table("chat_completion_requests")
                .select("*", count="exact")
                .gte("created_at", day_start.isoformat())
                .lt("created_at", day_end.isoformat())
                .execute()
            )
            total = total_result.count if hasattr(total_result, "count") else 0

            # With key for this day
            with_key_result = (
                client.table("chat_completion_requests")
                .select("*", count="exact")
                .gte("created_at", day_start.isoformat())
                .lt("created_at", day_end.isoformat())
                .not_.is_("api_key_id", "null")
                .execute()
            )
            with_key = with_key_result.count if hasattr(with_key_result, "count") else 0

            tracking_rate = round((with_key / total) * 100, 2) if total > 0 else 0

            trend_data.append(
                {
                    "date": day_start.strftime("%Y-%m-%d"),
                    "total_requests": total,
                    "requests_with_api_key": with_key,
                    "tracking_rate_percent": tracking_rate,
                }
            )

        # Calculate summary
        total_all = sum(d["total_requests"] for d in trend_data)
        with_key_all = sum(d["requests_with_api_key"] for d in trend_data)
        avg_tracking_rate = round((with_key_all / total_all) * 100, 2) if total_all > 0 else 0

        return {
            "trend_data": trend_data,
            "summary": {
                "period_days": days,
                "total_requests": total_all,
                "requests_with_api_key": with_key_all,
                "average_tracking_rate_percent": avg_tracking_rate,
                "start_date": start_time.strftime("%Y-%m-%d"),
                "end_date": end_time.strftime("%Y-%m-%d"),
            },
        }

    except Exception as e:
        logger.error(f"Error getting API key tracking trend: {e}", exc_info=True)
        return {
            "error": str(e),
            "trend_data": [],
            "summary": {
                "period_days": days,
                "total_requests": 0,
                "requests_with_api_key": 0,
                "average_tracking_rate_percent": 0,
            },
        }
