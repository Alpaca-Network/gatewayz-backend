"""System health timeline endpoints for provider and model uptime tracking"""
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Query, HTTPException, Depends
from collections import defaultdict
import hashlib
import json
import logging

from ..config.supabase_config import get_supabase_admin
from ..schemas.health_timeline import (
    ProviderUptimeResponse,
    ModelUptimeResponse,
    ProviderUptimeData,
    ModelUptimeData,
    UptimeSample,
    IncidentMetadata,
    IncidentSummary,
)
from ..security.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

# Cache for health timeline data (5-minute TTL)
_health_timeline_cache = {
    "providers": {},  # Key: cache_key, Value: {"data": response, "timestamp": datetime}
    "models": {},     # Key: cache_key, Value: {"data": response, "timestamp": datetime}
    "ttl": 300,       # 5 minutes in seconds
}


def get_cache_key(endpoint: str, **params) -> str:
    """Generate cache key from endpoint and parameters"""
    param_str = json.dumps(params, sort_keys=True)
    return hashlib.md5(f"{endpoint}:{param_str}".encode()).hexdigest()


def get_cached_response(cache_type: str, cache_key: str) -> Optional[dict]:
    """Retrieve cached response if still valid"""
    cache = _health_timeline_cache.get(cache_type, {})
    cached_entry = cache.get(cache_key)

    if not cached_entry:
        return None

    # Check if cache is still fresh
    cache_age = (datetime.now(timezone.utc) - cached_entry["timestamp"]).total_seconds()
    if cache_age >= _health_timeline_cache["ttl"]:
        # Cache expired
        return None

    logger.debug(f"Cache hit for {cache_type}:{cache_key} (age: {cache_age:.1f}s)")
    return cached_entry["data"]


def set_cached_response(cache_type: str, cache_key: str, data: dict):
    """Store response in cache"""
    if cache_type not in _health_timeline_cache:
        _health_timeline_cache[cache_type] = {}

    _health_timeline_cache[cache_type][cache_key] = {
        "data": data,
        "timestamp": datetime.now(timezone.utc)
    }
    logger.debug(f"Cached {cache_type}:{cache_key}")


def parse_period(period: str) -> timedelta:
    """Parse period string to timedelta"""
    if period == "24h":
        return timedelta(hours=24)
    elif period == "72h":
        return timedelta(hours=72)
    elif period == "7d":
        return timedelta(days=7)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid period: {period}")


def parse_bucket(bucket: str) -> int:
    """Parse bucket string to minutes"""
    if bucket == "5m":
        return 5
    elif bucket == "1h":
        return 60
    else:
        raise HTTPException(status_code=400, detail=f"Invalid bucket: {bucket}")


def get_period_label(period: str) -> str:
    """Convert period to human-readable label"""
    if period == "24h":
        return "Last 24 hours"
    elif period == "72h":
        return "Last 72 hours"
    elif period == "7d":
        return "Last 7 days"
    return period


def calculate_status(success_rate: float) -> Literal["operational", "degraded", "downtime"]:
    """Determine status based on success rate"""
    if success_rate >= 0.95:
        return "operational"
    elif success_rate >= 0.50:
        return "degraded"
    else:
        return "downtime"


def detect_incident(
    error_rate: float, avg_response_time: Optional[float], status: str
) -> Optional[IncidentMetadata]:
    """Detect if an incident occurred in a time bucket"""
    if status == "downtime":
        return IncidentMetadata(
            type="downtime",
            region=None,
            error_rate=error_rate * 100
        )
    elif error_rate >= 0.15:
        return IncidentMetadata(
            type="error_surge",
            region=None,
            error_rate=error_rate * 100
        )
    elif avg_response_time and avg_response_time > 5000:  # > 5 seconds
        return IncidentMetadata(
            type="latency_spike",
            region=None,
            error_rate=error_rate * 100
        )
    return None


def calculate_mttr(incidents: list[dict]) -> float:
    """Calculate mean time to recovery from incidents"""
    if not incidents:
        return 0.0

    # Assuming each incident is resolved by the next operational bucket
    # For simplicity, we'll use the bucket duration as recovery time
    # In a real system, this would track actual incident resolution times
    total_recovery_minutes = sum(inc.get("duration_minutes", 0) for inc in incidents)
    return total_recovery_minutes / len(incidents) if incidents else 0.0


def get_worst_status(statuses: list[str]) -> Literal["operational", "degraded", "downtime"]:
    """Get the worst status from a list"""
    if "downtime" in statuses:
        return "downtime"
    elif "degraded" in statuses:
        return "degraded"
    return "operational"


@router.get(
    "/health/providers/uptime",
    response_model=ProviderUptimeResponse,
    tags=["health", "monitoring"]
)
async def get_providers_uptime(
    period: Literal["24h", "72h", "7d"] = Query("72h", description="Time period for uptime analysis"),
    bucket: Literal["5m", "1h"] = Query("1h", description="Time bucket size for sampling"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get provider uptime timeline with time-bucketed samples.

    Returns uptime percentage, status samples, and incident summaries for each provider.

    Cached for 5 minutes to reduce database load.
    """
    # Check cache first
    cache_key = get_cache_key("providers_uptime", period=period, bucket=bucket)
    cached = get_cached_response("providers", cache_key)
    if cached:
        return ProviderUptimeResponse(**cached)

    supabase = get_supabase_admin()
    current_time = datetime.now(timezone.utc)

    # Parse parameters
    time_delta = parse_period(period)
    bucket_minutes = parse_bucket(bucket)
    start_time = current_time - time_delta
    period_label = get_period_label(period)

    # Query model_health_history for the time range
    response = (
        supabase.table("model_health_history")
        .select("provider,gateway,checked_at,status,response_time_ms,error_message,http_status_code")
        .gte("checked_at", start_time.isoformat())
        .lte("checked_at", current_time.isoformat())
        .order("checked_at", desc=False)
        .execute()
    )

    history_records = response.data

    if not history_records:
        return ProviderUptimeResponse(success=True, providers=[])

    # Group by provider and gateway
    provider_data = defaultdict(lambda: {
        "gateway": None,
        "records": [],
        "last_checked": None
    })

    for record in history_records:
        provider = record["provider"]
        gateway = record.get("gateway", "primary")
        key = f"{provider}::{gateway}"

        provider_data[key]["gateway"] = gateway
        provider_data[key]["records"].append(record)

        # Track last checked time
        checked_at = datetime.fromisoformat(record["checked_at"].replace("Z", "+00:00"))
        if not provider_data[key]["last_checked"] or checked_at > provider_data[key]["last_checked"]:
            provider_data[key]["last_checked"] = checked_at

    # Process each provider
    providers = []

    for key, data in provider_data.items():
        provider_name, gateway = key.split("::")
        records = data["records"]

        # Create time buckets
        num_buckets = int(time_delta.total_seconds() / (bucket_minutes * 60))
        buckets = []

        for i in range(num_buckets):
            bucket_start = start_time + timedelta(minutes=i * bucket_minutes)
            bucket_end = bucket_start + timedelta(minutes=bucket_minutes)

            # Filter records for this bucket
            bucket_records = [
                r for r in records
                if bucket_start <= datetime.fromisoformat(r["checked_at"].replace("Z", "+00:00")) < bucket_end
            ]

            if not bucket_records:
                # No data for this bucket - skip it to avoid false "operational" status
                # This allows gaps in monitoring to be visible as gaps in the timeline
                continue

            # Calculate metrics for this bucket
            total = len(bucket_records)
            successes = sum(1 for r in bucket_records if r["status"] == "success")
            errors = total - successes
            success_rate = successes / total if total > 0 else 0.0
            error_rate = errors / total if total > 0 else 0.0

            # Calculate average response time
            response_times = [r["response_time_ms"] for r in bucket_records if r.get("response_time_ms")]
            avg_response_time = sum(response_times) / len(response_times) if response_times else None

            # Determine status
            bucket_status = calculate_status(success_rate)

            # Detect incident
            incident = detect_incident(error_rate, avg_response_time, bucket_status)

            buckets.append({
                "timestamp": bucket_start,
                "status": bucket_status,
                "duration_minutes": bucket_minutes,
                "incident": incident,
                "success_rate": success_rate
            })

        # Calculate overall uptime percentage
        total_success_rate = sum(b["success_rate"] for b in buckets) / len(buckets) if buckets else 0.0
        uptime_percentage = round(total_success_rate * 100, 3)

        # Create samples
        samples = [
            UptimeSample(
                timestamp=b["timestamp"],
                status=b["status"],
                duration_minutes=b["duration_minutes"],
                incident=b["incident"]
            )
            for b in buckets
        ]

        # Calculate incident summary
        incidents = [b for b in buckets if b["incident"] is not None]
        statuses = [b["status"] for b in buckets]

        incident_summary = IncidentSummary(
            total_incidents=len(incidents),
            mttr_minutes=round(calculate_mttr(incidents), 2),
            worst_status=get_worst_status(statuses)
        )

        providers.append(
            ProviderUptimeData(
                provider=provider_name,
                gateway=gateway,
                uptime_percentage=uptime_percentage,
                period_label=period_label,
                last_checked=data["last_checked"] or current_time,
                samples=samples,
                incident_summary=incident_summary
            )
        )

    # Sort by uptime percentage (worst first for alerting)
    providers.sort(key=lambda p: p.uptime_percentage)

    # Create response
    response = ProviderUptimeResponse(success=True, providers=providers)

    # Cache the response
    set_cached_response("providers", cache_key, response.model_dump())

    return response


@router.get(
    "/health/models/uptime",
    response_model=ModelUptimeResponse,
    tags=["health", "monitoring"]
)
async def get_models_uptime(
    period: Literal["24h", "72h", "7d"] = Query("72h", description="Time period for uptime analysis"),
    bucket: Literal["5m", "1h"] = Query("1h", description="Time bucket size for sampling"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of models to return"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get model uptime timeline with time-bucketed samples.

    Returns uptime percentage, status samples, and incident summaries for top models.

    Cached for 5 minutes to reduce database load.
    """
    # Check cache first
    cache_key = get_cache_key("models_uptime", period=period, bucket=bucket, limit=limit)
    cached = get_cached_response("models", cache_key)
    if cached:
        return ModelUptimeResponse(**cached)

    supabase = get_supabase_admin()
    current_time = datetime.now(timezone.utc)

    # Parse parameters
    time_delta = parse_period(period)
    bucket_minutes = parse_bucket(bucket)
    start_time = current_time - time_delta
    period_label = get_period_label(period)

    # Query model_health_history for the time range
    response = (
        supabase.table("model_health_history")
        .select("provider,model,gateway,checked_at,status,response_time_ms,error_message,http_status_code")
        .gte("checked_at", start_time.isoformat())
        .lte("checked_at", current_time.isoformat())
        .order("checked_at", desc=False)
        .execute()
    )

    history_records = response.data

    if not history_records:
        return ModelUptimeResponse(success=True, models=[])

    # Group by model, provider, and gateway
    model_data = defaultdict(lambda: {
        "provider": None,
        "gateway": None,
        "records": [],
        "last_checked": None
    })

    for record in history_records:
        model = record["model"]
        provider = record["provider"]
        gateway = record.get("gateway", "primary")
        key = f"{model}::{provider}::{gateway}"

        model_data[key]["provider"] = provider
        model_data[key]["gateway"] = gateway
        model_data[key]["records"].append(record)

        # Track last checked time
        checked_at = datetime.fromisoformat(record["checked_at"].replace("Z", "+00:00"))
        if not model_data[key]["last_checked"] or checked_at > model_data[key]["last_checked"]:
            model_data[key]["last_checked"] = checked_at

    # Process each model
    models = []

    for key, data in model_data.items():
        model_name, provider_name, gateway = key.split("::")
        records = data["records"]

        # Create time buckets (same logic as providers)
        num_buckets = int(time_delta.total_seconds() / (bucket_minutes * 60))
        buckets = []

        for i in range(num_buckets):
            bucket_start = start_time + timedelta(minutes=i * bucket_minutes)
            bucket_end = bucket_start + timedelta(minutes=bucket_minutes)

            # Filter records for this bucket
            bucket_records = [
                r for r in records
                if bucket_start <= datetime.fromisoformat(r["checked_at"].replace("Z", "+00:00")) < bucket_end
            ]

            if not bucket_records:
                # No data for this bucket - skip it to avoid false "operational" status
                # This allows gaps in monitoring to be visible as gaps in the timeline
                continue

            # Calculate metrics for this bucket
            total = len(bucket_records)
            successes = sum(1 for r in bucket_records if r["status"] == "success")
            errors = total - successes
            success_rate = successes / total if total > 0 else 0.0
            error_rate = errors / total if total > 0 else 0.0

            # Calculate average response time
            response_times = [r["response_time_ms"] for r in bucket_records if r.get("response_time_ms")]
            avg_response_time = sum(response_times) / len(response_times) if response_times else None

            # Determine status
            bucket_status = calculate_status(success_rate)

            # Detect incident
            incident = detect_incident(error_rate, avg_response_time, bucket_status)

            buckets.append({
                "timestamp": bucket_start,
                "status": bucket_status,
                "duration_minutes": bucket_minutes,
                "incident": incident,
                "success_rate": success_rate
            })

        # Calculate overall uptime percentage
        total_success_rate = sum(b["success_rate"] for b in buckets) / len(buckets) if buckets else 0.0
        uptime_percentage = round(total_success_rate * 100, 3)

        # Create samples
        samples = [
            UptimeSample(
                timestamp=b["timestamp"],
                status=b["status"],
                duration_minutes=b["duration_minutes"],
                incident=b["incident"]
            )
            for b in buckets
        ]

        # Calculate incident summary
        incidents = [b for b in buckets if b["incident"] is not None]
        statuses = [b["status"] for b in buckets]

        incident_summary = IncidentSummary(
            total_incidents=len(incidents),
            mttr_minutes=round(calculate_mttr(incidents), 2),
            worst_status=get_worst_status(statuses)
        )

        models.append(
            ModelUptimeData(
                model=model_name,
                provider=provider_name,
                gateway=gateway,
                uptime_percentage=uptime_percentage,
                period_label=period_label,
                last_checked=data["last_checked"] or current_time,
                samples=samples,
                incident_summary=incident_summary
            )
        )

    # Sort by uptime percentage (worst first) and apply limit
    models.sort(key=lambda m: m.uptime_percentage)
    models = models[:limit]

    # Create response
    response = ModelUptimeResponse(success=True, models=models)

    # Cache the response
    set_cached_response("models", cache_key, response.model_dump())

    return response
