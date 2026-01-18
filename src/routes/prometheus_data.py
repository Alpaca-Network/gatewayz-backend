"""
Prometheus Data Endpoints for Grafana Stack Integration.

This module provides REST API endpoints specifically designed for the Railway Grafana stack
(Prometheus, Grafana, Loki, Tempo) to consume telemetry data.

All endpoints are under the /prometheus/data/* path to avoid conflicts with existing routes.

CACHING STRATEGY:
- All expensive endpoints use Redis caching with configurable TTL
- Each response includes `cached_at` and `cache_ttl_seconds` for transparency
- Cache keys are prefixed with "prometheus:data:" for easy management
- Cache can be invalidated via /prometheus/data/cache/invalidate

Endpoints:
- GET /prometheus/data/health-scores         → Provider health scores (TTL: 30s)
- GET /prometheus/data/circuit-breakers      → Circuit breaker states (TTL: 15s)
- GET /prometheus/data/anomalies             → Active anomalies/alerts (TTL: 30s)
- GET /prometheus/data/cost-analysis         → Cost breakdown (TTL: 5min)
- GET /prometheus/data/error-rates           → Error rates by model (TTL: 1min)
- GET /prometheus/data/chat-requests/summary → Request summary (TTL: 30s)
- GET /prometheus/data/stats/realtime        → Real-time stats (TTL: 15s)
- GET /prometheus/data/provider-health       → Provider health scorecard (TTL: 30s)
- GET /prometheus/data/instrumentation/*     → Instrumentation status (no cache)
- GET /prometheus/data/model-performance     → Model metrics (TTL: 1min)
- GET /prometheus/data/trending-models       → Trending models (TTL: 2min)
- DELETE /prometheus/data/cache/invalidate   → Invalidate all caches
- GET /prometheus/data/metrics               → Prometheus format metrics for alerts

PROMETHEUS METRICS EXPOSED (for Grafana alerting):
- health_score{provider="..."} - Provider health score (0-100)
- circuit_breaker_state{provider="...", model="...", state="..."} - Circuit breaker state (1=active)
- error_rate{provider="..."} - Provider error rate (0-1)
- provider_availability{provider="..."} - Provider availability (0-100)
- total_requests{provider="..."} - Total requests in last hour
- avg_latency_ms{provider="..."} - Average latency in ms
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.security.deps import get_optional_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prometheus/data", tags=["prometheus-data"])

# Cache configuration
CACHE_PREFIX = "prometheus:data:"
CACHE_TTL = {
    "health_scores": 30,        # 30 seconds - health scores change frequently
    "circuit_breakers": 15,     # 15 seconds - circuit states can change rapidly
    "anomalies": 30,            # 30 seconds - anomalies are time-sensitive
    "cost_analysis": 300,       # 5 minutes - cost data is relatively stable
    "error_rates": 60,          # 1 minute - error rates need quick updates
    "chat_summary": 30,         # 30 seconds - overview should be fresh
    "realtime_stats": 15,       # 15 seconds - "realtime" should be fresh
    "provider_health": 30,      # 30 seconds - health scorecard
    "model_performance": 60,    # 1 minute - model stats
    "trending_models": 120,     # 2 minutes - trends don't change that fast
}


# ============================================================================
# Response Models
# ============================================================================

class HealthScoreItem(BaseModel):
    """Single provider health score."""
    provider: str
    health_score: float = Field(..., ge=0, le=100)
    status: str  # healthy, degraded, unhealthy
    availability: float = Field(..., ge=0, le=100)
    error_rate: float = Field(..., ge=0, le=100)
    avg_latency_ms: float
    last_updated: str


class CircuitBreakerItem(BaseModel):
    """Circuit breaker state for grafana table."""
    provider: str
    model: str
    status: str  # CLOSED, OPEN, HALF_OPEN
    requests_last_5m: int
    failures_last_5m: int
    failure_rate: float
    is_available: bool
    last_failure_time: str | None


class AnomalyItem(BaseModel):
    """Detected anomaly for alerts panel."""
    type: str  # high_error_rate, latency_spike, cost_spike, etc.
    severity: str  # CRITICAL, WARNING, INFO
    provider: str | None
    model: str | None
    description: str
    current_value: float
    threshold: float
    detected_at: str


class CostAnalysisItem(BaseModel):
    """Cost analysis per model/provider."""
    model: str
    provider: str
    cost: float
    requests: int
    cost_per_request: float
    tokens_used: int
    cost_per_1k_tokens: float


class ErrorRateItem(BaseModel):
    """Error rate by model."""
    model: str
    provider: str
    total_requests: int
    failed_requests: int
    error_rate: float
    error_types: dict[str, int]


# ============================================================================
# Helper Functions
# ============================================================================

def _classify_health_status(score: float) -> str:
    """Classify health score into status."""
    if score >= 80:
        return "healthy"
    elif score >= 50:
        return "degraded"
    else:
        return "unhealthy"


async def _get_redis_client():
    """Get Redis client for caching."""
    try:
        from src.config.redis_config import get_redis_client
        return await get_redis_client()
    except Exception as e:
        logger.warning(f"Failed to get Redis client for caching: {e}")
        return None


async def _get_cached_data(cache_key: str) -> dict | None:
    """Get data from Redis cache."""
    try:
        redis = await _get_redis_client()
        if not redis:
            return None

        full_key = f"{CACHE_PREFIX}{cache_key}"
        cached = await redis.get(full_key)
        if cached:
            return json.loads(cached)
        return None
    except Exception as e:
        logger.warning(f"Cache read error for {cache_key}: {e}")
        return None


async def _set_cached_data(cache_key: str, data: dict, ttl_seconds: int) -> bool:
    """Set data in Redis cache with TTL."""
    try:
        redis = await _get_redis_client()
        if not redis:
            return False

        full_key = f"{CACHE_PREFIX}{cache_key}"
        # Add cache metadata to data
        data["_cache_metadata"] = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "cache_ttl_seconds": ttl_seconds,
            "cache_key": cache_key
        }
        await redis.setex(full_key, ttl_seconds, json.dumps(data, default=str))
        return True
    except Exception as e:
        logger.warning(f"Cache write error for {cache_key}: {e}")
        return False


async def _invalidate_cache(pattern: str = "*") -> int:
    """Invalidate cache entries matching pattern."""
    try:
        redis = await _get_redis_client()
        if not redis:
            return 0

        full_pattern = f"{CACHE_PREFIX}{pattern}"
        keys = await redis.keys(full_pattern)
        if keys:
            await redis.delete(*keys)
            return len(keys)
        return 0
    except Exception as e:
        logger.warning(f"Cache invalidation error: {e}")
        return 0


def _add_cache_info(response: dict, cache_key: str, ttl: int, from_cache: bool) -> dict:
    """Add cache information to response."""
    response["_cache"] = {
        "from_cache": from_cache,
        "cache_key": cache_key,
        "cache_ttl_seconds": ttl,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return response


# ============================================================================
# Cache Management Endpoints
# ============================================================================

@router.delete("/cache/invalidate")
async def invalidate_cache(
    pattern: str = Query("*", description="Cache key pattern to invalidate (* for all)"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Invalidate prometheus data cache entries.

    Use this to force fresh data on the next request.
    Patterns: * (all), health_scores, circuit_breakers, anomalies, etc.
    """
    count = await _invalidate_cache(pattern)
    return {
        "success": True,
        "invalidated_count": count,
        "pattern": pattern,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/cache/status")
async def get_cache_status(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get cache status and statistics.

    Returns information about cached data and TTL settings.
    """
    try:
        redis = await _get_redis_client()
        if not redis:
            return {
                "status": "unavailable",
                "message": "Redis not connected",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        # Get all prometheus data cache keys
        keys = await redis.keys(f"{CACHE_PREFIX}*")
        cache_entries = []

        for key in keys:
            ttl = await redis.ttl(key)
            short_key = key.replace(CACHE_PREFIX, "") if isinstance(key, str) else key.decode().replace(CACHE_PREFIX, "")
            cache_entries.append({
                "key": short_key,
                "ttl_remaining": ttl
            })

        return {
            "status": "connected",
            "cache_prefix": CACHE_PREFIX,
            "ttl_settings": CACHE_TTL,
            "cached_entries": cache_entries,
            "entry_count": len(cache_entries),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


# ============================================================================
# Prometheus Format Metrics Endpoint (for Grafana Alerting)
# ============================================================================

@router.get("/metrics")
async def get_prometheus_metrics(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get metrics in Prometheus text exposition format.

    This endpoint is designed to be scraped by Prometheus and provides metrics
    required for Grafana alerting rules:

    - health_score{provider="..."} - Provider health score (0-100)
    - circuit_breaker_state{provider="...", model="...", state="..."} - 1 if state matches
    - error_rate{provider="..."} - Provider error rate (0-1)
    - provider_availability{provider="..."} - Provider availability (0-100)
    - total_requests{provider="..."} - Total requests in last hour
    - avg_latency_ms{provider="..."} - Average latency in ms

    Returns: text/plain in Prometheus format
    """
    from fastapi.responses import Response

    try:
        from src.services.redis_metrics import get_redis_metrics
        from src.services.model_availability import availability_service

        lines = []
        now_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Add HELP and TYPE declarations
        lines.append("# HELP health_score Provider health score from 0 to 100")
        lines.append("# TYPE health_score gauge")
        lines.append("# HELP circuit_breaker_state Circuit breaker state (1 if state matches)")
        lines.append("# TYPE circuit_breaker_state gauge")
        lines.append("# HELP error_rate Provider error rate from 0 to 1")
        lines.append("# TYPE error_rate gauge")
        lines.append("# HELP provider_availability Provider availability percentage from 0 to 100")
        lines.append("# TYPE provider_availability gauge")
        lines.append("# HELP total_requests Total requests in the last hour")
        lines.append("# TYPE total_requests gauge")
        lines.append("# HELP avg_latency_ms Average latency in milliseconds")
        lines.append("# TYPE avg_latency_ms gauge")
        lines.append("# HELP gatewayz_data_scrape_success Whether the prometheus data scrape was successful")
        lines.append("# TYPE gatewayz_data_scrape_success gauge")

        try:
            redis_metrics = get_redis_metrics()
            health_scores = await redis_metrics.get_all_provider_health()

            # Emit health_score, error_rate, provider_availability metrics
            for provider, score in health_scores.items():
                # Sanitize provider name for Prometheus labels
                safe_provider = provider.replace('"', '\\"').replace("\\", "\\\\")

                # Get hourly stats for this provider
                hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=1)

                total_requests = sum(h.get("total_requests", 0) for h in hourly_stats.values())
                total_errors = sum(h.get("error_count", 0) for h in hourly_stats.values())
                total_latency = sum(h.get("total_latency", 0) for h in hourly_stats.values())

                error_rate_value = (total_errors / total_requests) if total_requests > 0 else 0.0
                availability = 100.0 - (error_rate_value * 100)
                avg_latency = (total_latency / total_requests) if total_requests > 0 else 0.0

                lines.append(f'health_score{{provider="{safe_provider}"}} {score}')
                lines.append(f'error_rate{{provider="{safe_provider}"}} {error_rate_value:.6f}')
                lines.append(f'provider_availability{{provider="{safe_provider}"}} {availability:.2f}')
                lines.append(f'total_requests{{provider="{safe_provider}"}} {total_requests}')
                lines.append(f'avg_latency_ms{{provider="{safe_provider}"}} {avg_latency:.2f}')

            # Emit circuit_breaker_state metrics
            for provider_key, circuit_data in availability_service.circuit_breakers.items():
                parts = provider_key.split(":", 1)
                if len(parts) != 2:
                    continue

                provider, model = parts
                safe_provider = provider.replace('"', '\\"').replace("\\", "\\\\")
                safe_model = model.replace('"', '\\"').replace("\\", "\\\\")
                state = circuit_data.state.name.lower()

                # Emit 1 for current state, 0 for other states
                for check_state in ["open", "closed", "half_open"]:
                    value = 1 if state == check_state else 0
                    lines.append(
                        f'circuit_breaker_state{{provider="{safe_provider}",model="{safe_model}",state="{check_state}"}} {value}'
                    )

            # Mark scrape as successful
            lines.append("gatewayz_data_scrape_success 1")

        except Exception as e:
            logger.error(f"Error collecting metrics: {e}", exc_info=True)
            # Still return a valid Prometheus response with error indicator
            lines.append("gatewayz_data_scrape_success 0")
            lines.append(f'# Error: {str(e)}')

        # Join with newlines and add trailing newline
        content = "\n".join(lines) + "\n"

        return Response(
            content=content,
            media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    except Exception as e:
        logger.error(f"Failed to generate prometheus metrics: {e}", exc_info=True)
        return Response(
            content=f"# Error generating metrics: {str(e)}\ngatewayz_data_scrape_success 0\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
            status_code=500
        )


# ============================================================================
# Health Score Endpoints
# ============================================================================

@router.get("/health-scores")
async def get_health_scores(
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get health scores for all providers.

    Returns health metrics suitable for Grafana dashboard panels:
    - Health score (0-100)
    - Status (healthy/degraded/unhealthy)
    - Availability percentage
    - Error rate
    - Average latency

    Cache TTL: 30 seconds
    """
    cache_key = "health_scores"
    ttl = CACHE_TTL[cache_key]

    # Check cache first
    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.redis_metrics import get_redis_metrics

        redis_metrics = get_redis_metrics()
        health_scores = await redis_metrics.get_all_provider_health()

        results = []
        for provider, score in health_scores.items():
            # Get additional metrics for each provider
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=1)

            # Calculate aggregated stats
            total_requests = sum(h.get("total_requests", 0) for h in hourly_stats.values())
            total_errors = sum(h.get("error_count", 0) for h in hourly_stats.values())
            total_latency = sum(h.get("total_latency", 0) for h in hourly_stats.values())

            error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0.0
            avg_latency = (total_latency / total_requests) if total_requests > 0 else 0.0
            availability = 100.0 - error_rate

            results.append({
                "provider": provider,
                "health_score": round(score, 2),
                "status": _classify_health_status(score),
                "availability": round(availability, 2),
                "error_rate": round(error_rate, 2),
                "avg_latency_ms": round(avg_latency, 2),
                "requests_last_hour": total_requests,
                "last_updated": datetime.now(timezone.utc).isoformat()
            })

        # Sort by health score (worst first for attention)
        results.sort(key=lambda x: x["health_score"])

        response = {
            "data": results,
            "count": len(results),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Cache the result
        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get health scores: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get health scores: {str(e)}")


# ============================================================================
# Circuit Breaker Endpoints
# ============================================================================

@router.get("/circuit-breakers")
async def get_circuit_breakers(
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get circuit breaker states for all provider/model combinations.

    Returns data formatted for Grafana table panels:
    - Provider and model names
    - Status (CLOSED/OPEN/HALF_OPEN)
    - Request and failure counts
    - Availability status

    Cache TTL: 15 seconds (circuit states can change rapidly)
    """
    cache_key = "circuit_breakers"
    ttl = CACHE_TTL[cache_key]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.model_availability import availability_service

        circuit_states = []

        for provider_key, circuit_data in availability_service.circuit_breakers.items():
            parts = provider_key.split(":", 1)
            if len(parts) != 2:
                continue

            provider, model = parts

            # Calculate failure rate
            total = circuit_data.success_count + circuit_data.failure_count
            failure_rate = (circuit_data.failure_count / total * 100) if total > 0 else 0.0

            last_failure = None
            if circuit_data.last_failure_time:
                last_failure = datetime.fromtimestamp(
                    circuit_data.last_failure_time,
                    tz=timezone.utc
                ).isoformat()

            circuit_states.append({
                "provider": provider,
                "model": model,
                "status": circuit_data.state.name,
                "requests_last_5m": total,
                "failures_last_5m": circuit_data.failure_count,
                "failure_rate": round(failure_rate, 2),
                "is_available": availability_service.is_model_available(model, provider),
                "last_failure_time": last_failure
            })

        # Sort by status priority (OPEN first)
        status_priority = {"OPEN": 0, "HALF_OPEN": 1, "CLOSED": 2}
        circuit_states.sort(key=lambda x: (status_priority.get(x["status"], 3), -x["failure_rate"]))

        response = {
            "data": circuit_states,
            "count": len(circuit_states),
            "open_count": sum(1 for c in circuit_states if c["status"] == "OPEN"),
            "half_open_count": sum(1 for c in circuit_states if c["status"] == "HALF_OPEN"),
            "closed_count": sum(1 for c in circuit_states if c["status"] == "CLOSED"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get circuit breakers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get circuit breakers: {str(e)}")


# ============================================================================
# Anomaly Detection Endpoints
# ============================================================================

@router.get("/anomalies")
async def get_anomalies(
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get detected anomalies and active alerts.

    Returns anomalies formatted for Grafana alert panels:
    - Type (high_error_rate, latency_spike, cost_spike)
    - Severity (CRITICAL, WARNING, INFO)
    - Description with context
    - Current value vs threshold

    Cache TTL: 30 seconds
    """
    cache_key = "anomalies"
    ttl = CACHE_TTL[cache_key]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.analytics import get_analytics_service

        analytics = get_analytics_service()
        raw_anomalies = await analytics.detect_anomalies()

        anomalies = []
        for anomaly in raw_anomalies:
            anomalies.append({
                "type": anomaly.get("type", "unknown"),
                "severity": anomaly.get("severity", "WARNING").upper(),
                "provider": anomaly.get("provider"),
                "model": anomaly.get("model"),
                "description": anomaly.get("description", "Anomaly detected"),
                "current_value": float(anomaly.get("current_value", 0)),
                "threshold": float(anomaly.get("threshold", 0)),
                "detected_at": anomaly.get("detected_at", datetime.now(timezone.utc).isoformat())
            })

        # Sort by severity
        severity_priority = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        anomalies.sort(key=lambda x: severity_priority.get(x["severity"], 3))

        response = {
            "data": anomalies,
            "count": len(anomalies),
            "critical_count": sum(1 for a in anomalies if a["severity"] == "CRITICAL"),
            "warning_count": sum(1 for a in anomalies if a["severity"] == "WARNING"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get anomalies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get anomalies: {str(e)}")


# ============================================================================
# Cost Analysis Endpoints
# ============================================================================

@router.get("/cost-analysis")
async def get_cost_analysis(
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get cost breakdown by model and provider.

    Returns cost metrics for Grafana cost dashboards:
    - Total cost per model
    - Request count
    - Cost per request
    - Token usage

    Cache TTL: 5 minutes (cost data is relatively stable)
    """
    cache_key = f"cost_analysis:{days}d"
    ttl = CACHE_TTL["cost_analysis"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.analytics import get_analytics_service

        analytics = get_analytics_service()
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        cost_data = await analytics.get_cost_by_provider(start_date, end_date)

        results = []
        total_cost = 0.0
        total_requests = 0

        for provider, provider_data in cost_data.get("providers", {}).items():
            for model, model_data in provider_data.get("models", {}).items():
                cost = model_data.get("cost", 0)
                requests = model_data.get("requests", 0)
                tokens = model_data.get("tokens", 0)

                total_cost += cost
                total_requests += requests

                results.append({
                    "model": model,
                    "provider": provider,
                    "cost": round(cost, 4),
                    "requests": requests,
                    "cost_per_request": round(cost / requests, 6) if requests > 0 else 0,
                    "tokens_used": tokens,
                    "cost_per_1k_tokens": round((cost / tokens * 1000), 6) if tokens > 0 else 0
                })

        # Sort by cost
        results.sort(key=lambda x: x["cost"], reverse=True)

        response = {
            "data": results,
            "count": len(results),
            "period_days": days,
            "total_cost": round(total_cost, 4),
            "total_requests": total_requests,
            "avg_cost_per_request": round(total_cost / total_requests, 6) if total_requests > 0 else 0,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get cost analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get cost analysis: {str(e)}")


# ============================================================================
# Error Rate Endpoints
# ============================================================================

@router.get("/error-rates")
async def get_error_rates(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get error rates broken down by model.

    Returns error metrics for Grafana error tracking panels.

    Cache TTL: 1 minute
    """
    cache_key = f"error_rates:{hours}h"
    ttl = CACHE_TTL["error_rates"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.analytics import get_analytics_service

        analytics = get_analytics_service()
        error_data = await analytics.get_error_rate_by_model(hours=hours)

        results = []
        for model, model_data in error_data.get("models", {}).items():
            provider = model_data.get("provider", "unknown")
            total_requests = model_data.get("total_requests", 0)
            failed_requests = model_data.get("failed_requests", 0)
            error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0

            results.append({
                "model": model,
                "provider": provider,
                "total_requests": total_requests,
                "failed_requests": failed_requests,
                "error_rate": round(error_rate, 2),
                "error_types": model_data.get("error_types", {})
            })

        # Sort by error rate
        results.sort(key=lambda x: x["error_rate"], reverse=True)

        response = {
            "data": results,
            "count": len(results),
            "period_hours": hours,
            "overall_error_rate": round(
                sum(r["failed_requests"] for r in results) /
                max(sum(r["total_requests"] for r in results), 1) * 100, 2
            ),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get error rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get error rates: {str(e)}")


# ============================================================================
# Chat Request Summary Endpoints
# ============================================================================

@router.get("/chat-requests/summary")
async def get_chat_requests_summary(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get summarized chat completion request metrics.

    Cache TTL: 30 seconds
    """
    cache_key = f"chat_summary:{hours}h"
    ttl = CACHE_TTL["chat_summary"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.redis_metrics import get_redis_metrics

        redis_metrics = get_redis_metrics()

        total_requests = 0
        total_success = 0
        total_latency = 0
        total_tokens = 0
        model_requests: dict[str, int] = {}

        health_scores = await redis_metrics.get_all_provider_health()

        for provider in health_scores.keys():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=hours)

            for hour_data in hourly_stats.values():
                requests = hour_data.get("total_requests", 0)
                total_requests += requests
                total_success += hour_data.get("success_count", requests)
                total_latency += hour_data.get("total_latency", 0)
                total_tokens += hour_data.get("total_tokens", 0)

                for model, count in hour_data.get("model_requests", {}).items():
                    model_requests[model] = model_requests.get(model, 0) + count

        success_rate = (total_success / total_requests * 100) if total_requests > 0 else 100.0
        avg_latency = (total_latency / total_requests) if total_requests > 0 else 0.0
        requests_per_minute = total_requests / (hours * 60) if hours > 0 else 0.0
        tokens_per_second = total_tokens / (hours * 3600) if hours > 0 else 0.0

        top_models = sorted(model_requests.items(), key=lambda x: x[1], reverse=True)[:5]

        response = {
            "period_hours": hours,
            "total_requests": total_requests,
            "success_rate": round(success_rate, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "total_tokens": total_tokens,
            "tokens_per_second": round(tokens_per_second, 2),
            "requests_per_minute": round(requests_per_minute, 2),
            "top_models": [{"model": m, "requests": c} for m, c in top_models],
            "avg_health_score": round(
                sum(health_scores.values()) / len(health_scores) if health_scores else 0, 2
            ),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get chat requests summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get summary: {str(e)}")


# ============================================================================
# Real-time Stats Endpoints
# ============================================================================

@router.get("/stats/realtime")
async def get_realtime_stats(
    hours: int = Query(1, ge=1, le=24, description="Number of hours to look back"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get real-time statistics for live Grafana dashboards.

    Cache TTL: 15 seconds (should be fairly fresh for "realtime")
    """
    cache_key = f"realtime_stats:{hours}h"
    ttl = CACHE_TTL["realtime_stats"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.redis_metrics import get_redis_metrics

        redis_metrics = get_redis_metrics()
        health_scores = await redis_metrics.get_all_provider_health()

        total_requests = 0
        total_cost = 0.0
        total_errors = 0
        provider_stats = {}

        for provider, score in health_scores.items():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=hours)

            provider_requests = sum(h.get("total_requests", 0) for h in hourly_stats.values())
            provider_cost = sum(h.get("total_cost", 0.0) for h in hourly_stats.values())
            provider_errors = sum(h.get("error_count", 0) for h in hourly_stats.values())
            provider_latency = sum(h.get("total_latency", 0) for h in hourly_stats.values())

            total_requests += provider_requests
            total_cost += provider_cost
            total_errors += provider_errors

            provider_stats[provider] = {
                "health_score": round(score, 2),
                "status": _classify_health_status(score),
                "requests": provider_requests,
                "cost": round(provider_cost, 4),
                "error_rate": round(
                    (provider_errors / provider_requests * 100) if provider_requests > 0 else 0, 2
                ),
                "avg_latency_ms": round(
                    (provider_latency / provider_requests) if provider_requests > 0 else 0, 2
                )
            }

        avg_health = sum(health_scores.values()) / len(health_scores) if health_scores else 0
        error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0

        response = {
            "period_hours": hours,
            "avg_health_score": round(avg_health, 2),
            "total_requests": total_requests,
            "total_cost": round(total_cost, 4),
            "error_rate": round(error_rate, 2),
            "requests_per_hour": round(total_requests / hours, 2) if hours > 0 else 0,
            "providers": provider_stats,
            "provider_count": len(provider_stats),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get realtime stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get realtime stats: {str(e)}")


# ============================================================================
# Provider Health Scorecard Endpoints
# ============================================================================

@router.get("/provider-health")
async def get_provider_health_scorecard(
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get detailed provider health scorecard.

    Cache TTL: 30 seconds
    """
    cache_key = "provider_health"
    ttl = CACHE_TTL[cache_key]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.services.redis_metrics import get_redis_metrics
        from src.services.model_availability import availability_service

        redis_metrics = get_redis_metrics()
        health_scores = await redis_metrics.get_all_provider_health()

        providers = []

        for provider, score in health_scores.items():
            current_stats = await redis_metrics.get_hourly_stats(provider, hours=1)
            historical_stats = await redis_metrics.get_hourly_stats(provider, hours=24)

            current_requests = sum(h.get("total_requests", 0) for h in current_stats.values())
            current_errors = sum(h.get("error_count", 0) for h in current_stats.values())
            current_latency = sum(h.get("total_latency", 0) for h in current_stats.values())
            hist_requests = sum(h.get("total_requests", 0) for h in historical_stats.values())

            active_models = sum(
                1 for key in availability_service.circuit_breakers.keys()
                if key.startswith(f"{provider}:")
            )

            error_rate = (current_errors / current_requests * 100) if current_requests > 0 else 0
            avg_latency = (current_latency / current_requests) if current_requests > 0 else 0

            avg_hourly = hist_requests / 24 if hist_requests > 0 else 0
            trend = "stable"
            if current_requests > avg_hourly * 1.2:
                trend = "up"
            elif current_requests < avg_hourly * 0.8:
                trend = "down"

            providers.append({
                "provider": provider,
                "health_score": round(score, 2),
                "status": _classify_health_status(score),
                "trend": trend,
                "availability": round(100 - error_rate, 2),
                "error_rate": round(error_rate, 2),
                "avg_latency_ms": round(avg_latency, 2),
                "requests_last_hour": current_requests,
                "requests_last_24h": hist_requests,
                "active_models": active_models,
                "last_updated": datetime.now(timezone.utc).isoformat()
            })

        providers.sort(key=lambda x: x["health_score"], reverse=True)

        response = {
            "providers": providers,
            "total_providers": len(providers),
            "healthy_count": sum(1 for p in providers if p["status"] == "healthy"),
            "degraded_count": sum(1 for p in providers if p["status"] == "degraded"),
            "unhealthy_count": sum(1 for p in providers if p["status"] == "unhealthy"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get provider health scorecard: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get scorecard: {str(e)}")


# ============================================================================
# Instrumentation Status Endpoints (NO CACHING - must be live)
# ============================================================================

@router.get("/instrumentation/health")
async def get_instrumentation_health(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get overall instrumentation and observability health.

    Checks connectivity to Redis, Supabase, Prometheus, Loki, Tempo.

    NO CACHING - this is a live health check.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        health_status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "healthy",
            "components": {}
        }

        # Check Redis
        try:
            redis = await _get_redis_client()
            if redis:
                await redis.ping()
                health_status["components"]["redis"] = {"status": "healthy"}
            else:
                health_status["components"]["redis"] = {"status": "unavailable"}
        except Exception as e:
            health_status["components"]["redis"] = {"status": "unhealthy", "error": str(e)}
            health_status["status"] = "degraded"

        # Check Supabase
        try:
            client = get_supabase_client()
            client.table("providers").select("id").limit(1).execute()
            health_status["components"]["supabase"] = {"status": "healthy"}
        except Exception as e:
            health_status["components"]["supabase"] = {"status": "unhealthy", "error": str(e)}
            health_status["status"] = "degraded"

        # Check Prometheus metrics
        try:
            from prometheus_client import REGISTRY, generate_latest
            metrics = generate_latest(REGISTRY)
            health_status["components"]["prometheus"] = {
                "status": "healthy",
                "metrics_count": len(metrics.decode().split("\n"))
            }
        except Exception as e:
            health_status["components"]["prometheus"] = {"status": "unhealthy", "error": str(e)}

        # Check Loki
        loki_url = os.environ.get("LOKI_URL")
        if loki_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{loki_url}/ready")
                    health_status["components"]["loki"] = {
                        "status": "healthy" if response.status_code == 200 else "degraded",
                        "url": loki_url
                    }
            except Exception as e:
                health_status["components"]["loki"] = {"status": "unhealthy", "error": str(e)}
        else:
            health_status["components"]["loki"] = {"status": "not_configured"}

        # Check Tempo
        tempo_url = os.environ.get("TEMPO_URL")
        if tempo_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{tempo_url}/ready")
                    health_status["components"]["tempo"] = {
                        "status": "healthy" if response.status_code == 200 else "degraded",
                        "url": tempo_url
                    }
            except Exception as e:
                health_status["components"]["tempo"] = {"status": "unhealthy", "error": str(e)}
        else:
            health_status["components"]["tempo"] = {"status": "not_configured"}

        return health_status

    except Exception as e:
        logger.error(f"Failed to get instrumentation health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")


@router.get("/instrumentation/loki/status")
async def get_loki_status(api_key: str | None = Depends(get_optional_api_key)):
    """Get Loki logging service status (NO CACHING)."""
    loki_url = os.environ.get("LOKI_URL", os.environ.get("LOKI_HOST"))

    if not loki_url:
        return {
            "status": "not_configured",
            "message": "LOKI_URL environment variable not set",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            ready_response = await client.get(f"{loki_url}/ready")
            return {
                "status": "healthy" if ready_response.status_code == 200 else "unhealthy",
                "url": loki_url,
                "ready": ready_response.status_code == 200,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@router.get("/instrumentation/tempo/status")
async def get_tempo_status(api_key: str | None = Depends(get_optional_api_key)):
    """Get Tempo tracing service status (NO CACHING)."""
    tempo_url = os.environ.get("TEMPO_URL", os.environ.get("TEMPO_HOST"))

    if not tempo_url:
        return {
            "status": "not_configured",
            "message": "TEMPO_URL environment variable not set",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            ready_response = await client.get(f"{tempo_url}/ready")
            return {
                "status": "healthy" if ready_response.status_code == 200 else "unhealthy",
                "url": tempo_url,
                "ready": ready_response.status_code == 200,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@router.post("/instrumentation/test-log")
async def test_log_ingestion(
    message: str = Query("Test log message", description="Test message to log"),
    level: str = Query("info", description="Log level (debug, info, warn, error)"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """Test log ingestion to Loki (NO CACHING)."""
    trace_id = str(uuid.uuid4())

    log_func = getattr(logger, level.lower(), logger.info)
    log_func(f"[TEST LOG] {message}", extra={
        "trace_id": trace_id,
        "test": True,
        "source": "prometheus_data_test"
    })

    return {
        "success": True,
        "message": f"Test log sent at level '{level}'",
        "trace_id": trace_id,
        "log_message": message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/instrumentation/test-trace")
async def test_trace_ingestion(
    operation: str = Query("test_operation", description="Operation name for the trace"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """Test trace ingestion to Tempo (NO CACHING)."""
    trace_id = str(uuid.uuid4())
    span_id = str(uuid.uuid4())[:16]

    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind

        tracer = trace.get_tracer(__name__)

        with tracer.start_as_current_span(
            operation,
            kind=SpanKind.INTERNAL,
            attributes={"test": True, "source": "prometheus_data_test"}
        ) as span:
            span.set_attribute("test.trace_id", trace_id)

            return {
                "success": True,
                "message": "Test trace span created",
                "trace_id": trace_id,
                "span_id": span_id,
                "operation": operation,
                "method": "opentelemetry",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    except ImportError:
        return {
            "success": True,
            "message": "Test trace simulated (OpenTelemetry not configured)",
            "trace_id": trace_id,
            "span_id": span_id,
            "operation": operation,
            "method": "simulated",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


# ============================================================================
# Model Performance Endpoints
# ============================================================================

@router.get("/model-performance")
async def get_model_performance(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of models to return"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get performance metrics for all models.

    Cache TTL: 1 minute
    """
    cache_key = f"model_performance:{limit}"
    ttl = CACHE_TTL["model_performance"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Get recent requests with pagination to avoid memory issues
        result = client.table("chat_completion_requests").select(
            """
            model_id,
            input_tokens,
            output_tokens,
            processing_time_ms,
            status,
            models!inner(
                id,
                model_id,
                model_name,
                providers!inner(name, slug)
            )
            """
        ).order("created_at", desc=True).limit(10000).execute()

        if not result.data:
            response = {
                "models": [],
                "total_models": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await _set_cached_data(cache_key, response, ttl)
            return _add_cache_info(response, cache_key, ttl, from_cache=False)

        # Aggregate by model
        model_stats: dict[str, dict[str, Any]] = {}

        for record in result.data:
            model_info = record.get("models", {})
            model_id = model_info.get("model_id", "unknown")

            if model_id not in model_stats:
                model_stats[model_id] = {
                    "model_id": model_id,
                    "model_name": model_info.get("model_name", model_id),
                    "provider": model_info.get("providers", {}).get("name", "unknown"),
                    "provider_slug": model_info.get("providers", {}).get("slug", "unknown"),
                    "total_requests": 0,
                    "successful_requests": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_latency_ms": 0
                }

            stats = model_stats[model_id]
            stats["total_requests"] += 1
            if record.get("status") == "success":
                stats["successful_requests"] += 1
            stats["total_input_tokens"] += record.get("input_tokens", 0) or 0
            stats["total_output_tokens"] += record.get("output_tokens", 0) or 0
            stats["total_latency_ms"] += record.get("processing_time_ms", 0) or 0

        # Calculate derived metrics
        models = []
        for model_id, stats in model_stats.items():
            total = stats["total_requests"]
            if total == 0:
                continue

            models.append({
                "model_id": stats["model_id"],
                "model_name": stats["model_name"],
                "provider": stats["provider"],
                "provider_slug": stats["provider_slug"],
                "total_requests": total,
                "success_rate": round(stats["successful_requests"] / total * 100, 2),
                "avg_latency_ms": round(stats["total_latency_ms"] / total, 2),
                "total_tokens": stats["total_input_tokens"] + stats["total_output_tokens"],
                "avg_tokens_per_request": round(
                    (stats["total_input_tokens"] + stats["total_output_tokens"]) / total, 2
                )
            })

        models.sort(key=lambda x: x["total_requests"], reverse=True)

        response = {
            "models": models[:limit],
            "total_models": len(models),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get model performance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get performance: {str(e)}")


@router.get("/trending-models")
async def get_trending_models(
    limit: int = Query(15, ge=1, le=50, description="Maximum number of models"),
    time_range: str = Query("7d", description="Time range (1d, 7d, 30d)"),
    skip_cache: bool = Query(False, description="Skip cache and fetch fresh data"),
    api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get top trending models by request volume.

    Cache TTL: 2 minutes
    """
    cache_key = f"trending_models:{time_range}:{limit}"
    ttl = CACHE_TTL["trending_models"]

    if not skip_cache:
        cached = await _get_cached_data(cache_key)
        if cached:
            return _add_cache_info(cached, cache_key, ttl, from_cache=True)

    try:
        from src.config.supabase_config import get_supabase_client

        days = {"1d": 1, "7d": 7, "30d": 30}.get(time_range, 7)
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        client = get_supabase_client()

        result = client.table("chat_completion_requests").select(
            """
            model_id,
            models!inner(
                model_id,
                model_name,
                providers!inner(name, slug)
            )
            """
        ).gte("created_at", start_date).execute()

        if not result.data:
            response = {
                "time_range": time_range,
                "models": [],
                "total_requests": 0,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await _set_cached_data(cache_key, response, ttl)
            return _add_cache_info(response, cache_key, ttl, from_cache=False)

        # Count by model
        model_counts: dict[str, dict[str, Any]] = {}
        total_requests = 0

        for record in result.data:
            model_info = record.get("models", {})
            model_id = model_info.get("model_id", "unknown")

            if model_id not in model_counts:
                model_counts[model_id] = {
                    "model_id": model_id,
                    "model_name": model_info.get("model_name", model_id),
                    "provider": model_info.get("providers", {}).get("name", "unknown"),
                    "provider_slug": model_info.get("providers", {}).get("slug", "unknown"),
                    "requests": 0
                }

            model_counts[model_id]["requests"] += 1
            total_requests += 1

        models = []
        for model_id, data in model_counts.items():
            data["usage_share"] = round(data["requests"] / total_requests * 100, 2) if total_requests > 0 else 0
            models.append(data)

        models.sort(key=lambda x: x["requests"], reverse=True)

        response = {
            "time_range": time_range,
            "models": models[:limit],
            "total_requests": total_requests,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        await _set_cached_data(cache_key, response, ttl)

        return _add_cache_info(response, cache_key, ttl, from_cache=False)

    except Exception as e:
        logger.error(f"Failed to get trending models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get trending models: {str(e)}")
