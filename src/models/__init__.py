"""
Models package for health monitoring and availability
"""

# Import new health models
from .health_models import (
    HealthCheckRequest,
    HealthDashboardResponse,
    HealthStatus,
    HealthSummaryResponse,
    ModelAvailabilityRequest,
    ModelHealthResponse,
    ModelStatusResponse,
    ProviderAvailabilityRequest,
    ProviderHealthResponse,
    ProviderStatus,
    ProviderStatusResponse,
    SystemHealthResponse,
    UptimeMetricsResponse,
)

__all__ = [
    # Health models
    "HealthStatus",
    "ProviderStatus",
    "ModelHealthResponse",
    "ProviderHealthResponse",
    "SystemHealthResponse",
    "HealthSummaryResponse",
    "ModelAvailabilityRequest",
    "ProviderAvailabilityRequest",
    "HealthCheckRequest",
    "UptimeMetricsResponse",
    "ModelStatusResponse",
    "ProviderStatusResponse",
    "HealthDashboardResponse",
]
