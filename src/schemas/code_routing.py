"""
Code Routing Schemas

Pydantic models for code router request/response validation.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


# Router mode type
RouterModeType = Literal["auto", "price", "quality", "agentic"]


class CodeTaskClassification(BaseModel):
    """Classification result for a code-related task."""

    category: str = Field(
        ...,
        description="Task category (e.g., 'debugging', 'architecture', 'code_generation')",
    )
    complexity: str = Field(
        ...,
        description="Complexity level (e.g., 'low', 'medium', 'high', 'very_high')",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Classification confidence (0-1)",
    )
    default_tier: int = Field(
        ...,
        ge=1,
        le=4,
        description="Recommended model tier (1=premium, 4=economy)",
    )
    min_tier: int = Field(
        ...,
        ge=1,
        le=4,
        description="Minimum required tier (quality gate)",
    )
    classification_time_ms: float = Field(
        ...,
        ge=0,
        description="Time taken to classify in milliseconds",
    )
    category_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Scores for each category considered",
    )


class ModelInfo(BaseModel):
    """Information about a selected model."""

    name: str | None = Field(None, description="Human-readable model name")
    swe_bench: float | None = Field(None, description="SWE-Bench score (0-100)")
    human_eval: float | None = Field(None, description="HumanEval score (0-100)")
    price_input: float | None = Field(None, description="Input price per million tokens in USD")
    price_output: float | None = Field(None, description="Output price per million tokens in USD")


class SavingsDetail(BaseModel):
    """Savings detail against a specific baseline."""

    baseline_cost_usd: float = Field(..., description="Cost using baseline model")
    selected_cost_usd: float = Field(..., description="Cost using selected model")
    savings_usd: float = Field(..., ge=0, description="Savings in USD")
    savings_percent: float = Field(..., ge=0, description="Savings percentage")


class SavingsEstimate(BaseModel):
    """Estimated savings against multiple baselines."""

    claude_3_5_sonnet: SavingsDetail | None = Field(None, description="Savings vs Claude 3.5 Sonnet")
    gpt_4o: SavingsDetail | None = Field(None, description="Savings vs GPT-4o")
    user_default: SavingsDetail | None = Field(None, description="Savings vs user's default model")


class CodeRoutingResult(BaseModel):
    """Result from code routing decision."""

    model_id: str = Field(..., description="Selected model ID")
    provider: str = Field(..., description="Provider slug (e.g., 'openrouter', 'zai')")
    tier: int = Field(..., ge=1, le=4, description="Selected tier number")
    task_category: str = Field(..., description="Classified task category")
    complexity: str = Field(..., description="Classified complexity level")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Classification confidence")
    mode: RouterModeType = Field(..., description="Routing mode used")
    routing_latency_ms: float = Field(..., ge=0, description="Routing decision latency in ms")
    savings_estimate: dict[str, SavingsDetail] = Field(
        default_factory=dict,
        description="Savings estimates against baselines",
    )
    selected_model_info: ModelInfo = Field(
        default_factory=ModelInfo,
        description="Information about selected model",
    )


class CodeRoutingMetadata(BaseModel):
    """Routing metadata for inclusion in API response."""

    router_mode: str = Field(..., description="Full router mode string (e.g., 'code:price')")
    task_category: str = Field(..., description="Classified task category")
    complexity: str = Field(..., description="Classified complexity level")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Classification confidence")
    selected_model: str = Field(..., description="Selected model ID")
    selected_tier: int = Field(..., ge=1, le=4, description="Selected tier number")
    routing_latency_ms: float = Field(..., ge=0, description="Routing latency in ms")
    savings: dict[str, str] = Field(
        default_factory=dict,
        description="Formatted savings strings per baseline",
    )
    model_info: ModelInfo = Field(
        default_factory=ModelInfo,
        description="Information about selected model",
    )


class CodeRouterStatsResponse(BaseModel):
    """Statistics about code router performance."""

    success: bool = Field(..., description="Whether stats retrieval succeeded")
    stats: dict[str, Any] = Field(default_factory=dict, description="Router statistics")
    message: str | None = Field(None, description="Optional message")


class ModelTierConfig(BaseModel):
    """Configuration for a model tier."""

    name: str = Field(..., description="Tier name (e.g., 'Premium', 'Economy')")
    description: str = Field(..., description="Tier description")
    price_range: str = Field(..., description="Price range string")
    models: list[dict[str, Any]] = Field(default_factory=list, description="Models in this tier")


class BaselineModelConfig(BaseModel):
    """Configuration for a baseline model used for savings calculation."""

    model_id: str = Field(..., description="Model ID (e.g., 'anthropic/claude-3.5-sonnet')")
    price_input: float = Field(..., ge=0, description="Input price per million tokens in USD")
    price_output: float = Field(..., ge=0, description="Output price per million tokens in USD")
    description: str = Field("", description="Human-readable description")


class FallbackModelConfig(BaseModel):
    """Configuration for the fallback model."""

    id: str = Field(..., description="Model ID")
    provider: str = Field(..., description="Provider slug")
    reason: str = Field("", description="Reason for using this fallback")


class QualityPriors(BaseModel):
    """Quality priors configuration."""

    version: str = Field(..., description="Version string")
    last_updated: str = Field(..., description="Last updated date")
    benchmarks_source: list[str] = Field(default_factory=list, description="Benchmark sources")
    model_tiers: dict[str, ModelTierConfig] = Field(
        default_factory=dict,
        description="Model tier configurations",
    )
    task_taxonomy: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Task taxonomy definitions",
    )
    quality_gates: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Quality gate configurations",
    )
    fallback_model: FallbackModelConfig | dict[str, str] = Field(
        default_factory=dict,
        description="Fallback model configuration",
    )
    baselines: dict[str, BaselineModelConfig | dict[str, Any]] = Field(
        default_factory=dict,
        description="Baseline models for savings calculation",
    )
