"""
Detailed Status Endpoint for Monitoring System Stability

Provides real-time visibility into internal server metrics:
- Concurrency (Active & Queued)
- Circuit Breaker States
- Cache Statistics
- Database Connectivity
"""

import logging
from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, Depends
from prometheus_client import REGISTRY

from src.security.deps import get_api_key
from src.services.circuit_breaker import get_all_circuit_breakers
from src.config.supabase_config import get_initialization_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["monitoring"])

def get_prometheus_metric(metric_name: str) -> float:
    """Helper to extract current value from Prometheus registry."""
    for metric in REGISTRY.collect():
        if metric.name == metric_name:
            # Gauges usually have one sample
            if metric.samples:
                return metric.samples[0].value
    return 0.0

@router.get("/detailed", response_model=dict[str, Any])
async def get_detailed_status(api_key: str = Depends(get_api_key)):
    """
    Get detailed internal system status.
    Requires an admin or valid API key.
    """
    try:
        # 1. Concurrency Metrics (from Prometheus gauges)
        active_requests = get_prometheus_metric("concurrency_active_requests")
        queued_requests = get_prometheus_metric("concurrency_queued_requests")

        # 2. Circuit Breaker States
        circuit_breakers = get_all_circuit_breakers()
        
        # 3. Database Status
        db_status = get_initialization_status()

        # 4. Redis/Cache Status (Attempt a simple ping via health cache)
        from src.services.simple_health_cache import simple_health_cache
        cache_available = simple_health_cache.get_system_health() is not None

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "concurrency": {
                "active": active_requests,
                "queued": queued_requests,
                "status": "normal" if active_requests < 15 else "high_load"
            },
            "circuit_breakers": {
                "total": len(circuit_breakers),
                "open": sum(1 for b in circuit_breakers.values() if b["state"] == "open"),
                "half_open": sum(1 for b in circuit_breakers.values() if b["state"] == "half_open"),
                "summary": circuit_breakers
            },
            "infrastructure": {
                "database": "connected" if not db_status["has_error"] else "error",
                "cache": "connected" if cache_available else "disconnected",
                "db_initialized": db_status["initialized"]
            }
        }
    except Exception as e:
        logger.error(f"Error generating detailed status: {e}")
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }
