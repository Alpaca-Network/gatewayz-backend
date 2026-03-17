"""
CM-4: Intelligent Routing Tests

Tests covering:
  4.1 General Router (parse_router_model_string, mode handling, selection logic)
  4.2 Code Router (parse_router_model_string, mode handling, SWE-bench, classification, tier matching)
"""

from unittest.mock import MagicMock, patch

import pytest

from src.schemas.general_router import (
    GeneralRouterSettings,
    RouteTestRequest,
)
from src.services.code_router import (
    CodeRouter,
)
from src.services.code_router import parse_router_model_string as code_parse
from src.services.general_router import parse_router_model_string as general_parse

# ===================================================================
# 4.1 General Router
# ===================================================================


@pytest.mark.cm_verified
class TestGeneralRouterParsing:
    """Tests for general router model string parsing and mode validation."""

    def test_general_router_parses_quality_mode(self):
        """CM-4.1.1: 'router:general:quality' parsed correctly."""
        is_router, mode = general_parse("router:general:quality")
        assert is_router is True
        assert mode == "quality"

    def test_general_router_parses_cost_mode(self):
        """CM-4.1.2: 'router:general:cost' parsed correctly."""
        is_router, mode = general_parse("router:general:cost")
        assert is_router is True
        assert mode == "cost"

    def test_general_router_parses_latency_mode(self):
        """CM-4.1.3: 'router:general:latency' parsed correctly."""
        is_router, mode = general_parse("router:general:latency")
        assert is_router is True
        assert mode == "latency"

    def test_general_router_parses_balanced_mode(self):
        """CM-4.1.4: 'router:general:balanced' or bare 'router:general' → balanced."""
        # Bare string defaults to balanced
        is_router, mode = general_parse("router:general")
        assert is_router is True
        assert mode == "balanced"

        # Explicit "balanced" is not a recognized sub-mode in the parser
        # (only quality/cost/latency are checked), so it falls back to balanced.
        is_router2, mode2 = general_parse("router:general:balanced")
        assert is_router2 is True
        assert mode2 == "balanced"

    def test_general_router_quality_settings_produce_quality_model_string(self):
        """CM-4.1.5: quality mode settings → model string that parses to quality mode."""
        settings = GeneralRouterSettings(
            use_general_router=True,
            optimization_mode="quality",
        )
        model_string = settings.get_model_string()
        assert model_string == "router:general:quality"

        # Verify the produced string drives correct mode in the parser
        is_router, mode = general_parse(model_string)
        assert is_router is True
        assert mode == "quality"

        # Disabled router returns manual model instead
        disabled = GeneralRouterSettings(use_general_router=False, optimization_mode="quality")
        assert not disabled.get_model_string().startswith("router:")

    def test_general_router_cost_settings_produce_cost_model_string(self):
        """CM-4.1.6: cost mode settings → model string that parses to cost mode."""
        settings = GeneralRouterSettings(
            use_general_router=True,
            optimization_mode="cost",
        )
        model_string = settings.get_model_string()
        assert model_string == "router:general:cost"

        is_router, mode = general_parse(model_string)
        assert is_router is True
        assert mode == "cost"

        # Each mode produces a distinct string
        quality_string = GeneralRouterSettings(
            use_general_router=True, optimization_mode="quality"
        ).get_model_string()
        assert model_string != quality_string

    def test_general_router_latency_settings_produce_latency_model_string(self):
        """CM-4.1.7: latency mode settings → model string that parses to latency mode."""
        settings = GeneralRouterSettings(
            use_general_router=True,
            optimization_mode="latency",
        )
        model_string = settings.get_model_string()
        assert model_string == "router:general:latency"

        is_router, mode = general_parse(model_string)
        assert is_router is True
        assert mode == "latency"

        # All three non-balanced modes produce distinct strings
        all_modes = ["quality", "cost", "latency"]
        strings = set()
        for m in all_modes:
            s = GeneralRouterSettings(
                use_general_router=True, optimization_mode=m
            ).get_model_string()
            strings.add(s)
        assert len(strings) == 3

    def test_general_router_invalid_mode_rejected(self):
        """CM-4.1.8: 'router:general:invalid' falls back; schema rejects invalid mode."""
        # Parser falls back to balanced for unknown modes
        is_router, mode = general_parse("router:general:invalid")
        assert is_router is True
        assert mode == "balanced"

        # RouteTestRequest schema rejects invalid mode with a ValueError
        with pytest.raises(ValueError, match="Invalid mode"):
            RouteTestRequest(
                messages=[{"role": "user", "content": "test"}],
                mode="invalid",
            )


# ===================================================================
# 4.2 Code Router
# ===================================================================


@pytest.mark.cm_verified
class TestCodeRouterParsing:
    """Tests for code router model string parsing and routing logic."""

    def test_code_router_parses_auto_mode(self):
        """CM-4.2.1: 'router:code:auto' or bare 'router:code' → auto."""
        # Bare string defaults to auto
        is_router, mode = code_parse("router:code")
        assert is_router is True
        assert mode == "auto"

        # Explicit auto is not listed in the valid sub-modes (price/quality/agentic),
        # so "router:code:auto" falls back to auto as the unknown-mode default.
        is_router2, mode2 = code_parse("router:code:auto")
        assert is_router2 is True
        assert mode2 == "auto"

    def test_code_router_parses_agentic_mode(self):
        """CM-4.2.2: 'router:code:agentic' parsed correctly."""
        is_router, mode = code_parse("router:code:agentic")
        assert is_router is True
        assert mode == "agentic"

    def test_code_router_parses_price_mode(self):
        """CM-4.2.3: 'router:code:price' parsed correctly."""
        is_router, mode = code_parse("router:code:price")
        assert is_router is True
        assert mode == "price"

    def test_code_router_parses_quality_mode(self):
        """CM-4.2.4: 'router:code:quality' parsed correctly."""
        is_router, mode = code_parse("router:code:quality")
        assert is_router is True
        assert mode == "quality"

    def test_code_router_uses_swe_bench_scores(self):
        """CM-4.2.5: quality mode factors in SWE-bench scores for model selection."""
        # Build a CodeRouter with controlled tier data containing SWE-bench scores
        mock_priors = {
            "model_tiers": {
                "1": {
                    "name": "Premium",
                    "models": [
                        {
                            "id": "model-a",
                            "name": "Model A",
                            "swe_bench": 30.0,
                            "human_eval": 85.0,
                            "price_input": 10.0,
                            "price_output": 30.0,
                            "strengths": ["code_generation"],
                            "provider": "test",
                        },
                        {
                            "id": "model-b",
                            "name": "Model B",
                            "swe_bench": 50.0,
                            "human_eval": 92.0,
                            "price_input": 15.0,
                            "price_output": 60.0,
                            "strengths": ["code_generation"],
                            "provider": "test",
                        },
                    ],
                },
            },
            "fallback_model": {"id": "fallback/model", "provider": "test"},
            "baselines": {},
        }

        with (
            patch("src.services.code_router._load_quality_priors", return_value=mock_priors),
            patch(
                "src.services.code_router.get_model_tiers", return_value=mock_priors["model_tiers"]
            ),
            patch(
                "src.services.code_router.get_fallback_model",
                return_value=mock_priors["fallback_model"],
            ),
            patch("src.services.code_router.get_baselines", return_value=mock_priors["baselines"]),
            patch("src.services.code_router.get_classifier") as mock_classifier_fn,
        ):

            mock_classifier = MagicMock()
            mock_classifier.classify.return_value = {
                "category": "code_generation",
                "complexity": "high",
                "confidence": 0.9,
                "default_tier": 1,
                "min_tier": 1,
                "classification_time_ms": 0.5,
                "category_scores": {},
            }
            mock_classifier_fn.return_value = mock_classifier

            router = CodeRouter()
            result = router.route("Write a complex module", mode="quality")

            # In quality mode, swe_bench score is factored in.
            # Model B has swe_bench=50.0 vs Model A's 30.0, so Model B should win.
            assert result["model_id"] == "model-b"
            assert result["selected_model_info"]["swe_bench"] == 50.0

    def test_code_router_classifies_task_complexity(self):
        """CM-4.2.6: classifier assigns complexity tiers to prompts."""
        # Use real classifier to verify it produces expected classification shape
        from src.services.code_classifier import CodeTaskClassifier

        classifier = CodeTaskClassifier()
        result = classifier.classify("debug this segfault in the memory allocator")

        # Verify classification returns expected fields
        assert "category" in result
        assert "complexity" in result
        assert result["complexity"] in ("low", "medium", "medium_high", "high", "very_high")
        assert "default_tier" in result
        assert 1 <= result["default_tier"] <= 4
        assert "min_tier" in result
        assert 1 <= result["min_tier"] <= 4
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0

    def test_code_router_matches_tier_to_model(self):
        """CM-4.2.7: higher complexity tier maps to higher-capability model."""
        mock_priors = {
            "model_tiers": {
                "1": {
                    "name": "Premium",
                    "models": [
                        {
                            "id": "premium/model",
                            "name": "Premium Model",
                            "swe_bench": 49.0,
                            "human_eval": 92.0,
                            "price_input": 15.0,
                            "price_output": 60.0,
                            "strengths": ["architecture", "reasoning"],
                            "provider": "test",
                        },
                    ],
                },
                "3": {
                    "name": "Standard",
                    "models": [
                        {
                            "id": "standard/model",
                            "name": "Standard Model",
                            "swe_bench": 25.0,
                            "human_eval": 70.0,
                            "price_input": 0.5,
                            "price_output": 1.5,
                            "strengths": ["code_generation"],
                            "provider": "test",
                        },
                    ],
                },
            },
            "fallback_model": {"id": "fallback/model", "provider": "test"},
            "baselines": {},
        }

        with (
            patch("src.services.code_router._load_quality_priors", return_value=mock_priors),
            patch(
                "src.services.code_router.get_model_tiers", return_value=mock_priors["model_tiers"]
            ),
            patch(
                "src.services.code_router.get_fallback_model",
                return_value=mock_priors["fallback_model"],
            ),
            patch("src.services.code_router.get_baselines", return_value=mock_priors["baselines"]),
            patch("src.services.code_router.get_classifier") as mock_classifier_fn,
        ):

            mock_classifier = MagicMock()
            mock_classifier_fn.return_value = mock_classifier

            # High-complexity task → tier 1 (premium)
            mock_classifier.classify.return_value = {
                "category": "architecture",
                "complexity": "very_high",
                "confidence": 0.95,
                "default_tier": 1,
                "min_tier": 1,
                "classification_time_ms": 0.3,
                "category_scores": {},
            }
            router = CodeRouter()
            high_result = router.route("Design a distributed system", mode="auto")
            assert high_result["model_id"] == "premium/model"
            assert high_result["tier"] == 1

            # Low-complexity task → tier 3 (standard)
            mock_classifier.classify.return_value = {
                "category": "code_generation",
                "complexity": "low",
                "confidence": 0.8,
                "default_tier": 3,
                "min_tier": 4,
                "classification_time_ms": 0.2,
                "category_scores": {},
            }
            low_result = router.route("Print hello world", mode="auto")
            assert low_result["model_id"] == "standard/model"
            assert low_result["tier"] == 3
