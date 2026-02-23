"""Real-time diagnostics endpoints for bottleneck monitoring.

Provides detailed visibility into concurrency gate status, provider performance,
and system capacity utilization to help identify and resolve 503 errors.
"""

import logging
from typing import Any

from fastapi import APIRouter

from src.config import Config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/concurrency")
async def get_concurrency_stats() -> dict[str, Any]:
    """Get real-time concurrency gate statistics.

    Returns detailed information about current active requests, queued requests,
    and utilization percentages to help diagnose 503 Service Unavailable errors.

    This endpoint is useful for:
    - Identifying when the concurrency gate is near capacity
    - Detecting queue overflow conditions
    - Understanding request backpressure in real-time
    - Troubleshooting 503 errors from queue_full/queue_timeout rejections

    Returns:
        dict: Concurrency statistics including:
            - active_requests: Current number of requests being processed
            - queued_requests: Current number of requests waiting in queue
            - concurrency_limit: Maximum concurrent requests allowed
            - queue_size_limit: Maximum queue size
            - utilization_percent: Active slots usage (0-100)
            - queue_utilization_percent: Queue usage (0-100)
            - status: Overall health status (healthy/warning/critical)
    """
    try:
        # Import here to avoid circular dependencies
        from src.middleware.concurrency_middleware import (
            concurrency_active,
            concurrency_queued,
        )

        # Get current values from Prometheus metrics
        active = concurrency_active._value._value
        queued = concurrency_queued._value._value

        # Calculate utilization percentages
        utilization = (active / Config.CONCURRENCY_LIMIT) * 100 if Config.CONCURRENCY_LIMIT > 0 else 0
        queue_utilization = (
            (queued / Config.CONCURRENCY_QUEUE_SIZE) * 100 if Config.CONCURRENCY_QUEUE_SIZE > 0 else 0
        )

        # Determine status based on utilization
        if utilization >= 90 or queue_utilization >= 80:
            status = "critical"
        elif utilization >= 70 or queue_utilization >= 60:
            status = "warning"
        else:
            status = "healthy"

        return {
            "active_requests": active,
            "queued_requests": queued,
            "concurrency_limit": Config.CONCURRENCY_LIMIT,
            "queue_size_limit": Config.CONCURRENCY_QUEUE_SIZE,
            "queue_timeout_seconds": Config.CONCURRENCY_QUEUE_TIMEOUT,
            "utilization_percent": round(utilization, 2),
            "queue_utilization_percent": round(queue_utilization, 2),
            "status": status,
            "available_slots": Config.CONCURRENCY_LIMIT - active,
            "available_queue_slots": Config.CONCURRENCY_QUEUE_SIZE - queued,
        }
    except Exception as e:
        logger.error(f"Failed to get concurrency stats: {e}")
        return {
            "error": str(e),
            "status": "unknown",
            "concurrency_limit": Config.CONCURRENCY_LIMIT,
            "queue_size_limit": Config.CONCURRENCY_QUEUE_SIZE,
        }


@router.get("/provider-timing")
async def get_provider_timing_summary() -> dict[str, Any]:
    """Get summary of provider response times from Prometheus metrics.

    Returns aggregated metrics showing which providers are slow and contributing
    to concurrency slot blocking.

    Returns:
        dict: Provider timing summary with:
            - metrics_available: Whether Prometheus metrics are accessible
            - slow_providers: List of providers with >30s response times
            - note: Instructions for viewing detailed metrics
    """
    try:
        from src.services.prometheus_metrics import (
            provider_slow_requests_total,
        )

        # Get current slow request counts
        slow_counts = {}
        for sample in provider_slow_requests_total.collect()[0].samples:
            labels = sample.labels
            provider = labels.get("provider", "unknown")
            model = labels.get("model", "unknown")
            severity = labels.get("severity", "unknown")
            count = sample.value

            if count > 0:
                key = f"{provider}/{model}"
                if key not in slow_counts:
                    slow_counts[key] = {}
                slow_counts[key][severity] = int(count)

        return {
            "metrics_available": True,
            "slow_request_counts": slow_counts,
            "note": "Use Prometheus/Grafana for detailed timing histograms. Query: provider_response_duration_seconds",
            "thresholds": {
                "slow": "30-45 seconds",
                "very_slow": ">45 seconds",
            },
        }
    except Exception as e:
        logger.warning(f"Failed to get provider timing summary: {e}")
        return {
            "metrics_available": False,
            "error": str(e),
            "note": "Provider timing metrics are exposed via Prometheus at /metrics endpoint",
        }


@router.get("/health")
async def diagnostics_health() -> dict[str, str]:
    """Health check endpoint for diagnostics API.

    Returns:
        dict: Simple health status
    """
    return {"status": "healthy", "service": "diagnostics"}
