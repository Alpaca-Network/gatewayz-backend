"""Pydantic schemas for system health timeline endpoints"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ===========================
# Incident Schemas
# ===========================


class IncidentMetadata(BaseModel):
    """Metadata for an incident within a time bucket"""

    type: str | None = Field(
        None, description="Incident type (e.g., latency_spike, error_surge, downtime)"
    )
    region: str | None = Field(None, description="Affected region")
    error_rate: float | None = Field(None, description="Error rate percentage during incident")


class IncidentSummary(BaseModel):
    """Summary of incidents over the entire period"""

    total_incidents: int = Field(..., description="Total number of incidents in the period")
    mttr_minutes: float = Field(..., description="Mean time to recovery in minutes")
    worst_status: Literal["operational", "degraded", "downtime"] = Field(
        ..., description="Worst status observed in the period"
    )


# ===========================
# Uptime Sample Schemas
# ===========================


class UptimeSample(BaseModel):
    """Individual uptime sample for a time bucket"""

    timestamp: datetime = Field(..., description="Start timestamp of the time bucket")
    status: Literal["operational", "degraded", "downtime"] = Field(
        ..., description="Status during this time bucket"
    )
    duration_minutes: int = Field(..., description="Duration of the bucket in minutes")
    incident: IncidentMetadata | None = Field(None, description="Incident details if any occurred")


# ===========================
# Provider Uptime Schemas
# ===========================


class ProviderUptimeData(BaseModel):
    """Uptime data for a single provider"""

    provider: str = Field(..., description="Provider name (e.g., OpenAI, Anthropic)")
    gateway: str = Field(..., description="Gateway identifier")
    uptime_percentage: float = Field(..., description="Uptime percentage over the period")
    period_label: str = Field(
        ..., description="Human-readable period label (e.g., 'Last 72 hours')"
    )
    last_checked: datetime = Field(..., description="Timestamp of last health check")
    samples: list[UptimeSample] = Field(..., description="Array of uptime samples per time bucket")
    incident_summary: IncidentSummary = Field(
        ..., description="Summary of incidents over the period"
    )


class ProviderUptimeResponse(BaseModel):
    """Response for GET /health/providers/uptime"""

    success: bool = True
    providers: list[ProviderUptimeData]


# ===========================
# Model Uptime Schemas
# ===========================


class ModelUptimeData(BaseModel):
    """Uptime data for a single model"""

    model: str = Field(..., description="Model identifier (e.g., gpt-4, claude-3-opus)")
    provider: str = Field(..., description="Provider name")
    gateway: str = Field(..., description="Gateway identifier")
    uptime_percentage: float = Field(..., description="Uptime percentage over the period")
    period_label: str = Field(..., description="Human-readable period label")
    last_checked: datetime = Field(..., description="Timestamp of last health check")
    samples: list[UptimeSample] = Field(..., description="Array of uptime samples per time bucket")
    incident_summary: IncidentSummary = Field(
        ..., description="Summary of incidents over the period"
    )


class ModelUptimeResponse(BaseModel):
    """Response for GET /health/models/uptime"""

    success: bool = True
    models: list[ModelUptimeData]


# ===========================
# Gateway Uptime Schemas
# ===========================


class GatewayUptimeData(BaseModel):
    """Uptime data for a single gateway"""

    gateway: str = Field(..., description="Gateway name (e.g., openrouter, fireworks)")
    uptime_percentage: float = Field(..., description="Uptime percentage over the period")
    period_label: str = Field(..., description="Human-readable period label")
    last_checked: datetime = Field(..., description="Timestamp of last health check")
    total_providers: int = Field(..., description="Total number of providers on this gateway")
    healthy_providers: int = Field(..., description="Number of healthy providers")
    samples: list[UptimeSample] = Field(..., description="Array of uptime samples per time bucket")
    incident_summary: IncidentSummary = Field(
        ..., description="Summary of incidents over the period"
    )


class GatewayUptimeResponse(BaseModel):
    """Response for GET /health/gateways/uptime"""

    success: bool = True
    gateways: list[GatewayUptimeData]
