"""
Monitoring API endpoints - Exposes real-time metrics, health, and analytics.

This module provides REST API endpoints for accessing:
- Provider health scores and status
- Recent errors and failures
- Real-time statistics (Redis)
- Circuit breaker states
- Provider comparison and analytics
- Anomaly detection

Endpoints:
- GET /api/monitoring/health - All provider health scores
- GET /api/monitoring/health/{provider} - Specific provider health
- GET /api/monitoring/errors/{provider} - Recent errors for a provider
- GET /api/monitoring/stats/realtime - Real-time statistics (last hour)
- GET /api/monitoring/stats/hourly/{provider} - Hourly stats for a provider
- GET /api/monitoring/circuit-breakers - All circuit breaker states
- GET /api/monitoring/circuit-breakers/{provider} - Provider circuit breakers
- GET /api/monitoring/providers/comparison - Compare all providers
- GET /api/monitoring/latency/{provider}/{model} - Latency percentiles
- GET /api/monitoring/anomalies - Detected anomalies
- GET /api/monitoring/trial-analytics - Trial funnel metrics
- GET /api/monitoring/cost-analysis - Cost breakdown by provider
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.services.analytics import get_analytics_service
from src.services.model_availability import availability_service
from src.services.redis_metrics import get_redis_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


# Response Models
class HealthResponse(BaseModel):
    """Provider health response"""
    provider: str
    health_score: float = Field(..., ge=0, le=100, description="Health score 0-100")
    status: str = Field(..., description="healthy, degraded, unhealthy")
    last_updated: str | None = None


class ErrorResponse(BaseModel):
    """Error entry response"""
    model: str
    error: str
    timestamp: float
    latency_ms: int


class CircuitBreakerResponse(BaseModel):
    """Circuit breaker state response"""
    provider: str
    model: str
    state: str = Field(..., description="CLOSED, OPEN, HALF_OPEN")
    failure_count: int
    is_available: bool
    last_updated: float


class RealtimeStatsResponse(BaseModel):
    """Real-time statistics response"""
    timestamp: str
    providers: dict[str, dict[str, Any]]
    total_requests: int
    total_cost: float
    avg_health_score: float


class LatencyPercentilesResponse(BaseModel):
    """Latency percentiles response"""
    provider: str
    model: str
    count: int
    avg: float
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None


# Endpoints
@router.get("/health", response_model=list[HealthResponse])
async def get_all_provider_health():
    """
    Get health scores for all providers.

    Returns a list of provider health scores (0-100) with status classification.
    """
    try:
        redis_metrics = get_redis_metrics()
        health_scores = await redis_metrics.get_all_provider_health()

        results = []
        for provider, score in health_scores.items():
            # Classify health status
            if score >= 80:
                status = "healthy"
            elif score >= 50:
                status = "degraded"
            else:
                status = "unhealthy"

            results.append(HealthResponse(
                provider=provider,
                health_score=score,
                status=status,
                last_updated=datetime.now(timezone.utc).isoformat()
            ))

        return results
    except Exception as e:
        logger.error(f"Failed to get provider health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get health data: {str(e)}")


@router.get("/health/{provider}", response_model=HealthResponse)
async def get_provider_health(provider: str):
    """
    Get health score for a specific provider.

    Args:
        provider: Provider name (e.g., "openrouter", "portkey")
    """
    try:
        redis_metrics = get_redis_metrics()
        score = await redis_metrics.get_provider_health(provider)

        # Classify health status
        if score >= 80:
            status = "healthy"
        elif score >= 50:
            status = "degraded"
        else:
            status = "unhealthy"

        return HealthResponse(
            provider=provider,
            health_score=score,
            status=status,
            last_updated=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"Failed to get health for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get health data: {str(e)}")


@router.get("/errors/{provider}", response_model=list[ErrorResponse])
async def get_provider_errors(
    provider: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of errors to return")
):
    """
    Get recent errors for a specific provider.

    Args:
        provider: Provider name
        limit: Maximum number of errors (default: 100, max: 1000)
    """
    try:
        redis_metrics = get_redis_metrics()
        errors = await redis_metrics.get_recent_errors(provider, limit=limit)

        return [ErrorResponse(**error) for error in errors]
    except Exception as e:
        logger.error(f"Failed to get errors for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get error data: {str(e)}")


@router.get("/stats/realtime", response_model=RealtimeStatsResponse)
async def get_realtime_stats(
    hours: int = Query(1, ge=1, le=24, description="Number of hours to look back")
):
    """
    Get real-time statistics from Redis for all providers.

    Args:
        hours: Number of hours to look back (default: 1, max: 24)
    """
    try:
        redis_metrics = get_redis_metrics()

        # Get all provider health scores
        health_scores = await redis_metrics.get_all_provider_health()

        # Build provider stats
        provider_stats = {}
        total_requests = 0
        total_cost = 0.0

        for provider in health_scores.keys():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=hours)

            # Aggregate across hours
            provider_total_requests = sum(
                hour_data.get("total_requests", 0) for hour_data in hourly_stats.values()
            )
            provider_total_cost = sum(
                hour_data.get("total_cost", 0.0) for hour_data in hourly_stats.values()
            )

            provider_stats[provider] = {
                "total_requests": provider_total_requests,
                "total_cost": provider_total_cost,
                "health_score": health_scores[provider],
                "hourly_breakdown": hourly_stats
            }

            total_requests += provider_total_requests
            total_cost += provider_total_cost

        # Calculate average health score
        avg_health = sum(health_scores.values()) / len(health_scores) if health_scores else 0.0

        return RealtimeStatsResponse(
            timestamp=datetime.now(timezone.utc).isoformat(),
            providers=provider_stats,
            total_requests=total_requests,
            total_cost=total_cost,
            avg_health_score=avg_health
        )
    except Exception as e:
        logger.error(f"Failed to get realtime stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/stats/hourly/{provider}")
async def get_hourly_stats(
    provider: str,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to look back")
):
    """
    Get hourly statistics for a specific provider.

    Args:
        provider: Provider name
        hours: Number of hours to look back (default: 24, max: 168 = 1 week)
    """
    try:
        redis_metrics = get_redis_metrics()
        stats = await redis_metrics.get_hourly_stats(provider, hours=hours)

        return {
            "provider": provider,
            "hours": hours,
            "data": stats
        }
    except Exception as e:
        logger.error(f"Failed to get hourly stats for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get hourly stats: {str(e)}")


@router.get("/circuit-breakers", response_model=list[CircuitBreakerResponse])
async def get_all_circuit_breakers():
    """
    Get circuit breaker states for all provider/model combinations.

    Returns the current circuit breaker state, availability, and failure counts.
    """
    try:
        # Get all models that have been tracked
        circuit_states = []

        # Access circuit breaker data from availability service
        for provider_key, circuit_data in availability_service.circuit_breakers.items():
            # Parse provider and model from key (format: "provider:model")
            parts = provider_key.split(":", 1)
            if len(parts) == 2:
                provider, model = parts

                circuit_states.append(CircuitBreakerResponse(
                    provider=provider,
                    model=model,
                    state=circuit_data.state.name,
                    failure_count=circuit_data.failure_count,
                    is_available=availability_service.is_model_available(model, provider),
                    last_updated=circuit_data.last_failure_time or 0.0
                ))

        return circuit_states
    except Exception as e:
        logger.error(f"Failed to get circuit breaker states: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get circuit breaker data: {str(e)}")


@router.get("/circuit-breakers/{provider}", response_model=list[CircuitBreakerResponse])
async def get_provider_circuit_breakers(provider: str):
    """
    Get circuit breaker states for a specific provider's models.

    Args:
        provider: Provider name
    """
    try:
        circuit_states = []

        # Filter by provider
        for provider_key, circuit_data in availability_service.circuit_breakers.items():
            parts = provider_key.split(":", 1)
            if len(parts) == 2 and parts[0] == provider:
                model = parts[1]

                circuit_states.append(CircuitBreakerResponse(
                    provider=provider,
                    model=model,
                    state=circuit_data.state.name,
                    failure_count=circuit_data.failure_count,
                    is_available=availability_service.is_model_available(model, provider),
                    last_updated=circuit_data.last_failure_time or 0.0
                ))

        if not circuit_states:
            raise HTTPException(status_code=404, detail=f"No circuit breaker data found for provider: {provider}")

        return circuit_states
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get circuit breakers for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get circuit breaker data: {str(e)}")


@router.get("/providers/comparison")
async def get_provider_comparison():
    """
    Compare all providers across key metrics.

    Returns side-by-side comparison of providers including:
    - Total requests and success rates
    - Average latency
    - Total cost
    - Error rates
    - Health scores
    """
    try:
        analytics = get_analytics_service()
        providers = await analytics.get_provider_comparison()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "providers": providers,
            "total_providers": len(providers)
        }
    except Exception as e:
        logger.error(f"Failed to get provider comparison: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get comparison data: {str(e)}")


@router.get("/latency/{provider}/{model}", response_model=LatencyPercentilesResponse)
async def get_latency_percentiles(
    provider: str,
    model: str,
    percentiles: str = Query("50,95,99", description="Comma-separated percentiles (e.g., 50,95,99)")
):
    """
    Get latency percentiles for a specific provider/model combination.

    Args:
        provider: Provider name
        model: Model ID
        percentiles: Comma-separated percentiles (e.g., "50,95,99")
    """
    try:
        # Parse percentiles
        percentile_list = [int(p.strip()) for p in percentiles.split(",")]

        redis_metrics = get_redis_metrics()
        stats = await redis_metrics.get_latency_percentiles(provider, model, percentiles=percentile_list)

        if not stats:
            raise HTTPException(
                status_code=404,
                detail=f"No latency data found for {provider}/{model}"
            )

        return LatencyPercentilesResponse(
            provider=provider,
            model=model,
            count=stats.get("count", 0),
            avg=stats.get("avg", 0.0),
            p50=stats.get("p50"),
            p95=stats.get("p95"),
            p99=stats.get("p99")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get latency percentiles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get latency data: {str(e)}")


@router.get("/anomalies")
async def get_anomalies():
    """
    Get detected anomalies in metrics.

    Detects:
    - Cost spikes (>200% of average)
    - Latency spikes (>200% of average)
    - High error rates (>10%)

    Returns list of anomalies with severity classification.
    """
    try:
        analytics = get_analytics_service()
        anomalies = await analytics.detect_anomalies()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "anomalies": anomalies,
            "total_count": len(anomalies),
            "critical_count": sum(1 for a in anomalies if a.get("severity") == "critical"),
            "warning_count": sum(1 for a in anomalies if a.get("severity") == "warning")
        }
    except Exception as e:
        logger.error(f"Failed to detect anomalies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to detect anomalies: {str(e)}")


@router.get("/trial-analytics")
async def get_trial_analytics():
    """
    Get trial funnel analytics.

    Returns metrics including:
    - Total signups
    - Trial activations
    - Conversions to paid
    - Conversion rates
    - Average time to conversion
    """
    try:
        analytics = get_analytics_service()
        trial_data = analytics.get_trial_analytics()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **trial_data
        }
    except Exception as e:
        logger.error(f"Failed to get trial analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get trial analytics: {str(e)}")


@router.get("/cost-analysis")
async def get_cost_analysis(
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze")
):
    """
    Get cost breakdown by provider.

    Args:
        days: Number of days to analyze (default: 7, max: 90)

    Returns cost breakdown including:
    - Total cost per provider
    - Cost per request
    - Total requests
    - Cost trends
    """
    try:
        from datetime import timedelta

        analytics = get_analytics_service()
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        cost_data = await analytics.get_cost_by_provider(start_date, end_date)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period_days": days,
            **cost_data
        }
    except Exception as e:
        logger.error(f"Failed to get cost analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get cost analysis: {str(e)}")


@router.get("/latency-trends/{provider}")
async def get_latency_trends(
    provider: str,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze")
):
    """
    Get latency trends for a provider over time.

    Args:
        provider: Provider name
        hours: Number of hours to analyze (default: 24, max: 168 = 1 week)

    Returns latency trends including:
    - Hourly average latency
    - P95 and P99 percentiles
    - Latency trends over time
    """
    try:
        analytics = get_analytics_service()
        trends = await analytics.get_latency_trends(provider, hours=hours)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **trends
        }
    except Exception as e:
        logger.error(f"Failed to get latency trends: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get latency trends: {str(e)}")


@router.get("/error-rates")
async def get_error_rates(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze")
):
    """
    Get error rates broken down by model.

    Args:
        hours: Number of hours to analyze (default: 24, max: 168 = 1 week)

    Returns error rate breakdown including:
    - Error rate per model
    - Total requests and failures
    - Provider information
    """
    try:
        analytics = get_analytics_service()
        error_data = await analytics.get_error_rate_by_model(hours=hours)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **error_data
        }
    except Exception as e:
        logger.error(f"Failed to get error rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get error rates: {str(e)}")


@router.get("/token-efficiency/{provider}/{model}")
async def get_token_efficiency(provider: str, model: str):
    """
    Get token efficiency metrics for a provider/model.

    Args:
        provider: Provider name
        model: Model ID

    Returns efficiency metrics including:
    - Cost per token
    - Tokens per request
    - Cost per request
    - Average input/output tokens
    """
    try:
        analytics = get_analytics_service()
        efficiency_data = await analytics.get_token_efficiency(provider, model)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **efficiency_data
        }
    except Exception as e:
        logger.error(f"Failed to get token efficiency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get token efficiency: {str(e)}")
