"""Pydantic response models for the public GPU transparency feed
(gatewayz-backend#2263 #2264, spec §6).

These models are the single source of truth for both the live JSON
responses served by src/routes/gpu_public.py and the published JSON Schema
at GET /gpu/public/schema and docs/gpu/public-feed.schema.json --
build_public_feed_schema() in that route module generates the schema from
these classes' model_json_schema(), so the two can never drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class GpuRegionCount(BaseModel):
    region: str
    nodes: int


class GpuModelCount(BaseModel):
    id: str
    nodes: int


class GpuLastHourStats(BaseModel):
    requests: int
    tokens: int
    avg_latency_ms: int
    error_rate: float = Field(description="Share of last_hour requests with status='failed', 0-1")


class GpuPublicSummary(BaseModel):
    """GET /gpu/public/summary"""

    active_nodes: int
    approved_providers: int
    regions: list[GpuRegionCount]
    models: list[GpuModelCount]
    last_hour: GpuLastHourStats
    updated_at: str = Field(description="ISO-8601 UTC timestamp, e.g. 2026-09-03T18:00:00+00:00")


class GpuPublicNode(BaseModel):
    """One row of GET /gpu/public/nodes.

    Deliberately excludes wallet address, endpoint URL, node token, and
    provider identity -- see tests/security/test_gpu_public_aggregate_only.py.
    """

    name: str
    region: str
    gpu_model: str
    vram_gb: int
    status: str
    uptime_24h_pct: float = Field(
        description=(
            "Approximation: share of the last 24 hourly rollup buckets in "
            "which any node serving this node's (region, model) pair was "
            "active. See docs/gpu/PUBLIC_FEED.md."
        )
    )
    models: list[str]


GpuPublicNodesResponse = RootModel[list[GpuPublicNode]]


class GpuUtilizationPoint(BaseModel):
    """One hourly bucket of GET /gpu/public/utilization."""

    hour: str = Field(description="ISO-8601 UTC hour bucket start, e.g. 2026-09-03T17:00:00+00:00")
    key: str = Field(description="The region or model value this bucket is grouped by")
    requests: int
    prompt_tokens: int
    completion_tokens: int
    avg_latency_ms: int
    error_rate: float
    active_nodes: int


class GpuPublicUtilizationResponse(BaseModel):
    """GET /gpu/public/utilization?window=24h|7d&group=region|model"""

    window: str
    group: str
    series: list[GpuUtilizationPoint]
