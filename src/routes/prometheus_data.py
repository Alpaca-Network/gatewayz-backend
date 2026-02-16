"""
Prometheus Data Endpoints for Grafana Stack Integration.

This module provides REST API endpoints specifically designed for the Railway Grafana stack
(Prometheus, Grafana, Loki, Tempo) to consume telemetry data.

All endpoints are under the /prometheus/data/* path.

Endpoints:
- GET /prometheus/data/metrics                    → Prometheus format metrics for scraping
- GET /prometheus/data/admin/cache/status         → Cache status and statistics (admin-only)
- DELETE /prometheus/data/admin/cache/invalidate  → Invalidate all caches (admin-only)
- GET /prometheus/data/instrumentation/*          → Instrumentation status (Loki, Tempo)

PROMETHEUS METRICS EXPOSED (for Grafana alerting):
- health_score{provider="..."} - Provider health score (0-100)
- circuit_breaker_state{provider="...", model="...", state="..."} - Circuit breaker state (1=active)
- error_rate{provider="..."} - Provider error rate (0-1)
- provider_availability{provider="..."} - Provider availability (0-100)
- total_requests{provider="..."} - Total requests in last hour
- avg_latency_ms{provider="..."} - Average latency in ms

NOTE: For JSON API endpoints (health scores, circuit breakers, anomalies, cost analysis,
error rates, realtime stats, etc.), use the /api/monitoring/* endpoints instead.
See src/routes/monitoring.py for the full list of monitoring endpoints.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prometheus/data", tags=["prometheus-data"])

# Cache configuration
CACHE_PREFIX = "prometheus:data:"


# ============================================================================
# Helper Functions
# ============================================================================

async def _get_redis_client():
    """Get Redis client for caching."""
    try:
        from src.config.redis_config import get_redis_client
        return await get_redis_client()
    except Exception as e:
        logger.warning(f"Failed to get Redis client for caching: {e}")
        return None


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


# ============================================================================
# Cache Management Endpoints
# ============================================================================

@router.delete("/admin/cache/invalidate")
async def invalidate_cache(
    pattern: str = Query("*", description="Cache key pattern to invalidate (* for all)"),
    admin_user: dict = Depends(require_admin)
):
    """
    Invalidate prometheus data cache entries.

    Use this to force fresh data on the next request.
    Patterns: * (all), or specific key patterns.
    """
    count = await _invalidate_cache(pattern)
    return {
        "success": True,
        "invalidated_count": count,
        "pattern": pattern,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/admin/cache/status")
async def get_cache_status(admin_user: dict = Depends(require_admin)):
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
    try:
        from src.services.redis_metrics import get_redis_metrics
        from src.services.model_availability import availability_service

        lines = []

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
        from prometheus_client import REGISTRY, generate_latest
        import httpx

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
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "error",
            "error": str(e)
        }


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
