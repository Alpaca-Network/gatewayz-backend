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


class GpuPublicEmission(BaseModel):
    """Aggregate-only WAYZ emission info (Chutes-style WAYZ emission
    rewards, gatewayz-backend tokenomics) -- config + the most recent
    epoch_date, nothing per-provider or per-user. See
    docs/tokenomics/EMISSION.md."""

    mode: str = Field(description="'per_unit' or 'emission' -- Config.REWARDS_MODE")
    daily_emission_wayz: str
    provider_pool_usd_per_day: str = Field(
        default="0", description="USD/day allocated to providers by score, paid in ETH on Base"
    )
    provider_payout_asset: str = Field(default="ETH", description="Provider payout asset")
    providers_bps: int
    stakers_bps: int
    treasury_bps: int
    last_epoch: str | None = Field(
        description="ISO-8601 date of the most recent emission_epochs row, or null if none has run yet"
    )


class GpuPublicSummary(BaseModel):
    """GET /gpu/public/summary"""

    active_nodes: int = Field(
        description=(
            "Live snapshot: count of gpu_nodes currently in status='active' "
            "(approved providers only) at request time -- NOT an hourly "
            "aggregate. Contrast with GpuUtilizationPoint.active_nodes, "
            "which counts nodes with traffic in a specific past hour. See "
            "docs/gpu/PUBLIC_FEED.md."
        )
    )
    approved_providers: int
    regions: list[GpuRegionCount]
    models: list[GpuModelCount]
    last_hour: GpuLastHourStats
    updated_at: str = Field(description="ISO-8601 UTC timestamp, e.g. 2026-09-03T18:00:00+00:00")
    emission: GpuPublicEmission


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
    active_nodes: int = Field(
        description=(
            "Nodes that completed at least one provider_work request in "
            "THIS hour bucket -- a historical traffic count, NOT a live "
            "status snapshot. Contrast with GpuPublicSummary.active_nodes, "
            "which reflects current gpu_nodes.status. See "
            "docs/gpu/PUBLIC_FEED.md."
        )
    )


class GpuPublicUtilizationResponse(BaseModel):
    """GET /gpu/public/utilization?window=24h|7d&group=region|model"""

    window: str
    group: str
    series: list[GpuUtilizationPoint]
