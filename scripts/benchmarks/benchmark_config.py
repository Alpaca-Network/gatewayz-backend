"""
Benchmark configuration for GLM-4.5-Air evaluation.

This module defines model configurations, test parameters, and scoring weights
for benchmarking the GLM-4.5-Air distilled model for code router integration.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TestCategory(str, Enum):
    """Benchmark test categories."""

    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"


class Difficulty(str, Enum):
    """Test case difficulty levels."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class ModelConfig:
    """Configuration for a model being benchmarked."""

    model_id: str
    provider: str
    api_base_url: str
    api_key_env_var: str
    price_input_per_m: float  # USD per million input tokens
    price_output_per_m: float  # USD per million output tokens
    context_length: int = 128000
    supports_streaming: bool = True
    is_reasoning_model: bool = False  # Has separate reasoning field

    @property
    def api_key(self) -> str:
        """Get API key from environment."""
        return os.environ.get(self.api_key_env_var, "")


@dataclass
class BenchmarkConfig:
    """Main benchmark configuration."""

    # Run identification
    run_id: str = ""
    description: str = "GLM-4.5-Air Code Router Benchmark"

    # Model to benchmark
    model: ModelConfig = field(default_factory=lambda: SOUNDSGOOD_GLM_45_AIR)

    # Test parameters
    iterations_per_test: int = 3  # Iterations for statistical consistency
    warmup_iterations: int = 2  # Warmup before measurement

    # Category weights for final score (must sum to 1.0)
    category_weights: dict[TestCategory, float] = field(
        default_factory=lambda: {
            TestCategory.CODE_GENERATION: 0.35,
            TestCategory.REASONING: 0.30,
            TestCategory.DEBUGGING: 0.20,
            TestCategory.REFACTORING: 0.15,
        }
    )

    # Latency thresholds (seconds)
    ttfb_target_p50: float = 0.5  # 500ms
    ttfb_target_p95: float = 1.5  # 1500ms (reasoning models are slower)
    ttfc_target_p50: float = 0.6  # 600ms
    tps_target: float = 20.0  # tokens per second

    # Quality thresholds for tier placement
    tier_thresholds: dict[int, float] = field(
        default_factory=lambda: {
            2: 85.0,  # Tier 2: High Performance
            3: 75.0,  # Tier 3: Balanced
            4: 65.0,  # Tier 4: Economy
        }
    )

    # Output configuration
    output_dir: str = "benchmark_results"
    save_raw_responses: bool = True

    # Timeouts
    request_timeout_seconds: float = 120.0
    streaming_timeout_seconds: float = 180.0

    # Judge model for quality evaluation
    judge_model: str = "gpt-4o"
    judge_api_key_env: str = "OPENAI_API_KEY"


# Model configurations
SOUNDSGOOD_GLM_45_AIR = ModelConfig(
    model_id="zai-org/GLM-4.5-Air",
    provider="soundsgood",
    api_base_url="https://soundsgood.one/v1",
    api_key_env_var="SOUNDSGOOD_API_KEY",
    price_input_per_m=0.15,  # $0.15 per million input tokens
    price_output_per_m=4.13,  # $4.13 per million output tokens
    context_length=128000,
    supports_streaming=True,
    is_reasoning_model=True,  # Has reasoning field in response
)

# Baseline models for comparison (from code_quality_priors.json)
BASELINE_MODELS = {
    "tier_1": {
        "claude-3.5-sonnet": {"swe_bench": 49.0, "human_eval": 92.0},
        "openai/o1": {"swe_bench": 48.9, "human_eval": 94.4},
    },
    "tier_2": {
        "claude-3.5-haiku": {"swe_bench": 40.6, "human_eval": 88.1},
        "deepseek-v3": {"swe_bench": 42.0, "human_eval": 82.6},
        "gpt-4o": {"swe_bench": 38.4, "human_eval": 90.2},
    },
    "tier_3": {
        "qwen-2.5-coder-32b": {"swe_bench": 48.3, "human_eval": 86.2},
        "llama-3.3-70b": {"swe_bench": 43.2, "human_eval": 82.5},
    },
    "tier_4": {
        "zai/glm-4.7": {"swe_bench": 38.1, "human_eval": 78.4},
    },
}


@dataclass
class TestCase:
    """A single benchmark test case."""

    id: str
    category: TestCategory
    difficulty: Difficulty
    prompt: str
    expected_behavior: str
    evaluation_criteria: list[str]
    test_code: str | None = None  # For code_generation: test cases to run
    reference_answer: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """Result from a single test execution."""

    test_case: TestCase
    iteration: int

    # Response data
    response_content: str
    reasoning_content: str | None  # For reasoning models
    raw_response: dict[str, Any]

    # Timing metrics (seconds)
    ttfb_seconds: float
    ttfc_seconds: float | None  # None if non-streaming
    total_duration_seconds: float

    # Token metrics
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    # Quality score (0-100)
    quality_score: float
    quality_breakdown: dict[str, float]
    quality_feedback: str

    # Cost
    cost_usd: float

    # Status
    success: bool
    error_type: str | None = None
    error_message: str | None = None

    @property
    def tokens_per_second(self) -> float:
        """Calculate output tokens per second."""
        if self.total_duration_seconds > 0:
            return self.output_tokens / self.total_duration_seconds
        return 0.0


@dataclass
class CategoryResult:
    """Aggregated results for a test category."""

    category: TestCategory
    test_count: int
    pass_count: int
    fail_count: int

    # Quality scores
    avg_quality_score: float
    min_quality_score: float
    max_quality_score: float

    # Latency stats (seconds)
    ttfb_mean: float
    ttfb_p50: float
    ttfb_p95: float
    ttfb_p99: float

    ttfc_mean: float | None
    ttfc_p50: float | None
    ttfc_p95: float | None

    # Throughput
    tps_mean: float
    tps_p50: float

    # Cost
    total_cost_usd: float
    avg_cost_per_test: float

    # Error rate
    error_rate: float


@dataclass
class BenchmarkSummary:
    """Overall benchmark summary."""

    run_id: str
    model_id: str
    started_at: str
    completed_at: str
    total_duration_seconds: float

    # Overall scores
    weighted_quality_score: float
    category_results: dict[TestCategory, CategoryResult]

    # Tier recommendation
    recommended_tier: int
    tier_reasoning: str

    # Latency summary
    overall_ttfb_p95: float
    overall_tps_mean: float
    meets_latency_targets: bool

    # Cost summary
    total_cost_usd: float
    estimated_cost_per_1k_requests: float

    # Comparison to baselines
    baseline_comparison: dict[str, dict[str, float]]

    # Strengths identified
    strengths: list[str]
    weaknesses: list[str]
