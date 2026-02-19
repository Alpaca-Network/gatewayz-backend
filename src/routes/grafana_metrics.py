"""
Grafana Metrics Endpoints for Prometheus, Loki, and Tempo integration.

This module provides endpoints for:
- /metrics - Prometheus metrics endpoint (Grafana dashboard compatible)
- /api/metrics/status - Metrics service status
- /api/metrics/summary - Structured metrics summary
- /api/metrics/test - Generate test metrics for verification

Compatible with:
- Grafana FastAPI Observability Dashboard (ID: 16110)
- Prometheus scraping
- Loki log aggregation
- Tempo distributed tracing
"""

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from src.services.grafana_metrics_service import grafana_metrics_service

router = APIRouter(tags=["metrics"])
logger = logging.getLogger(__name__)


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """
    Prometheus metrics endpoint.

    Exposes metrics in Prometheus text exposition format for scraping.
    Compatible with Grafana FastAPI Observability Dashboard.

    Metrics exposed:
    - fastapi_requests_total: Total HTTP requests (Counter)
    - fastapi_requests_duration_seconds: Request duration (Histogram)
    - fastapi_requests_in_progress: Concurrent requests (Gauge)
    - model_inference_requests_total: Model inference requests (Counter)
    - model_inference_duration_seconds: Inference duration (Histogram)
    - tokens_used_total: Token consumption (Counter)
    - provider_availability: Provider health status (Gauge)
    - cache_hits_total / cache_misses_total: Cache metrics (Counter)

    Returns:
        Response: Prometheus text format metrics
    """
    try:
        metrics_data = grafana_metrics_service.get_prometheus_metrics()
        return Response(
            content=metrics_data,
            media_type="text/plain; charset=utf-8"
        )
    except Exception as e:
        logger.error(f"Error generating Prometheus metrics: {e}")
        return Response(
            content=f"# Error generating metrics: {e}\n",
            media_type="text/plain; charset=utf-8",
            status_code=500
        )


@router.get("/api/metrics/status", tags=["metrics"])
async def metrics_status():
    """
    Get metrics service status.

    Returns:
        dict: Status of metrics collection including mode (live/synthetic)
    """
    return grafana_metrics_service.get_metrics_summary()


@router.get("/api/metrics/summary", tags=["metrics"])
async def metrics_summary():
    """
    Get structured metrics summary for dashboard overview.

    Returns:
        dict: Summary of key metrics and system state
    """
    from prometheus_client import REGISTRY

    # Collect metric names and types
    metrics_info = []
    for collector in REGISTRY._collector_to_names.keys():
        if hasattr(collector, '_name'):
            metric_type = type(collector).__name__
            metrics_info.append({
                "name": collector._name,
                "type": metric_type,
                "description": getattr(collector, '_documentation', 'N/A')
            })

    summary = grafana_metrics_service.get_metrics_summary()
    summary["registered_metrics"] = len(metrics_info)
    summary["metrics"] = metrics_info[:20]  # Limit to first 20 for readability

    return summary


@router.post("/api/metrics/test", tags=["metrics"])
async def test_metrics(request: Request):
    """
    Generate test metrics for verification.

    This endpoint generates sample metrics data to verify:
    - Prometheus scraping is working
    - Loki is receiving structured logs
    - Tempo is receiving traces

    Returns:
        dict: Test results with trace IDs for correlation
    """
    from src.services.prometheus_metrics import (
        fastapi_requests_duration_seconds,
        fastapi_requests_total,
        model_inference_requests,
    )

    start_time = time.time()
    trace_id = None
    span_id = None

    # Get trace context
    try:
        from src.config.opentelemetry_config import (
            OpenTelemetryConfig,
            get_current_span_id,
            get_current_trace_id,
        )

        trace_id = get_current_trace_id()
        span_id = get_current_span_id()

        # Create a test span
        tracer = OpenTelemetryConfig.get_tracer(__name__)
        if tracer:
            with tracer.start_as_current_span("test_metrics_generation") as span:
                span.set_attribute("test", True)
                span.set_attribute("timestamp", datetime.now(timezone.utc).isoformat())

                # Generate test metrics within span
                _generate_test_metrics(
                    fastapi_requests_total,
                    fastapi_requests_duration_seconds,
                    model_inference_requests,
                )
    except Exception as e:
        logger.warning(f"Tracing not available: {e}")
        # Generate metrics without tracing
        _generate_test_metrics(
            fastapi_requests_total,
            fastapi_requests_duration_seconds,
            model_inference_requests,
        )

    duration_ms = int((time.time() - start_time) * 1000)

    # Log structured entry for Loki
    log_entry = grafana_metrics_service.get_structured_log_entry(
        level="INFO",
        message="Test metrics generated",
        endpoint="/api/metrics/test",
        method="POST",
        status_code=200,
        duration_ms=duration_ms,
        extra={"test": True, "metrics_generated": 5}
    )
    logger.info(f"Test metrics: {log_entry}")

    return {
        "status": "success",
        "message": "Test metrics generated successfully",
        "trace_id": trace_id or "none",
        "span_id": span_id or "none",
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verification": {
            "prometheus": "Check /metrics endpoint for fastapi_* metrics",
            "loki": "Query: {service=\"gatewayz-api\", test=\"true\"}",
            "tempo": f"Search for trace_id: {trace_id}" if trace_id else "Tracing not enabled",
        }
    }


def _generate_test_metrics(requests_total, requests_duration, inference_requests):
    """Generate test metric values."""
    from src.services.grafana_metrics_service import APP_NAME

    # Test request metrics
    try:
        requests_total.labels(
            app_name=APP_NAME,
            method="POST",
            path="/api/metrics/test",
            status_code=200,
            status_class="2xx"
        ).inc()

        requests_duration.labels(
            app_name=APP_NAME,
            method="POST",
            path="/api/metrics/test"
        ).observe(0.05)
    except Exception as e:
        logger.debug(f"Could not record request metrics: {e}")

    # Test inference metrics
    try:
        inference_requests.labels(
            provider="test",
            model="test-model",
            status="success"
        ).inc()
    except Exception as e:
        logger.debug(f"Could not record inference metrics: {e}")


@router.get("/api/metrics/grafana-queries", tags=["metrics"])
async def grafana_queries():
    """
    Get PromQL queries for Grafana dashboard panels.

    Returns:
        dict: PromQL queries for each dashboard panel
    """
    return {
        "dashboard_id": "16110",
        "dashboard_name": "FastAPI Observability",
        "panels": {
            "total_requests": {
                "title": "Total Requests",
                "query": "sum(rate(fastapi_requests_total[5m]))",
                "description": "Current request rate (requests/second)"
            },
            "requests_per_minute": {
                "title": "Requests Per Minute",
                "query": "rate(fastapi_requests_total[1m]) * 60",
                "description": "Trend line showing RPM over time"
            },
            "errors_per_second": {
                "title": "Errors Per Second",
                "query": "rate(fastapi_requests_total{status_code=~\"4..|5..\"}[1m])",
                "description": "Error rate for 4xx and 5xx responses"
            },
            "average_response_time": {
                "title": "Average Response Time",
                "query": "rate(fastapi_requests_duration_seconds_sum[5m]) / rate(fastapi_requests_duration_seconds_count[5m])",
                "description": "Mean latency per endpoint"
            },
            "request_duration_p50": {
                "title": "Request Duration P50",
                "query": "histogram_quantile(0.50, rate(fastapi_requests_duration_seconds_bucket[5m]))",
                "description": "50th percentile latency"
            },
            "request_duration_p95": {
                "title": "Request Duration P95",
                "query": "histogram_quantile(0.95, rate(fastapi_requests_duration_seconds_bucket[5m]))",
                "description": "95th percentile latency"
            },
            "request_duration_p99": {
                "title": "Request Duration P99",
                "query": "histogram_quantile(0.99, rate(fastapi_requests_duration_seconds_bucket[5m]))",
                "description": "99th percentile latency"
            },
            "cpu_usage": {
                "title": "CPU Usage",
                "query": "rate(process_cpu_seconds_total[5m]) * 100",
                "description": "Process CPU percentage (0-100%)"
            },
            "memory_usage": {
                "title": "Memory Usage (MB)",
                "query": "process_resident_memory_bytes / 1024 / 1024",
                "description": "Process memory in MB"
            },
            "requests_in_progress": {
                "title": "Requests In Progress",
                "query": "sum(fastapi_requests_in_progress)",
                "description": "Current concurrent requests"
            },
            "model_inference_rate": {
                "title": "Model Inference Rate",
                "query": "sum(rate(model_inference_requests_total[5m])) by (provider)",
                "description": "Inference requests per second by provider"
            },
            "token_consumption": {
                "title": "Token Consumption",
                "query": "sum(rate(tokens_used_total[5m])) by (token_type)",
                "description": "Token usage rate by type (input/output)"
            },
            "provider_availability": {
                "title": "Provider Availability",
                "query": "provider_availability",
                "description": "Provider health status (1=available, 0=unavailable)"
            },
            "cache_hit_rate": {
                "title": "Cache Hit Rate",
                "query": "sum(rate(cache_hits_total[5m])) / (sum(rate(cache_hits_total[5m])) + sum(rate(cache_misses_total[5m])))",
                "description": "Cache hit ratio (0-1)"
            },
        },
        "loki_queries": {
            "all_logs": "{service=\"gatewayz-api\"}",
            "error_logs": "{service=\"gatewayz-api\", level=\"ERROR\"}",
            "slow_requests": "{service=\"gatewayz-api\"} | json | duration_ms > 1000",
            "by_endpoint": "{service=\"gatewayz-api\"} | json | endpoint=\"/v1/chat/completions\"",
        },
        "tempo_queries": {
            "service_traces": "service.name=\"gatewayz-api\"",
            "slow_traces": "service.name=\"gatewayz-api\" && duration > 1s",
            "error_traces": "service.name=\"gatewayz-api\" && status.code=error",
        }
    }


@router.get("/api/metrics/health", tags=["metrics"])
async def metrics_health():
    """
    Health check for metrics collection.

    Returns:
        dict: Health status of metrics subsystem
    """
    health = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {}
    }

    # Check Prometheus registry
    try:
        from prometheus_client import REGISTRY
        metric_count = len(list(REGISTRY._collector_to_names.keys()))
        health["components"]["prometheus"] = {
            "status": "healthy",
            "registered_metrics": metric_count
        }
    except Exception as e:
        health["components"]["prometheus"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health["status"] = "degraded"

    # Check Supabase availability
    health["components"]["supabase"] = {
        "status": "healthy" if not grafana_metrics_service.is_synthetic_mode() else "unavailable",
        "mode": "live" if not grafana_metrics_service.is_synthetic_mode() else "synthetic"
    }

    # Check OpenTelemetry
    try:
        from src.config.config import Config
        from src.config.opentelemetry_config import OPENTELEMETRY_AVAILABLE

        health["components"]["opentelemetry"] = {
            "status": "healthy" if OPENTELEMETRY_AVAILABLE and Config.TEMPO_ENABLED else "disabled",
            "available": OPENTELEMETRY_AVAILABLE,
            "tempo_enabled": Config.TEMPO_ENABLED
        }
    except Exception as e:
        health["components"]["opentelemetry"] = {
            "status": "unavailable",
            "error": str(e)
        }

    # Check Loki
    try:
        from src.config.config import Config
        health["components"]["loki"] = {
            "status": "healthy" if Config.LOKI_ENABLED else "disabled",
            "enabled": Config.LOKI_ENABLED
        }
    except Exception as e:
        health["components"]["loki"] = {
            "status": "unavailable",
            "error": str(e)
        }

    return health
