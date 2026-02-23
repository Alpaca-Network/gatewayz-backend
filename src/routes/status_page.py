"""
Public Status Page API Endpoints

Provides public-facing endpoints for status page display without authentication.
Optimized for performance with caching and pre-aggregated data.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.config.supabase_config import supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["status-page"])


@router.get("/", response_model=dict[str, Any])
async def get_overall_status():
    """
    Get overall system status for status page

    Public endpoint - no authentication required.
    Returns current status, uptime, and basic metrics.
    """
    try:
        # Get overall system health from view
        response = (
            supabase.table("provider_health_current")
            .select("*")
            .execute()
        )

        providers = response.data or []

        if not providers:
            return {
                "status": "unknown",
                "message": "Status data not available",
                "timestamp": datetime.now(UTC).isoformat(),
            }

        # Calculate overall metrics
        total_models = sum(p["total_models"] for p in providers)
        healthy_models = sum(p["healthy_models"] for p in providers)
        offline_models = sum(p["offline_models"] for p in providers)

        # Ensure healthy_models doesn't exceed total_models (data consistency fix)
        # This can happen when the database view has stale data
        if healthy_models > total_models:
            logger.warning(
                f"Data inconsistency: healthy_models ({healthy_models}) > total_models ({total_models}). "
                "Constraining healthy_models to total_models."
            )
            healthy_models = total_models

        # Determine overall status
        if total_models == 0:
            status = "unknown"
            status_message = "No models monitored"
        elif offline_models == 0:
            status = "operational"
            status_message = "All Systems Operational"
        elif offline_models < total_models * 0.1:
            status = "degraded"
            status_message = "Partial Service Degradation"
        else:
            status = "major_outage"
            status_message = "Major Service Disruption"

        uptime_percentage = (healthy_models / total_models * 100) if total_models > 0 else 0

        # Get active incidents count
        incidents_response = (
            supabase.table("model_health_incidents")
            .select("id", count="exact")
            .eq("status", "active")
            .execute()
        )

        active_incidents = incidents_response.count or 0

        # Calculate gateway health metrics
        # Filter out None and empty string gateways
        gateways_set = set(
            p["gateway"] for p in providers if p.get("gateway") and p["gateway"].strip()
        )
        total_gateways = len(gateways_set) if gateways_set else 0

        # Calculate healthy gateways (gateways that have at least one healthy provider)
        gateway_health = {}
        for p in providers:
            gw = p.get("gateway")
            if gw and gw.strip():  # Filter out None and empty strings
                if gw not in gateway_health:
                    gateway_health[gw] = {"has_healthy": False}
                # Consider a gateway healthy if any of its providers are operational
                if p.get("status_indicator") == "operational":
                    gateway_health[gw]["has_healthy"] = True
        healthy_gateways = sum(1 for g in gateway_health.values() if g.get("has_healthy", False))

        # Calculate gateway health percentage
        gateway_health_percentage = (
            round((healthy_gateways / total_gateways) * 100, 1)
            if total_gateways > 0
            else 0.0
        )

        return {
            "status": status,
            "status_message": status_message,
            "uptime_percentage": round(uptime_percentage, 2),
            "total_models": total_models,
            "healthy_models": healthy_models,
            "offline_models": offline_models,
            "total_providers": len(providers),
            "total_gateways": total_gateways,
            "healthy_gateways": healthy_gateways,
            "gateway_health_percentage": gateway_health_percentage,
            "active_incidents": active_incidents,
            "last_updated": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get overall status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve status") from e


@router.get("/providers", response_model=list[dict[str, Any]])
async def get_providers_status():
    """
    Get status for all providers

    Public endpoint - no authentication required.
    Returns health status for each provider/gateway combination.
    """
    try:
        response = (
            supabase.table("provider_health_current")
            .select("*")
            .order("provider")
            .execute()
        )

        providers = response.data or []

        # Format for frontend display
        formatted = []
        for provider in providers:
            healthy = provider["healthy_models"]
            total = provider["total_models"]

            # Apply same data consistency check as main status endpoint
            if healthy > total:
                logger.warning(
                    f"Data inconsistency in provider {provider['provider']}/{provider['gateway']}: "
                    f"healthy_models ({healthy}) > total_models ({total}). Capping to total."
                )
                healthy = total

            formatted.append({
                "name": provider["provider"],
                "gateway": provider["gateway"],
                "status": provider["status_indicator"],
                "uptime_24h": round(provider["avg_uptime_24h"] or 0, 2),
                "uptime_7d": round(provider["avg_uptime_7d"] or 0, 2),
                "total_models": total,
                "healthy_models": healthy,
                "offline_models": provider["offline_models"],
                "avg_response_time_ms": round(provider["avg_response_time_ms"] or 0, 0),
                "last_checked": provider["last_checked_at"],
            })

        return formatted

    except Exception as e:
        logger.error(f"Failed to get providers status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve provider status") from e


@router.get("/models", response_model=list[dict[str, Any]])
async def get_models_status(
    provider: str | None = Query(None, description="Filter by provider"),
    gateway: str | None = Query(None, description="Filter by gateway"),
    status: str | None = Query(None, description="Filter by status"),
    tier: str | None = Query(None, description="Filter by monitoring tier"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get status for models

    Public endpoint - no authentication required.
    Supports filtering and pagination.
    """
    try:
        query = supabase.table("model_status_current").select("*")

        # Apply filters
        if provider:
            query = query.eq("provider", provider)
        if gateway:
            query = query.eq("gateway", gateway)
        if status:
            query = query.eq("status_indicator", status)
        if tier:
            query = query.eq("monitoring_tier", tier)

        # Apply pagination
        query = query.range(offset, offset + limit - 1)
        query = query.order("usage_count_24h", desc=True)

        response = query.execute()
        models = response.data or []

        # Format for frontend
        formatted = []
        for model in models:
            formatted.append({
                "model_id": model["model"],
                "provider": model["provider"],
                "gateway": model["gateway"],
                "status": model["status_indicator"],
                "tier": model["monitoring_tier"],
                "uptime_24h": round(model["uptime_percentage_24h"] or 0, 2),
                "uptime_7d": round(model["uptime_percentage_7d"] or 0, 2),
                "uptime_30d": round(model["uptime_percentage_30d"] or 0, 2),
                "avg_response_time_ms": round(model["average_response_time_ms"] or 0, 0),
                "last_checked": model["last_called_at"],
                "last_success": model["last_success_at"],
                "last_failure": model["last_failure_at"],
                "circuit_breaker_state": model["circuit_breaker_state"],
                "active_incidents": model["active_incidents_count"],
            })

        return formatted

    except Exception as e:
        logger.error(f"Failed to get models status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve model status") from e


@router.get("/models/{provider}/{model_id}", response_model=dict[str, Any])
async def get_model_status(provider: str, model_id: str, gateway: str | None = Query(None)):
    """
    Get status for a specific model

    Public endpoint - no authentication required.
    Returns detailed status information for a single model.
    """
    try:
        query = (
            supabase.table("model_status_current")
            .select("*")
            .eq("provider", provider)
            .eq("model", model_id)
        )

        if gateway:
            query = query.eq("gateway", gateway)

        response = query.maybe_single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Model not found")

        model = response.data

        return {
            "model_id": model["model"],
            "provider": model["provider"],
            "gateway": model["gateway"],
            "status": model["status_indicator"],
            "tier": model["monitoring_tier"],
            "uptime_24h": round(model["uptime_percentage_24h"] or 0, 2),
            "uptime_7d": round(model["uptime_percentage_7d"] or 0, 2),
            "uptime_30d": round(model["uptime_percentage_30d"] or 0, 2),
            "avg_response_time_ms": round(model["average_response_time_ms"] or 0, 0),
            "last_checked": model["last_called_at"],
            "last_success": model["last_success_at"],
            "last_failure": model["last_failure_at"],
            "circuit_breaker_state": model["circuit_breaker_state"],
            "consecutive_failures": model["consecutive_failures"],
            "usage_24h": model["usage_count_24h"],
            "is_enabled": model["is_enabled"],
            "active_incidents": model["active_incidents_count"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get model status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve model status") from e


@router.get("/incidents", response_model=list[dict[str, Any]])
async def get_incidents(
    status: str | None = Query(None, description="Filter by status (active, resolved)"),
    severity: str | None = Query(None, description="Filter by severity"),
    provider: str | None = Query(None, description="Filter by provider"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get incidents

    Public endpoint - no authentication required.
    Returns recent incidents with filtering.
    """
    try:
        query = supabase.table("model_health_incidents").select("*")

        # Apply filters
        if status:
            query = query.eq("status", status)
        if severity:
            query = query.eq("severity", severity)
        if provider:
            query = query.eq("provider", provider)

        # Pagination
        query = query.range(offset, offset + limit - 1)
        query = query.order("started_at", desc=True)

        response = query.execute()
        incidents = response.data or []

        # Format for frontend
        formatted = []
        for incident in incidents:
            duration = None
            if incident["resolved_at"]:
                duration = incident["duration_seconds"]
            else:
                # Calculate current duration for active incidents
                started = datetime.fromisoformat(incident["started_at"])
                duration = int((datetime.now(UTC) - started).total_seconds())

            formatted.append({
                "id": incident["id"],
                "provider": incident["provider"],
                "model": incident["model"],
                "gateway": incident["gateway"],
                "type": incident["incident_type"],
                "severity": incident["severity"],
                "status": incident["status"],
                "started_at": incident["started_at"],
                "resolved_at": incident["resolved_at"],
                "duration_seconds": duration,
                "duration_human": _format_duration(duration) if duration else None,
                "error_message": incident["error_message"],
                "error_count": incident["error_count"],
                "resolution_notes": incident.get("resolution_notes"),
            })

        return formatted

    except Exception as e:
        logger.error(f"Failed to get incidents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve incidents") from e


@router.get("/uptime/{provider}/{model_id}", response_model=dict[str, Any])
async def get_model_uptime_history(
    provider: str,
    model_id: str,
    gateway: str | None = Query(None),
    period: str = Query("24h", description="Time period: 24h, 7d, 30d"),
):
    """
    Get uptime history for a specific model

    Public endpoint - no authentication required.
    Returns time-series uptime data for charts.
    """
    try:
        # Determine time range
        now = datetime.now(UTC)
        if period == "24h":
            start_time = now - timedelta(hours=24)
            interval = "hour"
        elif period == "7d":
            start_time = now - timedelta(days=7)
            interval = "day"
        elif period == "30d":
            start_time = now - timedelta(days=30)
            interval = "day"
        else:
            raise HTTPException(status_code=400, detail="Invalid period")

        # Query aggregated data
        query = (
            supabase.table("model_health_aggregates")
            .select("*")
            .eq("provider", provider)
            .eq("model", model_id)
            .eq("aggregation_period", interval)
            .gte("period_start", start_time.isoformat())
        )

        if gateway:
            query = query.eq("gateway", gateway)

        response = query.order("period_start", desc=False).execute()
        data = response.data or []

        # Format for charting
        points = []
        for point in data:
            points.append({
                "timestamp": point["period_start"],
                "uptime_percentage": round(point["uptime_percentage"] or 0, 2),
                "avg_response_time_ms": round(point["avg_response_time_ms"] or 0, 0),
                "total_checks": point["total_checks"],
                "successful_checks": point["successful_checks"],
                "failed_checks": point["failed_checks"],
            })

        return {
            "provider": provider,
            "model": model_id,
            "period": period,
            "data_points": points,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get uptime history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve uptime history") from e


@router.get("/search", response_model=list[dict[str, Any]])
async def search_models(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Search for models by name or provider

    Public endpoint - no authentication required.
    """
    try:
        # Sanitize search query to prevent PostgREST filter injection
        # Remove special characters that have meaning in PostgREST syntax
        sanitized_q = q.replace(",", "").replace("(", "").replace(")", "").replace(".", "")

        query = (
            supabase.table("model_status_current")
            .select("*")
            .or_(f"model.ilike.%{sanitized_q}%,provider.ilike.%{sanitized_q}%")
            .limit(limit)
        )

        response = query.execute()
        models = response.data or []

        formatted = []
        for model in models:
            formatted.append({
                "model_id": model["model"],
                "provider": model["provider"],
                "gateway": model["gateway"],
                "status": model["status_indicator"],
                "tier": model["monitoring_tier"],
                "uptime_24h": round(model["uptime_percentage_24h"] or 0, 2),
            })

        return formatted

    except Exception as e:
        logger.error(f"Failed to search models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to search models") from e


@router.get("/stats", response_model=dict[str, Any])
async def get_stats():
    """
    Get overall statistics for status page

    Public endpoint - no authentication required.
    Returns summary statistics and metrics.
    """
    try:
        # Get model counts by tier
        tier_counts_response = (
            supabase.table("model_health_tracking")
            .select("monitoring_tier", count="exact")
            .eq("is_enabled", True)
            .execute()
        )

        tier_data = tier_counts_response.data or []
        tier_counts = {}
        for row in tier_data:
            tier = row.get("monitoring_tier", "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # Get incident statistics
        incidents_response = (
            supabase.table("model_health_incidents")
            .select("severity,status", count="exact")
            .execute()
        )

        total_incidents = incidents_response.count or 0
        active_incidents = len([i for i in incidents_response.data if i.get("status") == "active"])

        # Get check statistics from last 24h
        yesterday = datetime.now(UTC) - timedelta(hours=24)
        checks_response = (
            supabase.table("model_health_history")
            .select("status", count="exact")
            .gte("checked_at", yesterday.isoformat())
            .execute()
        )

        total_checks = checks_response.count or 0
        successful_checks = len([c for c in checks_response.data if c.get("status") == "success"])

        return {
            "monitoring": {
                "total_models": sum(tier_counts.values()),
                "critical_tier": tier_counts.get("critical", 0),
                "popular_tier": tier_counts.get("popular", 0),
                "standard_tier": tier_counts.get("standard", 0),
                "on_demand_tier": tier_counts.get("on_demand", 0),
            },
            "incidents": {
                "total_all_time": total_incidents,
                "active": active_incidents,
                "resolved": total_incidents - active_incidents,
            },
            "checks_24h": {
                "total": total_checks,
                "successful": successful_checks,
                "failed": total_checks - successful_checks,
                "success_rate": round(
                    (successful_checks / total_checks * 100) if total_checks > 0 else 0, 2
                ),
            },
            "last_updated": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve statistics") from e


def _format_duration(seconds: int) -> str:
    """Format duration in human-readable format"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"
