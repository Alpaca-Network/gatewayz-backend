"""
Prometheus/Grafana SimpleJSON Datasource Endpoints

This module provides endpoints compatible with Grafana's SimpleJSON datasource plugin.
These endpoints act as a bridge between Grafana dashboards and the existing monitoring APIs.

Endpoints:
- GET /prometheus/datasource - Health check
- POST /prometheus/datasource/search - Return available metrics
- POST /prometheus/datasource/query - Query metrics data
- POST /prometheus/datasource/annotations - Annotations (not implemented)
- POST /prometheus/datasource/tag-keys - Tag keys (not implemented)
- POST /prometheus/datasource/tag-values - Tag values (not implemented)

Documentation:
https://github.com/grafana/simple-json-datasource
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.services.redis_metrics import get_redis_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prometheus/datasource", tags=["prometheus-grafana"])


# Request/Response Models
class SearchRequest(BaseModel):
    """Search request from Grafana"""

    target: str | None = None


class QueryTarget(BaseModel):
    """Query target from Grafana"""

    target: str
    refId: str
    type: str = "timeserie"


class QueryRequest(BaseModel):
    """Query request from Grafana"""

    targets: list[QueryTarget]
    range: dict[str, str] = Field(default_factory=dict)
    interval: str | None = None
    maxDataPoints: int | None = None


class Datapoint(BaseModel):
    """Single datapoint [value, timestamp_ms]"""

    pass  # We'll return raw arrays


class QueryResponse(BaseModel):
    """Query response to Grafana"""

    target: str
    datapoints: list[list[float | int]]


# Metric definitions - maps metric names to data extraction logic
AVAILABLE_METRICS = [
    "avg_health_score",
    "total_requests",
    "total_cost",
    "error_rate",
    "active_requests",
    "avg_latency_ms",
]


@router.get("")
async def health_check():
    """
    Health check endpoint for Grafana SimpleJSON datasource.

    Returns 200 OK if the datasource is available.
    """
    return {"status": "ok", "message": "Prometheus/Grafana datasource proxy is operational"}


@router.post("/search")
async def search(request: SearchRequest):
    """
    Return list of available metrics for Grafana metric selection.

    This endpoint is called when users open a panel to select metrics.
    """
    logger.info(f"Search request: {request.target if request else 'all'}")

    # Return all available metrics
    return AVAILABLE_METRICS


@router.post("/query")
async def query(request: QueryRequest):
    """
    Query metrics data for Grafana panels.

    This is the main endpoint that fetches actual metric values.

    Args:
        request: Query request containing targets and time range

    Returns:
        List of QueryResponse objects with metric data
    """
    try:
        logger.info(f"Query request: {len(request.targets)} targets")

        results = []
        current_timestamp = int(datetime.now().timestamp() * 1000)

        redis_metrics = get_redis_metrics()

        for target in request.targets:
            metric_name = target.target

            logger.info(f"Fetching metric: {metric_name}")

            try:
                # Fetch metric based on name
                value = await _fetch_metric_value(metric_name, redis_metrics)

                # Create datapoint [value, timestamp_ms]
                datapoint = [value, current_timestamp]

                results.append({"target": metric_name, "datapoints": [datapoint]})

                logger.info(f"Metric {metric_name} = {value}")

            except Exception as e:
                logger.error(f"Error fetching {metric_name}: {e}", exc_info=True)
                # Return 0 on error
                results.append({"target": metric_name, "datapoints": [[0.0, current_timestamp]]})

        return results

    except Exception as e:
        logger.error(f"Query error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.post("/annotations")
async def annotations(request: Request):
    """
    Return annotations for Grafana.

    Not implemented - returns empty list.
    """
    return []


@router.post("/tag-keys")
async def tag_keys(request: Request):
    """
    Return available tag keys.

    Not implemented - returns empty list.
    """
    return []


@router.post("/tag-values")
async def tag_values(request: Request):
    """
    Return available tag values.

    Not implemented - returns empty list.
    """
    return []


# Helper Functions
async def _fetch_metric_value(metric_name: str, redis_metrics) -> float:
    """
    Fetch a specific metric value from the monitoring system.

    Args:
        metric_name: Name of the metric to fetch
        redis_metrics: Redis metrics service instance

    Returns:
        Metric value as float

    Raises:
        ValueError: If metric name is unknown
    """
    if metric_name == "avg_health_score":
        # Get average health score across all providers
        health_scores = await redis_metrics.get_all_provider_health()
        if health_scores:
            avg_score = sum(health_scores.values()) / len(health_scores)
            return float(avg_score)
        return 100.0

    elif metric_name == "total_requests":
        # Get total requests across all providers (last hour)
        total = 0
        health_scores = await redis_metrics.get_all_provider_health()
        for provider in health_scores.keys():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=1)
            for hour_data in hourly_stats.values():
                total += hour_data.get("total_requests", 0)
        return float(total)

    elif metric_name == "total_cost":
        # Get total cost across all providers (last hour)
        total = 0.0
        health_scores = await redis_metrics.get_all_provider_health()
        for provider in health_scores.keys():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=1)
            for hour_data in hourly_stats.values():
                total += hour_data.get("total_cost", 0.0)
        return float(total)

    elif metric_name == "error_rate":
        # Calculate error rate (last hour)
        total_requests = 0
        failed_requests = 0
        health_scores = await redis_metrics.get_all_provider_health()
        for provider in health_scores.keys():
            hourly_stats = await redis_metrics.get_hourly_stats(provider, hours=1)
            for hour_data in hourly_stats.values():
                total_requests += hour_data.get("total_requests", 0)
                failed_requests += hour_data.get("failed_requests", 0)

        if total_requests > 0:
            error_rate = (failed_requests / total_requests) * 100
            return float(error_rate)
        return 0.0

    elif metric_name == "active_requests":
        # This would require tracking in-flight requests
        # For now, return 0
        return 0.0

    elif metric_name == "avg_latency_ms":
        # This would require calculating average latency from all providers
        # For now, return 0
        return 0.0

    else:
        raise ValueError(f"Unknown metric: {metric_name}")
