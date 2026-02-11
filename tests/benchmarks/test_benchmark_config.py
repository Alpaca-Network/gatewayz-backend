"""Tests for benchmark configuration module."""

import sys
from pathlib import Path

import pytest

# Add benchmark scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "benchmarks"))

from benchmark_config import (
    BASELINE_MODELS,
    SOUNDSGOOD_GLM_45_AIR,
    BenchmarkConfig,
    CategoryResult,
    Difficulty,
    ModelConfig,
    TestCase,
    TestCategory,
    TestResult,
)


class TestModelConfig:
    """Tests for ModelConfig dataclass."""

    def test_soundsgood_config_values(self):
        """Test that Soundsgood GLM config has correct values."""
        config = SOUNDSGOOD_GLM_45_AIR

        assert config.model_id == "zai-org/GLM-4.5-Air"
        assert config.provider == "soundsgood"
        assert config.api_base_url == "https://soundsgood.one/v1"
        assert config.api_key_env_var == "SOUNDSGOOD_API_KEY"
        assert config.price_input_per_m == 0.15
        assert config.price_output_per_m == 4.13
        assert config.context_length == 128000
        assert config.supports_streaming is True
        assert config.is_reasoning_model is True

    def test_api_key_from_env(self, monkeypatch):
        """Test that API key is read from environment."""
        monkeypatch.setenv("SOUNDSGOOD_API_KEY", "test_key_123")
        config = SOUNDSGOOD_GLM_45_AIR
        assert config.api_key == "test_key_123"

    def test_api_key_empty_when_not_set(self, monkeypatch):
        """Test that API key is empty when env var not set."""
        monkeypatch.delenv("SOUNDSGOOD_API_KEY", raising=False)
        config = SOUNDSGOOD_GLM_45_AIR
        assert config.api_key == ""

    def test_custom_model_config(self):
        """Test creating a custom ModelConfig."""
        config = ModelConfig(
            model_id="test/model",
            provider="test_provider",
            api_base_url="https://test.api.com/v1",
            api_key_env_var="TEST_API_KEY",
            price_input_per_m=0.10,
            price_output_per_m=0.20,
            context_length=4096,
            supports_streaming=False,
            is_reasoning_model=False,
        )

        assert config.model_id == "test/model"
        assert config.provider == "test_provider"
        assert config.context_length == 4096
        assert config.supports_streaming is False


class TestBenchmarkConfig:
    """Tests for BenchmarkConfig dataclass."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        config = BenchmarkConfig()

        assert config.iterations_per_test == 3
        assert config.warmup_iterations == 2
        assert config.ttfb_target_p50 == 0.5
        assert config.ttfb_target_p95 == 1.5
        assert config.tps_target == 20.0
        assert config.output_dir == "benchmark_results"
        assert config.save_raw_responses is True

    def test_category_weights_sum_to_one(self):
        """Test that category weights sum to 1.0."""
        config = BenchmarkConfig()
        total_weight = sum(config.category_weights.values())
        assert abs(total_weight - 1.0) < 0.01

    def test_tier_thresholds(self):
        """Test tier threshold values."""
        config = BenchmarkConfig()

        assert config.tier_thresholds[2] == 85.0
        assert config.tier_thresholds[3] == 75.0
        assert config.tier_thresholds[4] == 65.0

    def test_custom_run_id(self):
        """Test setting custom run ID."""
        config = BenchmarkConfig(run_id="test_run_001")
        assert config.run_id == "test_run_001"


class TestTestCategory:
    """Tests for TestCategory enum."""

    def test_all_categories_defined(self):
        """Test that all expected categories are defined."""
        categories = list(TestCategory)
        assert len(categories) == 4

        assert TestCategory.CODE_GENERATION in categories
        assert TestCategory.REASONING in categories
        assert TestCategory.DEBUGGING in categories
        assert TestCategory.REFACTORING in categories

    def test_category_values(self):
        """Test category string values."""
        assert TestCategory.CODE_GENERATION.value == "code_generation"
        assert TestCategory.REASONING.value == "reasoning"
        assert TestCategory.DEBUGGING.value == "debugging"
        assert TestCategory.REFACTORING.value == "refactoring"


class TestDifficulty:
    """Tests for Difficulty enum."""

    def test_all_difficulties_defined(self):
        """Test that all difficulty levels are defined."""
        difficulties = list(Difficulty)
        assert len(difficulties) == 3

        assert Difficulty.EASY in difficulties
        assert Difficulty.MEDIUM in difficulties
        assert Difficulty.HARD in difficulties


class TestTestCase:
    """Tests for TestCase dataclass."""

    def test_create_test_case(self):
        """Test creating a test case."""
        tc = TestCase(
            id="test_001",
            category=TestCategory.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            prompt="Write hello world",
            expected_behavior="Should print hello world",
            evaluation_criteria=["correctness", "style"],
            test_code="assert func() == 'hello world'",
            reference_answer="print('hello world')",
            tags=["easy", "beginner"],
        )

        assert tc.id == "test_001"
        assert tc.category == TestCategory.CODE_GENERATION
        assert tc.difficulty == Difficulty.EASY
        assert len(tc.evaluation_criteria) == 2
        assert tc.test_code is not None
        assert len(tc.tags) == 2

    def test_test_case_optional_fields(self):
        """Test that optional fields default correctly."""
        tc = TestCase(
            id="test_002",
            category=TestCategory.DEBUGGING,
            difficulty=Difficulty.MEDIUM,
            prompt="Find the bug",
            expected_behavior="Fix the bug",
            evaluation_criteria=["bug_found"],
        )

        assert tc.test_code is None
        assert tc.reference_answer is None
        assert tc.tags == []


class TestTestResult:
    """Tests for TestResult dataclass."""

    def test_tokens_per_second_calculation(self):
        """Test TPS calculation."""
        tc = TestCase(
            id="test_001",
            category=TestCategory.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            prompt="test",
            expected_behavior="test",
            evaluation_criteria=[],
        )

        result = TestResult(
            test_case=tc,
            iteration=0,
            response_content="response",
            reasoning_content=None,
            raw_response={},
            ttfb_seconds=0.5,
            ttfc_seconds=None,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=50,
            quality_score=85.0,
            quality_breakdown={},
            quality_feedback="Good",
            cost_usd=0.01,
            success=True,
        )

        # 200 output tokens / 2.0 seconds = 100 TPS
        assert result.tokens_per_second == 100.0

    def test_tokens_per_second_zero_duration(self):
        """Test TPS with zero duration returns 0."""
        tc = TestCase(
            id="test_001",
            category=TestCategory.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            prompt="test",
            expected_behavior="test",
            evaluation_criteria=[],
        )

        result = TestResult(
            test_case=tc,
            iteration=0,
            response_content="",
            reasoning_content=None,
            raw_response={},
            ttfb_seconds=0.0,
            ttfc_seconds=None,
            total_duration_seconds=0.0,  # Zero duration
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            quality_score=0.0,
            quality_breakdown={},
            quality_feedback="Failed",
            cost_usd=0.0,
            success=False,
        )

        assert result.tokens_per_second == 0.0


class TestBaselineModels:
    """Tests for baseline model definitions."""

    def test_baseline_tiers_exist(self):
        """Test that all tier levels are defined."""
        assert "tier_1" in BASELINE_MODELS
        assert "tier_2" in BASELINE_MODELS
        assert "tier_3" in BASELINE_MODELS
        assert "tier_4" in BASELINE_MODELS

    def test_tier_1_models(self):
        """Test tier 1 model definitions."""
        tier_1 = BASELINE_MODELS["tier_1"]
        assert "claude-3.5-sonnet" in tier_1
        assert "openai/o1" in tier_1

        # Check metrics exist
        for model_name, metrics in tier_1.items():
            assert "swe_bench" in metrics
            assert "human_eval" in metrics
            assert metrics["swe_bench"] > 0
            assert metrics["human_eval"] > 0

    def test_tier_2_models(self):
        """Test tier 2 model definitions."""
        tier_2 = BASELINE_MODELS["tier_2"]
        assert len(tier_2) >= 2

    def test_baseline_score_ordering(self):
        """Test that tier 1 models generally score higher than tier 4."""
        tier_1_scores = [m["human_eval"] for m in BASELINE_MODELS["tier_1"].values()]
        tier_4_scores = [m["human_eval"] for m in BASELINE_MODELS["tier_4"].values()]

        avg_tier_1 = sum(tier_1_scores) / len(tier_1_scores)
        avg_tier_4 = sum(tier_4_scores) / len(tier_4_scores)

        assert avg_tier_1 > avg_tier_4
