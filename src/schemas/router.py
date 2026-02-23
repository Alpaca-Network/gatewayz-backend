"""
Router schemas for prompt-level model routing.

Defines data models for the routing system that automatically selects
optimal models based on price/performance trade-offs.
"""

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Constants for routing thresholds
CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.6  # Minimum confidence to trust classification
HEALTH_DATA_STALE_SECONDS = 300  # 5 minutes - health data older than this is stale
MODEL_COOLDOWN_SECONDS = 60  # 1 minute cooldown after failures


class PromptCategory(str, Enum):  # noqa: UP042
    """Classification of prompt types for routing decisions."""

    SIMPLE_QA = "simple_qa"  # Short factual questions
    COMPLEX_REASONING = "complex_reasoning"  # Multi-step logic, analysis
    CODE_GENERATION = "code_generation"  # Writing code
    CODE_REVIEW = "code_review"  # Code analysis, debugging
    CREATIVE_WRITING = "creative_writing"  # Stories, content
    SUMMARIZATION = "summarization"  # Condensing information
    TRANSLATION = "translation"  # Language conversion
    MATH_CALCULATION = "math_calculation"  # Mathematical operations
    DATA_ANALYSIS = "data_analysis"  # Structured data work
    CONVERSATION = "conversation"  # Multi-turn chat
    TOOL_USE = "tool_use"  # Function calling scenarios
    UNKNOWN = "unknown"  # Catch-all for low-confidence classification


class RouterOptimization(str, Enum):  # noqa: UP042
    """Optimization target for model selection."""

    PRICE = "price"  # Optimize for lowest cost
    QUALITY = "quality"  # Optimize for best quality
    BALANCED = "balanced"  # Balance cost and quality
    FAST = "fast"  # Optimize for lowest latency


@dataclass
class RequiredCapabilities:
    """
    Capabilities extracted from request that gate model selection.
    Capability gating happens BEFORE scoring.
    """

    needs_tools: bool = False  # tools parameter present
    needs_json: bool = False  # response_format.type == "json_object"
    needs_json_schema: bool = False  # response_format.type == "json_schema"
    needs_vision: bool = False  # messages contain image content
    min_context_tokens: int = 0  # estimated from message length
    max_cost_per_1k: float | None = None  # from user preferences

    # Format constraints affecting model compatibility
    strict_json: bool = False  # must return valid JSON
    tool_schema_adherence: str = "any"  # "high", "medium", "any"


@dataclass
class ClassificationResult:
    """Result of prompt classification."""

    category: PromptCategory
    confidence: float  # 0.0 to 1.0
    signals: dict[str, Any] = field(default_factory=dict)  # Debug info

    @property
    def is_low_confidence(self) -> bool:
        """Check if confidence is below threshold for escalation."""
        return self.confidence < CLASSIFICATION_CONFIDENCE_THRESHOLD


@dataclass
class RouterDecision:
    """Complete routing decision with model and fallback chain."""

    selected_model: str
    selected_provider: str | None = None
    fallback_chain: list[tuple[str, str]] = field(default_factory=list)  # [(model, provider), ...]
    classification: ClassificationResult | None = None
    required_capabilities: RequiredCapabilities | None = None
    estimated_cost_per_1k: float | None = None
    decision_time_ms: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for logging/analytics."""
        return {
            "selected_model": self.selected_model,
            "selected_provider": self.selected_provider,
            "fallback_chain": self.fallback_chain,
            "category": self.classification.category.value if self.classification else None,
            "confidence": self.classification.confidence if self.classification else None,
            "decision_time_ms": self.decision_time_ms,
            "reason": self.reason,
        }


class UserRouterPreferences(BaseModel):
    """
    Per-user/API-key routing preferences.
    Stored in database and loaded on request.
    """

    default_optimization: RouterOptimization = Field(
        default=RouterOptimization.BALANCED,
        description="Default optimization target when not specified in request",
    )
    max_cost_per_1k_tokens: float | None = Field(
        default=None,
        description="Maximum cost per 1K tokens (input). None = no limit.",
    )
    excluded_providers: list[str] = Field(
        default_factory=list,
        description="Providers to exclude from routing decisions",
    )
    excluded_models: list[str] = Field(
        default_factory=list,
        description="Specific models to exclude from routing decisions",
    )
    preferred_models: list[str] = Field(
        default_factory=list,
        description="Models to prefer when scores are similar",
    )
    enabled: bool = Field(
        default=True,
        description="Whether auto-routing is enabled for this user",
    )

    class Config:
        use_enum_values = True


@dataclass
class ModelCapabilities:
    """
    Static capability metadata for a model.
    Used for capability gating before scoring.
    """

    model_id: str
    provider: str
    tools: bool = False
    json_mode: bool = False
    json_schema: bool = False
    vision: bool = False
    max_context: int = 8192
    tool_schema_adherence: str = "medium"  # "high", "medium", "low"
    cost_per_1k_input: float = 0.01
    cost_per_1k_output: float = 0.01

    def satisfies(self, required: RequiredCapabilities) -> bool:
        """Check if this model satisfies the required capabilities."""
        if required.needs_tools and not self.tools:
            return False
        if required.needs_json and not self.json_mode:
            return False
        if required.needs_json_schema and not self.json_schema:
            return False
        if required.needs_vision and not self.vision:
            return False
        if required.min_context_tokens > self.max_context:
            return False
        if required.max_cost_per_1k and self.cost_per_1k_input > required.max_cost_per_1k:
            return False
        if required.tool_schema_adherence == "high" and self.tool_schema_adherence != "high":
            return False
        return True


@dataclass
class ModelHealthSnapshot:
    """
    Pre-computed health status for a model.
    Written by background monitor, read by router.
    """

    model_id: str
    is_healthy: bool
    health_score: float  # 0-100
    consecutive_failures: int
    last_failure_at: datetime | None
    last_updated: datetime

    @property
    def is_stale(self) -> bool:
        """Check if health data is too old to trust."""
        age_seconds = (datetime.now(UTC) - self.last_updated).total_seconds()
        return age_seconds > HEALTH_DATA_STALE_SECONDS

    @property
    def in_cooldown(self) -> bool:
        """Check if model is in cooldown after failure."""
        if not self.last_failure_at:
            return False
        since_failure = (datetime.now(UTC) - self.last_failure_at).total_seconds()
        return since_failure < MODEL_COOLDOWN_SECONDS


@dataclass
class RoutingMetrics:
    """Metrics for a single routing decision (for analytics)."""

    request_id: str
    timestamp: datetime
    selected_model: str
    category: str
    confidence: float
    decision_time_ms: float
    optimization: str
    reason: str
    fallback_used: bool = False
    fallback_model: str | None = None
    estimated_cost: float | None = None
    actual_cost: float | None = None  # Filled after request completes
