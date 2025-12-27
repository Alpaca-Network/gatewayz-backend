"""
Prometheus metrics endpoints with structured organization.

This module provides organized endpoints for accessing Prometheus metrics,
structured by category to support:
- Grafana dashboard queries
- Application health monitoring
- Cost analysis
- Performance tracking
- Provider health analysis

Endpoints:
- GET /prometheus/metrics/all           → All metrics in Prometheus format
- GET /prometheus/metrics/system        → System/HTTP metrics only
- GET /prometheus/metrics/providers     → Provider health metrics
- GET /prometheus/metrics/models        → Model-specific metrics
- GET /prometheus/metrics/business      → Business metrics (costs, credits)
- GET /prometheus/metrics/performance   → Latency/throughput metrics
- GET /prometheus/metrics/summary       → Summary statistics (JSON)

The /metrics endpoint (Prometheus format) remains available at GET /metrics
for backward compatibility and Prometheus scraping.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response, JSONResponse
from prometheus_client import REGISTRY, generate_latest

from src.config import Config
from src.services.prometheus_metrics import (
    # HTTP metrics
    fastapi_requests_total,
    fastapi_requests_duration_seconds,
    fastapi_requests_in_progress,
    fastapi_exceptions_total,
    # Model inference metrics
    model_inference_requests,
    model_inference_duration,
    tokens_used,
    credits_used,
    # Database metrics
    database_query_count,
    database_query_duration,
    # Cache metrics
    cache_hits,
    cache_misses,
    # Provider health metrics
    provider_availability,
    provider_error_rate,
    provider_response_time,
    # Business metrics
    active_api_keys,
    active_connections,
    subscription_count,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prometheus/metrics", tags=["prometheus-metrics"])


def _filter_metrics_by_name(metrics_text: str, metric_names: list[str]) -> str:
    """
    Filter Prometheus metrics by name.

    Args:
        metrics_text: Raw Prometheus metrics text
        metric_names: List of metric names to include (e.g., ["fastapi_requests_total"])

    Returns:
        Filtered metrics text containing only specified metrics
    """
    lines = metrics_text.split("\n")
    filtered = []

    for line in lines:
        # Include HELP and TYPE lines for any matching metric
        if line.startswith("# HELP") or line.startswith("# TYPE"):
            for name in metric_names:
                if name in line:
                    filtered.append(line)
                    break
        # Include metric lines that start with a matching metric name
        elif line and not line.startswith("#"):
            for name in metric_names:
                if line.startswith(name + "{") or line.startswith(name + " "):
                    filtered.append(line)
                    break

    return "\n".join(filtered) + "\n" if filtered else ""


@router.get("/all", include_in_schema=True)
async def all_metrics():
    """
    Get all Prometheus metrics.

    Returns all metrics in Prometheus text format. Same as GET /metrics
    but under /prometheus/metrics namespace for consistency.

    Returns:
        Prometheus text format metrics
    """
    return Response(generate_latest(REGISTRY), media_type="text/plain; charset=utf-8")


@router.get("/system", include_in_schema=True)
async def system_metrics():
    """
    Get system and HTTP metrics only.

    Includes:
    - FastAPI request metrics (count, duration, in_progress, exceptions)
    - Basic HTTP statistics

    Returns:
        Prometheus text format metrics (system metrics only)
    """
    all_metrics = generate_latest(REGISTRY).decode("utf-8")

    metric_names = [
        "fastapi_requests_total",
        "fastapi_requests_duration_seconds",
        "fastapi_requests_in_progress",
        "fastapi_exceptions_total",
        "fastapi_app_info",
    ]

    filtered = _filter_metrics_by_name(all_metrics, metric_names)
    return Response(filtered, media_type="text/plain; charset=utf-8")


@router.get("/providers", include_in_schema=True)
async def provider_metrics():
    """
    Get provider health metrics only.

    Includes:
    - provider_availability{provider} - 1=available, 0=down
    - provider_error_rate{provider} - 0.0-1.0
    - provider_response_time_seconds{provider} - histogram
    - gatewayz_provider_health_score{provider} - 0.0-1.0 composite score

    Returns:
        Prometheus text format metrics (provider metrics only)
    """
    all_metrics = generate_latest(REGISTRY).decode("utf-8")

    metric_names = [
        "provider_availability",
        "provider_error_rate",
        "provider_response_time_seconds",
        "gatewayz_provider_health_score",
    ]

    filtered = _filter_metrics_by_name(all_metrics, metric_names)
    return Response(filtered, media_type="text/plain; charset=utf-8")


@router.get("/models", include_in_schema=True)
async def model_metrics():
    """
    Get model-specific metrics.

    Includes:
    - model_inference_requests_total{model, provider, status}
    - model_inference_duration_seconds{model, provider}
    - tokens_used_total{model, provider}
    - credits_used_total{model, provider}

    Returns:
        Prometheus text format metrics (model metrics only)
    """
    all_metrics = generate_latest(REGISTRY).decode("utf-8")

    metric_names = [
        "model_inference_requests_total",
        "model_inference_duration_seconds",
        "tokens_used_total",
        "credits_used_total",
    ]

    filtered = _filter_metrics_by_name(all_metrics, metric_names)
    return Response(filtered, media_type="text/plain; charset=utf-8")


@router.get("/business", include_in_schema=True)
async def business_metrics():
    """
    Get business and user metrics.

    Includes:
    - active_api_keys - Count of active API keys
    - active_connections - Current active connections
    - subscription_count - Active subscriptions
    - trial_active - Active trial accounts
    - tokens_used_total{model, provider} - Token consumption
    - credits_used_total{model, provider} - Cost tracking

    Returns:
        Prometheus text format metrics (business metrics only)
    """
    all_metrics = generate_latest(REGISTRY).decode("utf-8")

    metric_names = [
        "active_api_keys",
        "active_connections",
        "subscription_count",
        "trial_active",
        "tokens_used_total",
        "credits_used_total",
    ]

    filtered = _filter_metrics_by_name(all_metrics, metric_names)
    return Response(filtered, media_type="text/plain; charset=utf-8")


@router.get("/performance", include_in_schema=True)
async def performance_metrics():
    """
    Get performance and latency metrics.

    Includes:
    - fastapi_requests_duration_seconds - HTTP request latency (histogram)
    - model_inference_duration_seconds{model, provider} - Model inference latency
    - database_query_duration_seconds{operation} - Database query latency
    - provider_response_time_seconds{provider} - Provider API response time

    Returns:
        Prometheus text format metrics (performance metrics only)
    """
    all_metrics = generate_latest(REGISTRY).decode("utf-8")

    metric_names = [
        "fastapi_requests_duration_seconds",
        "model_inference_duration_seconds",
        "database_query_duration_seconds",
        "provider_response_time_seconds",
    ]

    filtered = _filter_metrics_by_name(all_metrics, metric_names)
    return Response(filtered, media_type="text/plain; charset=utf-8")


@router.get("/summary", include_in_schema=True)
async def metrics_summary(
    category: str = Query(None, description="Filter by category: system, providers, models, business, performance")
):
    """
    Get summary statistics of current metrics (JSON format).

    Returns aggregated statistics including:
    - Total request count
    - Request rate (requests/minute)
    - Error rate
    - Average latency
    - Provider availability
    - Token usage
    - Credit consumption

    Query Parameters:
    - category: Optional filter by metric category

    Returns:
        JSON with summary statistics
    """
    try:
        # Parse all metrics
        all_metrics = generate_latest(REGISTRY).decode("utf-8")

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "http": _get_http_summary(),
                "models": _get_models_summary(),
                "providers": _get_providers_summary(),
                "database": _get_database_summary(),
                "business": _get_business_summary(),
            }
        }

        # Filter by category if specified
        if category and category in summary["metrics"]:
            summary["metrics"] = {category: summary["metrics"][category]}

        return JSONResponse(summary)

    except Exception as e:
        logger.error(f"Error generating metrics summary: {e}")
        return JSONResponse(
            {"error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()},
            status_code=500
        )


def _get_http_summary() -> dict[str, Any]:
    """Get summary of HTTP metrics."""
    try:
        # These would be calculated from actual metric values
        # For now, return structure with placeholder implementation
        return {
            "total_requests": "N/A",  # Would calculate from fastapi_requests_total
            "request_rate_per_minute": "N/A",
            "error_rate": "N/A",  # Would calculate from fastapi_exceptions_total / requests
            "avg_latency_ms": "N/A",
            "in_progress": "N/A",
        }
    except Exception as e:
        logger.warning(f"Could not calculate HTTP summary: {e}")
        return {}


def _get_models_summary() -> dict[str, Any]:
    """Get summary of model metrics."""
    try:
        return {
            "total_inference_requests": "N/A",
            "tokens_used_total": "N/A",
            "credits_used_total": "N/A",
            "avg_inference_latency_ms": "N/A",
        }
    except Exception as e:
        logger.warning(f"Could not calculate model summary: {e}")
        return {}


def _get_providers_summary() -> dict[str, Any]:
    """Get summary of provider health metrics."""
    try:
        return {
            "total_providers": "N/A",
            "healthy_providers": "N/A",
            "degraded_providers": "N/A",
            "unavailable_providers": "N/A",
            "avg_error_rate": "N/A",
            "avg_response_time_ms": "N/A",
        }
    except Exception as e:
        logger.warning(f"Could not calculate provider summary: {e}")
        return {}


def _get_database_summary() -> dict[str, Any]:
    """Get summary of database metrics."""
    try:
        return {
            "total_queries": "N/A",
            "avg_query_latency_ms": "N/A",
            "cache_hit_rate": "N/A",
        }
    except Exception as e:
        logger.warning(f"Could not calculate database summary: {e}")
        return {}


def _get_business_summary() -> dict[str, Any]:
    """Get summary of business metrics."""
    try:
        return {
            "active_api_keys": "N/A",
            "active_subscriptions": "N/A",
            "active_trials": "N/A",
            "total_tokens_used": "N/A",
            "total_credits_used": "N/A",
        }
    except Exception as e:
        logger.warning(f"Could not calculate business summary: {e}")
        return {}


@router.get("/docs", include_in_schema=True)
async def prometheus_endpoints_documentation():
    """
    Get documentation for all Prometheus endpoints.

    Returns:
        Markdown documentation with curl examples
    """
    docs = """
# Prometheus Metrics Endpoints

All metrics are available in Prometheus text format. Use these endpoints for Grafana dashboard queries.

## Endpoints

### GET /prometheus/metrics/all
All metrics in Prometheus format.
```bash
curl http://localhost:8000/prometheus/metrics/all
```

### GET /prometheus/metrics/system
System and HTTP metrics (fastapi_requests_*, exceptions, in_progress)
```bash
curl http://localhost:8000/prometheus/metrics/system
```

### GET /prometheus/metrics/providers
Provider health metrics (availability, error_rate, response_time, health_score)
```bash
curl http://localhost:8000/prometheus/metrics/providers
```

### GET /prometheus/metrics/models
Model-specific metrics (inference requests, duration, tokens, credits)
```bash
curl http://localhost:8000/prometheus/metrics/models
```

### GET /prometheus/metrics/business
Business metrics (active_api_keys, subscriptions, tokens, credits)
```bash
curl http://localhost:8000/prometheus/metrics/business
```

### GET /prometheus/metrics/performance
Performance metrics (request/inference/query latency, response times)
```bash
curl http://localhost:8000/prometheus/metrics/performance
```

### GET /prometheus/metrics/summary
Summary statistics in JSON format
```bash
curl http://localhost:8000/prometheus/metrics/summary
curl http://localhost:8000/prometheus/metrics/summary?category=providers
```

## Grafana Queries

### Provider Health Dashboard
```promql
# Provider availability
provider_availability{provider="openrouter"}

# Provider health score
gatewayz_provider_health_score{provider="openrouter"}

# Provider error rate
provider_error_rate{provider="openrouter"}
```

### Model Performance Dashboard
```promql
# Model request rate
sum(rate(model_inference_requests_total[5m])) by (model)

# Model latency (p95)
histogram_quantile(0.95, rate(model_inference_duration_seconds_bucket[5m])) by (model)

# Token usage
sum(rate(tokens_used_total[5m])) by (model)
```

### Business Dashboard
```promql
# Active subscriptions
subscription_count

# Total credits used
credits_used_total

# API key usage
api_key_usage_total
```

## Prometheus Configuration

Add to prometheus.yml:
```yaml
scrape_configs:
  - job_name: 'gatewayz-backend'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'  # or use /prometheus/metrics/all
    scrape_interval: 15s
    scrape_timeout: 10s
```

## Grafana Dashboard Variables

For Grafana dashboards, use these variables:

```json
{
  "name": "provider",
  "query": "label_values(provider_availability, provider)",
  "datasource": "Prometheus"
}

{
  "name": "model",
  "query": "label_values(model_inference_requests_total, model)",
  "datasource": "Prometheus"
}
```
"""
    return Response(docs, media_type="text/markdown")
