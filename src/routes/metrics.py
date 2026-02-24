"""
Prometheus metrics endpoints for GatewayZ API.

Provides:
- /metrics - Prometheus exposition format metrics
- /metrics/json - JSON formatted metrics
- /metrics/health - Health check with metrics
"""

from fastapi import APIRouter, HTTPException

from src.services.metrics_instrumentation import get_metrics_collector
from src.services.prometheus_exporter import PrometheusExporter

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/metrics", response_class=str)
async def get_prometheus_metrics():
    """
    Get metrics in Prometheus exposition format.

    This endpoint is scraped by Prometheus and returns metrics in the
    text-based exposition format (TYPE 0.0.4).

    Returns:
        Metrics in Prometheus exposition format
    """
    try:
        exporter = PrometheusExporter()
        return exporter.export_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export metrics: {str(e)}")


@router.get("/metrics/json")
async def get_metrics_json():
    """
    Get metrics in JSON format.

    Returns structured metrics as JSON for programmatic access.

    Returns:
        JSON object with all metrics
    """
    try:
        collector = get_metrics_collector()
        return collector.get_metrics_snapshot()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {str(e)}")


@router.get("/metrics/health")
async def get_metrics_health():
    """
    Get health status with key metrics.

    Returns:
        Health status and key performance indicators
    """
    try:
        collector = get_metrics_collector()
        metrics = collector.get_metrics_snapshot()

        # Calculate overall health
        total_requests = sum(
            sum(methods.values()) for methods in metrics.get("requests", {}).values()
        )
        total_errors = sum(sum(methods.values()) for methods in metrics.get("errors", {}).values())

        error_rate = total_errors / total_requests if total_requests > 0 else 0

        # Determine health status
        if error_rate > 0.1:  # > 10% error rate
            status = "unhealthy"
        elif error_rate > 0.05:  # > 5% error rate
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "total_requests": total_requests,
            "total_errors": total_errors,
            "error_rate": error_rate,
            "uptime_seconds": metrics.get("uptime_seconds", 0),
            "cache_hit_rate": metrics.get("cache", {}).get("hit_rate", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get health: {str(e)}")


@router.post("/metrics/reset")
async def reset_metrics():
    """
    Reset all metrics (admin only).

    Returns:
        Confirmation message
    """
    try:
        collector = get_metrics_collector()
        collector.reset()
        return {"message": "Metrics reset successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset metrics: {str(e)}")
