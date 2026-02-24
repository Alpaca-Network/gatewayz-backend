"""
Monitoring API endpoints - Exposes real-time metrics, health, and analytics.

This module provides REST API endpoints for accessing:
- Provider health scores and status
- Recent errors and failures
- Real-time statistics (Redis)
- Circuit breaker states
- Provider comparison and analytics
- Anomaly detection
- Chat completion request data for analytics and graphing

Endpoints:
- POST /monitoring - Sentry tunnel for frontend error tracking (bypasses ad blockers)
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
- GET /api/monitoring/chat-requests/providers - Get all providers with chat completion requests
- GET /api/monitoring/chat-requests/counts - Get request counts per model (lightweight)
- GET /api/monitoring/chat-requests/models - Get all models with chat completion requests
- GET /api/monitoring/chat-requests - Chat completion requests with flexible filtering

Note: These endpoints support optional authentication. If an API key is provided,
it will be validated. If not provided, public access is allowed with rate limiting.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from src.security.deps import get_optional_api_key
from src.services.analytics import get_analytics_service
from src.services.model_availability import availability_service
from src.services.redis_metrics import get_redis_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

# Separate router for Sentry tunnel at root /monitoring path
sentry_tunnel_router = APIRouter(tags=["sentry-tunnel"])

# Allowed Sentry hosts for security
ALLOWED_SENTRY_HOSTS = {
    "sentry.io",
    "o4510344966111232.ingest.us.sentry.io",  # From the error URL
    "ingest.sentry.io",
    "ingest.us.sentry.io",
}


def _is_valid_sentry_host(hostname: str | None) -> bool:
    """
    Validate that a hostname is a legitimate Sentry host.

    Uses exact matching or proper subdomain checking to prevent SSRF attacks
    via malicious domains like evil-sentry.io or malicioussentry.io.

    Args:
        hostname: The hostname to validate

    Returns:
        True if hostname is a valid Sentry host, False otherwise
    """
    if not hostname:
        return False

    for allowed in ALLOWED_SENTRY_HOSTS:
        # Exact match
        if hostname == allowed:
            return True
        # Valid subdomain (must have dot before the allowed domain)
        if hostname.endswith("." + allowed):
            return True

    return False


@sentry_tunnel_router.post("/monitoring")
async def sentry_tunnel(request: Request) -> Response:
    """
    Sentry tunnel endpoint for frontend error tracking.

    This endpoint acts as a proxy to forward Sentry events from the frontend
    to Sentry's ingestion endpoint. This helps bypass ad blockers that might
    block direct requests to sentry.io.

    The frontend Sentry SDK should be configured with:
    ```javascript
    Sentry.init({
      dsn: "your-dsn",
      tunnel: "/monitoring",
    });
    ```

    No authentication required - this is intentionally public to allow
    frontend error tracking without exposing API keys.
    """
    try:
        # Read the raw request body (Sentry envelope format)
        body = await request.body()

        if not body:
            logger.warning("Sentry tunnel: Empty request body")
            return Response(status_code=400, content="Empty request body")

        # Parse the envelope to extract the DSN
        # Sentry envelopes have a JSON header on the first line
        try:
            envelope_lines = body.split(b"\n")
            if not envelope_lines:
                return Response(status_code=400, content="Invalid envelope format")

            # First line contains the envelope header with DSN
            import json

            header = json.loads(envelope_lines[0])

            # Ensure header is a dict (not a list, string, number, etc.)
            if not isinstance(header, dict):
                logger.warning("Sentry tunnel: Envelope header is not a JSON object")
                return Response(status_code=400, content="Invalid envelope header")

            dsn = header.get("dsn")

            if not dsn:
                logger.warning("Sentry tunnel: No DSN in envelope header")
                return Response(status_code=400, content="No DSN in envelope")

            # Parse the DSN to get the project ID and host
            parsed_dsn = urlparse(dsn)
            sentry_host = parsed_dsn.hostname

            # Security check: Only allow forwarding to known Sentry hosts
            # Uses exact matching or proper subdomain checking to prevent SSRF
            if not _is_valid_sentry_host(sentry_host):
                logger.warning(f"Sentry tunnel: Blocked request to non-Sentry host: {sentry_host}")
                return Response(status_code=403, content="Invalid Sentry host")

            # Extract project ID from DSN path
            project_id = parsed_dsn.path.strip("/")

            # Construct the Sentry ingestion URL
            sentry_url = f"https://{sentry_host}/api/{project_id}/envelope/"

            logger.debug(f"Sentry tunnel: Forwarding to {sentry_url}")

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"Sentry tunnel: Failed to parse envelope header: {e}")
            return Response(status_code=400, content="Invalid envelope header")

        # Forward the envelope to Sentry
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                sentry_response = await client.post(
                    sentry_url,
                    content=body,
                    headers={
                        "Content-Type": "application/x-sentry-envelope",
                        "X-Forwarded-For": request.client.host if request.client else "",
                    },
                )

                # Return Sentry's response
                return Response(
                    status_code=sentry_response.status_code,
                    content=sentry_response.content,
                    headers={"Content-Type": "application/json"},
                )

            except httpx.TimeoutException:
                logger.error("Sentry tunnel: Timeout forwarding to Sentry")
                return Response(status_code=504, content="Sentry request timeout")

            except httpx.HTTPError as e:
                logger.error(f"Sentry tunnel: HTTP error forwarding to Sentry: {e}")
                return Response(status_code=502, content="Failed to forward to Sentry")

    except Exception as e:
        logger.error(f"Sentry tunnel: Unexpected error: {e}", exc_info=True)
        return Response(status_code=500, content="Internal server error")


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
async def get_all_provider_health(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get health scores for all providers.

    Returns a list of provider health scores (0-100) with status classification.

    Authentication: Optional. Provide API key for authenticated access.
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

            results.append(
                HealthResponse(
                    provider=provider,
                    health_score=score,
                    status=status,
                    last_updated=datetime.now(UTC).isoformat(),
                )
            )

        return results
    except Exception as e:
        logger.error(f"Failed to get provider health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get health data: {str(e)}")


@router.get("/health/{provider}", response_model=HealthResponse)
async def get_provider_health(provider: str, api_key: str | None = Depends(get_optional_api_key)):
    """
    Get health score for a specific provider.

    Args:
        provider: Provider name (e.g., "openrouter", "portkey")

    Authentication: Optional. Provide API key for authenticated access.
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
            last_updated=datetime.now(UTC).isoformat(),
        )
    except Exception as e:
        logger.error(f"Failed to get health for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get health data: {str(e)}")


@router.get("/errors/{provider}", response_model=list[ErrorResponse])
async def get_provider_errors(
    provider: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of errors to return"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get recent errors for a specific provider.

    Args:
        provider: Provider name
        limit: Maximum number of errors (default: 100, max: 1000)

    Authentication: Optional. Provide API key for authenticated access.
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
    hours: int = Query(1, ge=1, le=24, description="Number of hours to look back"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get real-time statistics from Redis for all providers.

    Args:
        hours: Number of hours to look back (default: 1, max: 24)

    Authentication: Optional. Provide API key for authenticated access.
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
                "hourly_breakdown": hourly_stats,
            }

            total_requests += provider_total_requests
            total_cost += provider_total_cost

        # Calculate average health score
        avg_health = sum(health_scores.values()) / len(health_scores) if health_scores else 0.0

        return RealtimeStatsResponse(
            timestamp=datetime.now(UTC).isoformat(),
            providers=provider_stats,
            total_requests=total_requests,
            total_cost=total_cost,
            avg_health_score=avg_health,
        )
    except Exception as e:
        logger.error(f"Failed to get realtime stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/stats/hourly/{provider}")
async def get_hourly_stats(
    provider: str,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to look back"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get hourly statistics for a specific provider.

    Args:
        provider: Provider name
        hours: Number of hours to look back (default: 24, max: 168 = 1 week)

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        redis_metrics = get_redis_metrics()
        stats = await redis_metrics.get_hourly_stats(provider, hours=hours)

        return {"provider": provider, "hours": hours, "data": stats}
    except Exception as e:
        logger.error(f"Failed to get hourly stats for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get hourly stats: {str(e)}")


@router.get("/circuit-breakers", response_model=list[CircuitBreakerResponse])
async def get_all_circuit_breakers(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get circuit breaker states for all provider/model combinations.

    Returns the current circuit breaker state, availability, and failure counts.

    Authentication: Optional. Provide API key for authenticated access.
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

                circuit_states.append(
                    CircuitBreakerResponse(
                        provider=provider,
                        model=model,
                        state=circuit_data.state.name,
                        failure_count=circuit_data.failure_count,
                        is_available=availability_service.is_model_available(model, provider),
                        last_updated=circuit_data.last_failure_time or 0.0,
                    )
                )

        return circuit_states
    except Exception as e:
        logger.error(f"Failed to get circuit breaker states: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get circuit breaker data: {str(e)}")


@router.get("/circuit-breakers/{provider}", response_model=list[CircuitBreakerResponse])
async def get_provider_circuit_breakers(
    provider: str, api_key: str | None = Depends(get_optional_api_key)
):
    """
    Get circuit breaker states for a specific provider's models.

    Args:
        provider: Provider name

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        circuit_states = []

        # Filter by provider
        for provider_key, circuit_data in availability_service.circuit_breakers.items():
            parts = provider_key.split(":", 1)
            if len(parts) == 2 and parts[0] == provider:
                model = parts[1]

                circuit_states.append(
                    CircuitBreakerResponse(
                        provider=provider,
                        model=model,
                        state=circuit_data.state.name,
                        failure_count=circuit_data.failure_count,
                        is_available=availability_service.is_model_available(model, provider),
                        last_updated=circuit_data.last_failure_time or 0.0,
                    )
                )

        if not circuit_states:
            raise HTTPException(
                status_code=404, detail=f"No circuit breaker data found for provider: {provider}"
            )

        return circuit_states
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get circuit breakers for {provider}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get circuit breaker data: {str(e)}")


@router.get("/providers/comparison")
async def get_provider_comparison(api_key: str | None = Depends(get_optional_api_key)):
    """
    Compare all providers across key metrics.

    Returns side-by-side comparison of providers including:
    - Total requests and success rates
    - Average latency
    - Total cost
    - Error rates
    - Health scores

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        providers = await analytics.get_provider_comparison()

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "providers": providers,
            "total_providers": len(providers),
        }
    except Exception as e:
        logger.error(f"Failed to get provider comparison: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get comparison data: {str(e)}")


@router.get("/latency/{provider}/{model}", response_model=LatencyPercentilesResponse)
async def get_latency_percentiles(
    provider: str,
    model: str,
    percentiles: str = Query(
        "50,95,99", description="Comma-separated percentiles (e.g., 50,95,99)"
    ),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get latency percentiles for a specific provider/model combination.

    Args:
        provider: Provider name
        model: Model ID
        percentiles: Comma-separated percentiles (e.g., "50,95,99")

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        # Parse percentiles
        percentile_list = [int(p.strip()) for p in percentiles.split(",")]

        redis_metrics = get_redis_metrics()
        stats = await redis_metrics.get_latency_percentiles(
            provider, model, percentiles=percentile_list
        )

        if not stats:
            raise HTTPException(
                status_code=404, detail=f"No latency data found for {provider}/{model}"
            )

        return LatencyPercentilesResponse(
            provider=provider,
            model=model,
            count=stats.get("count", 0),
            avg=stats.get("avg", 0.0),
            p50=stats.get("p50"),
            p95=stats.get("p95"),
            p99=stats.get("p99"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get latency percentiles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get latency data: {str(e)}")


@router.get("/anomalies")
async def get_anomalies(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get detected anomalies in metrics.

    Detects:
    - Cost spikes (>200% of average)
    - Latency spikes (>200% of average)
    - High error rates (>10%)

    Returns list of anomalies with severity classification.

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        anomalies = await analytics.detect_anomalies()

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "anomalies": anomalies,
            "total_count": len(anomalies),
            "critical_count": sum(1 for a in anomalies if a.get("severity") == "critical"),
            "warning_count": sum(1 for a in anomalies if a.get("severity") == "warning"),
        }
    except Exception as e:
        logger.error(f"Failed to detect anomalies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to detect anomalies: {str(e)}")


@router.get("/trial-analytics")
async def get_trial_analytics(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get trial funnel analytics.

    Returns metrics including:
    - Total signups
    - Trial activations
    - Conversions to paid
    - Conversion rates
    - Average time to conversion

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        trial_data = analytics.get_trial_analytics()

        return {"timestamp": datetime.now(UTC).isoformat(), **trial_data}
    except Exception as e:
        logger.error(f"Failed to get trial analytics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get trial analytics: {str(e)}")


@router.get("/cost-analysis")
async def get_cost_analysis(
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze"),
    api_key: str | None = Depends(get_optional_api_key),
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

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from datetime import timedelta

        analytics = get_analytics_service()
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=days)

        cost_data = await analytics.get_cost_by_provider(start_date, end_date)

        return {"timestamp": datetime.now(UTC).isoformat(), "period_days": days, **cost_data}
    except Exception as e:
        logger.error(f"Failed to get cost analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get cost analysis: {str(e)}")


@router.get("/latency-trends/{provider}")
async def get_latency_trends(
    provider: str,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze"),
    api_key: str | None = Depends(get_optional_api_key),
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

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        trends = await analytics.get_latency_trends(provider, hours=hours)

        return {"timestamp": datetime.now(UTC).isoformat(), **trends}
    except Exception as e:
        logger.error(f"Failed to get latency trends: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get latency trends: {str(e)}")


@router.get("/error-rates")
async def get_error_rates(
    hours: int = Query(24, ge=1, le=168, description="Number of hours to analyze"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get error rates broken down by model.

    Args:
        hours: Number of hours to analyze (default: 24, max: 168 = 1 week)

    Returns error rate breakdown including:
    - Error rate per model
    - Total requests and failures
    - Provider information

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        error_data = await analytics.get_error_rate_by_model(hours=hours)

        return {"timestamp": datetime.now(UTC).isoformat(), **error_data}
    except Exception as e:
        logger.error(f"Failed to get error rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get error rates: {str(e)}")


@router.get("/token-efficiency/{provider}/{model}")
async def get_token_efficiency(
    provider: str, model: str, api_key: str | None = Depends(get_optional_api_key)
):
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

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        analytics = get_analytics_service()
        efficiency_data = await analytics.get_token_efficiency(provider, model)

        return {"timestamp": datetime.now(UTC).isoformat(), **efficiency_data}
    except Exception as e:
        logger.error(f"Failed to get token efficiency: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get token efficiency: {str(e)}")


@router.get("/chat-requests/providers")
async def get_providers_with_requests(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get all providers that have models with chat completion requests.

    Returns a list of providers that have at least one model with chat completion requests.
    Useful for building provider selection UI.

    Returns:
    - List of providers with:
      - Provider information (id, name, slug)
      - Count of models with requests
      - Total requests across all models

    Example:
    - /api/monitoring/chat-requests/providers

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try to use optimized RPC function first (fastest)
        try:
            rpc_result = client.rpc("get_provider_request_stats").execute()
            if rpc_result.data:
                return {
                    "success": True,
                    "data": rpc_result.data,
                    "metadata": {
                        "total_providers": len(rpc_result.data),
                        "timestamp": datetime.now(UTC).isoformat(),
                        "method": "rpc",
                    },
                }
        except Exception as rpc_error:
            logger.debug(f"RPC function not available, using fallback method: {rpc_error}")

        # Fallback: Get distinct model_ids with their provider info (lightweight)
        # We only fetch unique model_id + provider combinations, not all requests
        distinct_result = client.table("chat_completion_requests").select("""
            model_id,
            models!inner(
                provider_id,
                providers!inner(
                    id,
                    name,
                    slug
                )
            )
            """).execute()

        if not distinct_result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_providers": 0,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "method": "fallback",
                },
            }

        # Build provider stats efficiently
        provider_stats = {}
        for record in distinct_result.data:
            model_id = record.get("model_id")
            if model_id is None:
                continue

            model_info = record.get("models", {})
            provider_info = model_info.get("providers", {})
            provider_id = provider_info.get("id")

            if provider_id is None:
                continue

            if provider_id not in provider_stats:
                provider_stats[provider_id] = {
                    "provider_id": provider_id,
                    "name": provider_info.get("name"),
                    "slug": provider_info.get("slug"),
                    "model_ids": set(),
                }

            provider_stats[provider_id]["model_ids"].add(model_id)

        # Now get counts efficiently per provider using count queries
        providers_list = []
        for provider_id, stats in provider_stats.items():
            # Get total request count for this provider using COUNT
            # Query only models from this provider
            model_ids_list = list(stats["model_ids"])

            # Count total requests for all models of this provider
            count_result = (
                client.table("chat_completion_requests")
                .select("id", count="exact", head=True)
                .in_("model_id", model_ids_list)
                .execute()
            )

            total_requests = count_result.count if count_result.count is not None else 0

            providers_list.append(
                {
                    "provider_id": stats["provider_id"],
                    "name": stats["name"],
                    "slug": stats["slug"],
                    "models_with_requests": len(stats["model_ids"]),
                    "total_requests": total_requests,
                }
            )

        # Sort by request count (most used first)
        providers_list.sort(key=lambda x: x["total_requests"], reverse=True)

        return {
            "success": True,
            "data": providers_list,
            "metadata": {
                "total_providers": len(providers_list),
                "timestamp": datetime.now(UTC).isoformat(),
                "method": "fallback_with_counts",
            },
        }

    except Exception as e:
        logger.error(f"Failed to get providers with requests: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get providers with requests: {str(e)}"
        )


@router.get("/chat-requests/counts")
async def get_request_counts_by_model(api_key: str | None = Depends(get_optional_api_key)):
    """
    Get request counts for each model (lightweight endpoint).

    Returns a simple count of how many requests each model has received.
    This is a lighter alternative to /chat-requests/models when you only need counts.

    Returns:
    - List of models with request counts, sorted by count (descending)
    - Each entry includes: model_id, model_name, provider name, request_count

    Example:
    - /api/monitoring/chat-requests/counts

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Get all requests with model info
        result = client.table("chat_completion_requests").select("""
            model_id,
            models!inner(
                id,
                model_name,
                model_id,
                providers!inner(
                    name,
                    slug
                )
            )
            """).execute()

        if not result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_models": 0,
                    "total_requests": 0,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            }

        # Count requests per model
        model_counts = {}
        for record in result.data:
            model_id = record.get("model_id")
            if model_id is None:
                continue

            model_info = record.get("models", {})

            if model_id not in model_counts:
                model_counts[model_id] = {
                    "model_id": model_id,
                    "model_name": model_info.get("model_name"),
                    "model_identifier": model_info.get("model_id"),
                    "provider_name": model_info.get("providers", {}).get("name"),
                    "provider_slug": model_info.get("providers", {}).get("slug"),
                    "request_count": 0,
                }

            model_counts[model_id]["request_count"] += 1

        # Convert to list and sort by count
        counts_list = list(model_counts.values())
        counts_list.sort(key=lambda x: x["request_count"], reverse=True)

        return {
            "success": True,
            "data": counts_list,
            "metadata": {
                "total_models": len(counts_list),
                "total_requests": sum(m["request_count"] for m in counts_list),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get request counts by model: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get request counts: {str(e)}")


@router.get("/chat-requests/models")
async def get_models_with_requests(
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get all unique models that have chat completion requests.

    Returns a list of all models that have been used in chat completion requests,
    along with basic statistics for each model.

    Query Parameters:
    - provider_id: Optional filter to only show models from a specific provider

    Returns:
    - List of models with:
      - Model information (id, name, provider)
      - Request count
      - Total tokens used
      - Average processing time

    Examples:
    - /api/monitoring/chat-requests/models (all models)
    - /api/monitoring/chat-requests/models?provider_id=5 (only models from provider 5)

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try to use optimized RPC function first (fastest)
        try:
            if provider_id is not None:
                # Use RPC with provider filter
                rpc_result = client.rpc(
                    "get_models_with_requests_by_provider", {"p_provider_id": provider_id}
                ).execute()
            else:
                # Use RPC for all models
                rpc_result = client.rpc("get_models_with_requests").execute()

            if rpc_result.data:
                return {
                    "success": True,
                    "data": rpc_result.data,
                    "metadata": {
                        "total_models": len(rpc_result.data),
                        "timestamp": datetime.now(UTC).isoformat(),
                        "method": "rpc",
                    },
                }
        except Exception as rpc_error:
            logger.debug(f"RPC function not available, using fallback method: {rpc_error}")

        # Fallback: Optimized query using aggregations (no fetching all requests!)
        # Step 1: Get all models with their provider info
        models_query = client.table("models").select("""
            id,
            model_id,
            model_name,
            provider_model_id,
            provider_id,
            providers!inner(id, name, slug)
            """)

        # Apply provider filter if specified
        if provider_id is not None:
            models_query = models_query.eq("provider_id", provider_id)

        models_result = models_query.execute()

        if not models_result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_models": 0,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "method": "fallback",
                },
            }

        # Step 2: For each model, get aggregated stats using COUNT and RPC
        models_data = []
        for model_info in models_result.data:
            model_id = model_info.get("id")
            if model_id is None:
                continue

            try:
                # Try to use RPC for aggregated stats first
                try:
                    stats_rpc = client.rpc(
                        "get_model_request_stats", {"p_model_id": model_id}
                    ).execute()

                    if stats_rpc.data and len(stats_rpc.data) > 0:
                        stats_data = stats_rpc.data[0]
                        stats = {
                            "total_requests": int(stats_data.get("total_requests", 0)),
                            "total_input_tokens": int(stats_data.get("total_input_tokens", 0)),
                            "total_output_tokens": int(stats_data.get("total_output_tokens", 0)),
                            "total_tokens": int(stats_data.get("total_tokens", 0)),
                            "avg_processing_time_ms": round(
                                float(stats_data.get("avg_processing_time_ms", 0)), 2
                            ),
                        }
                    else:
                        raise Exception("RPC returned no data")

                except Exception:
                    # Fallback: Use COUNT query (still better than fetching all records)
                    count_result = (
                        client.table("chat_completion_requests")
                        .select("id", count="exact", head=True)
                        .eq("model_id", model_id)
                        .execute()
                    )

                    total_requests = count_result.count if count_result.count is not None else 0

                    # Skip models with no requests
                    if total_requests == 0:
                        continue

                    # Note: In fallback mode without RPC, we can't get token stats efficiently
                    # So we set them to 0 or fetch a sample
                    stats = {
                        "total_requests": total_requests,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "avg_processing_time_ms": 0,
                    }

                # Skip models with no requests
                if stats["total_requests"] == 0:
                    continue

                models_data.append(
                    {
                        "model_id": model_info["id"],
                        "model_identifier": model_info["model_id"],
                        "model_name": model_info["model_name"],
                        "provider_model_id": model_info["provider_model_id"],
                        "provider": model_info["providers"],
                        "stats": stats,
                    }
                )

            except Exception as model_error:
                logger.warning(f"Failed to get stats for model_id={model_id}: {model_error}")
                continue

        # Sort by request count (most used first)
        models_data.sort(key=lambda x: x["stats"]["total_requests"], reverse=True)

        return {
            "success": True,
            "data": models_data,
            "metadata": {
                "total_models": len(models_data),
                "timestamp": datetime.now(UTC).isoformat(),
                "method": "fallback_optimized",
            },
        }

    except Exception as e:
        logger.error(f"Failed to get models with requests: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get models with requests: {str(e)}")


@router.get("/chat-requests")
async def get_chat_completion_requests(
    model_id: int | None = Query(None, description="Filter by model ID"),
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    model_name: str | None = Query(None, description="Filter by model name (contains)"),
    start_date: str | None = Query(
        None, description="Filter by start date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
    ),
    end_date: str | None = Query(
        None, description="Filter by end date (ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)"
    ),
    limit: int = Query(100, ge=1, le=100000, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip (pagination)"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get chat completion requests with flexible filtering options.

    This endpoint allows fetching chat completion request data for analytics and plotting graphs.
    You can filter by model_id, provider_id, or model_name (or combine multiple filters).

    Query Parameters:
    - model_id: Filter by specific model ID
    - provider_id: Filter by specific provider ID
    - model_name: Filter by model name (partial match/contains)
    - start_date: Filter requests created after this date (ISO format)
    - end_date: Filter requests created before this date (ISO format)
    - limit: Maximum number of records (default: 100, max: 100000)
    - offset: Pagination offset (default: 0)

    Returns:
    - List of chat completion requests with all metadata including:
      - Request details (request_id, status, error_message)
      - Token usage (input_tokens, output_tokens, total_tokens)
      - Performance metrics (processing_time_ms)
      - Model information (model_id, model_name, provider info)
      - Timestamps (created_at)

    Examples:
    - /api/monitoring/chat-requests?model_id=123
    - /api/monitoring/chat-requests?provider_id=5
    - /api/monitoring/chat-requests?model_name=gpt-4
    - /api/monitoring/chat-requests?model_id=123&start_date=2025-12-01&end_date=2025-12-31
    - /api/monitoring/chat-requests?provider_id=5&limit=50

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Build the query with joins to get model and provider information
        query = client.table("chat_completion_requests").select("""
            *,
            models!inner(
                id,
                model_id,
                model_name,
                provider_model_id,
                provider_id,
                providers!inner(
                    id,
                    name,
                    slug
                )
            )
            """)

        # Apply filters
        if model_id is not None:
            query = query.eq("model_id", model_id)

        if provider_id is not None:
            query = query.eq("models.provider_id", provider_id)

        if model_name is not None:
            # Use ilike for case-insensitive partial matching
            query = query.ilike("models.model_name", f"%{model_name}%")

        if start_date is not None:
            query = query.gte("created_at", start_date)

        if end_date is not None:
            query = query.lte("created_at", end_date)

        # Apply ordering (most recent first), pagination
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

        # Execute query
        result = query.execute()

        # Get total count for pagination metadata (without limit/offset)
        count_query = client.table("chat_completion_requests").select(
            "id", count="exact", head=True
        )

        if model_id is not None:
            count_query = count_query.eq("model_id", model_id)

        # Note: Filtering by provider_id or model_name requires joins,
        # so we'll use the result length as an approximation for now
        count_result = count_query.execute()
        total_count = count_result.count if count_result.count is not None else len(result.data)

        # Format response
        return {
            "success": True,
            "data": result.data or [],
            "metadata": {
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "returned_count": len(result.data or []),
                "filters": {
                    "model_id": model_id,
                    "provider_id": provider_id,
                    "model_name": model_name,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get chat completion requests: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get chat completion requests: {str(e)}"
        )


@router.get("/chat-requests/plot-data")
async def get_chat_requests_plot_data(
    model_id: int | None = Query(None, description="Filter by model ID"),
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    start_date: str | None = Query(None, description="Filter by start date (ISO format)"),
    end_date: str | None = Query(None, description="Filter by end date (ISO format)"),
    api_key: str | None = Depends(get_optional_api_key),
):
    """
    Get optimized chat completion request data for plotting.

    Returns:
    - recent_requests: Last 10 full requests for display
    - plot_data: ALL requests but only tokens and latency (compressed arrays)

    This is highly optimized for frontend plotting:
    - Minimal data transfer (only what's needed for graphs)
    - Compressed format (arrays instead of objects)
    - Fast response time

    Examples:
    - /api/monitoring/chat-requests/plot-data?model_id=123
    - /api/monitoring/chat-requests/plot-data?provider_id=5

    Authentication: Optional. Provide API key for authenticated access.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Build base query
        base_filters = []

        if model_id is not None:
            base_filters.append(("model_id", "eq", model_id))

        # Step 1: Get last 10 full requests for display
        recent_query = client.table("chat_completion_requests").select("""
            id,
            request_id,
            model_id,
            input_tokens,
            output_tokens,
            processing_time_ms,
            status,
            error_message,
            created_at,
            models!inner(
                id,
                model_id,
                model_name,
                provider_model_id,
                providers!inner(
                    id,
                    name,
                    slug
                )
            )
            """)

        # Apply filters
        if model_id is not None:
            recent_query = recent_query.eq("model_id", model_id)

        if provider_id is not None:
            # Note: provider_id filter requires checking through models table
            # We'll filter in Python after fetching
            pass

        if start_date is not None:
            recent_query = recent_query.gte("created_at", start_date)

        if end_date is not None:
            recent_query = recent_query.lte("created_at", end_date)

        recent_query = recent_query.order("created_at", desc=True).limit(10)
        recent_result = recent_query.execute()

        recent_requests = recent_result.data or []

        # Filter by provider_id if specified (post-fetch filtering)
        if provider_id is not None:
            recent_requests = [
                r
                for r in recent_requests
                if r.get("models", {}).get("providers", {}).get("id") == provider_id
            ]

        # Add total_tokens to each recent request
        for req in recent_requests:
            req["total_tokens"] = req.get("input_tokens", 0) + req.get("output_tokens", 0)

        # Step 2: Get ALL requests but only tokens and latency for plotting
        # This is much lighter - we only fetch 3 fields instead of all
        plot_query = client.table("chat_completion_requests").select(
            "input_tokens,output_tokens,processing_time_ms,created_at"
        )

        # Apply same filters
        if model_id is not None:
            plot_query = plot_query.eq("model_id", model_id)

        if start_date is not None:
            plot_query = plot_query.gte("created_at", start_date)

        if end_date is not None:
            plot_query = plot_query.lte("created_at", end_date)

        # Order by created_at for chronological plotting
        plot_query = plot_query.order("created_at", desc=False)

        plot_result = plot_query.execute()
        all_requests = plot_result.data or []

        # Step 3: Compress into arrays for efficient transfer and plotting
        # Instead of sending [{tokens: 100, latency: 50}, ...] we send [[100, 50], ...]
        tokens_array = []
        latency_array = []
        timestamps_array = []

        for req in all_requests:
            input_tokens = req.get("input_tokens", 0)
            output_tokens = req.get("output_tokens", 0)
            total_tokens = input_tokens + output_tokens
            latency = req.get("processing_time_ms", 0)
            timestamp = req.get("created_at")

            tokens_array.append(total_tokens)
            latency_array.append(latency)
            timestamps_array.append(timestamp)

        # Return compressed format
        return {
            "success": True,
            "recent_requests": recent_requests[:10],  # Last 10 for display
            "plot_data": {
                "tokens": tokens_array,  # All total_tokens as array
                "latency": latency_array,  # All latency_ms as array
                "timestamps": timestamps_array,  # All timestamps for x-axis
            },
            "metadata": {
                "recent_count": len(recent_requests[:10]),
                "total_count": len(all_requests),
                "timestamp": datetime.now(UTC).isoformat(),
                "compression": "arrays",
                "format_version": "1.0",
            },
        }

    except Exception as e:
        logger.error(f"Failed to get plot data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get plot data: {str(e)}")
