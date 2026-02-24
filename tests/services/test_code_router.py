"""
Tests for Code-Optimized Prompt Router

Tests the code routing logic that selects optimal models based on
task classification and routing mode.
"""

import pytest

from src.services.code_router import (
    CodeRouter,
    get_baselines,
    get_fallback_model,
    get_model_tiers,
    get_router,
    get_routing_metadata,
    parse_router_model_string,
    route_code_prompt,
)


# Shared fixtures for code router tests
@pytest.fixture
def router():
    """Provide a fresh CodeRouter instance for tests."""
    return CodeRouter()


@pytest.mark.unit
class TestParseRouterModelString:
    """Test router model string parsing."""

    def test_parse_router_code_auto(self):
        """Test parsing 'router:code' as auto mode."""
        is_code, mode = parse_router_model_string("router:code")
        assert is_code is True
        assert mode == "auto"

    def test_parse_router_code_price(self):
        """Test parsing 'router:code:price' mode."""
        is_code, mode = parse_router_model_string("router:code:price")
        assert is_code is True
        assert mode == "price"

    def test_parse_router_code_quality(self):
        """Test parsing 'router:code:quality' mode."""
        is_code, mode = parse_router_model_string("router:code:quality")
        assert is_code is True
        assert mode == "quality"

    def test_parse_router_code_agentic(self):
        """Test parsing 'router:code:agentic' mode."""
        is_code, mode = parse_router_model_string("router:code:agentic")
        assert is_code is True
        assert mode == "agentic"

    def test_parse_non_router_model(self):
        """Test parsing regular model strings."""
        is_code, mode = parse_router_model_string("gpt-4")
        assert is_code is False
        assert mode == "auto"

    def test_parse_router_auto(self):
        """Test parsing general 'router:' prefix (not code router)."""
        is_code, mode = parse_router_model_string("router:auto")
        assert is_code is False  # Not router:code

    def test_parse_case_insensitive(self):
        """Test that parsing is case-insensitive."""
        is_code, mode = parse_router_model_string("ROUTER:CODE:PRICE")
        assert is_code is True
        assert mode == "price"

    def test_parse_unknown_mode(self):
        """Test parsing unknown mode falls back to auto."""
        is_code, mode = parse_router_model_string("router:code:unknown")
        assert is_code is True
        assert mode == "auto"


@pytest.mark.unit
class TestCodeRouter:
    """Test suite for CodeRouter class."""

    # ==================== Basic Routing Tests ====================

    def test_route_simple_task(self, router):
        """Test routing a simple code task."""
        result = router.route("Fix the typo in the variable name")
        assert "model_id" in result
        assert "tier" in result
        assert "task_category" in result
        assert result["tier"] >= 3  # Simple tasks use lower tiers

    def test_route_debugging_task(self, router):
        """Test routing a debugging task."""
        result = router.route("Debug the null pointer exception")
        assert result["task_category"] == "debugging"
        assert result["tier"] <= 2  # Quality gate enforces tier 2 minimum

    def test_route_architecture_task(self, router):
        """Test routing an architecture task."""
        result = router.route("Design the microservices architecture")
        assert result["task_category"] == "architecture"
        assert result["tier"] == 1  # Quality gate enforces tier 1

    def test_route_agentic_task(self, router):
        """Test routing an agentic task."""
        result = router.route("Build the complete authentication system")
        assert result["task_category"] == "agentic"
        assert result["tier"] == 1  # Quality gate enforces tier 1

    # ==================== Mode-Specific Tests ====================

    def test_route_price_mode(self, router):
        """Test routing in price optimization mode."""
        result = router.route("Write a function to calculate sum", mode="price")
        assert result["mode"] == "price"
        # Price mode should respect quality gates but prefer cheaper models

    def test_route_quality_mode(self, router):
        """Test routing in quality optimization mode."""
        result = router.route("Write a function to calculate sum", mode="quality")
        assert result["mode"] == "quality"
        # Quality mode bumps up the tier

    def test_route_agentic_mode(self, router):
        """Test routing in agentic mode."""
        result = router.route("Write a simple function", mode="agentic")
        assert result["mode"] == "agentic"
        assert result["tier"] == 1  # Agentic mode always uses tier 1

    def test_route_auto_mode(self, router):
        """Test routing in auto mode (default)."""
        result = router.route("Write a sorting function", mode="auto")
        assert result["mode"] == "auto"

    # ==================== Quality Gate Tests ====================

    def test_quality_gate_debugging(self, router):
        """Test that debugging tasks respect quality gate."""
        result = router.route("Debug the error", mode="price")
        assert result["tier"] <= 2  # Debugging has min_tier=2

    def test_quality_gate_architecture(self, router):
        """Test that architecture tasks respect quality gate."""
        result = router.route("Design the system architecture", mode="price")
        assert result["tier"] == 1  # Architecture has min_tier=1

    def test_quality_gate_agentic_task(self, router):
        """Test that agentic tasks respect quality gate."""
        result = router.route("Build entire feature end-to-end", mode="price")
        assert result["tier"] == 1  # Agentic has min_tier=1

    # ==================== Routing Result Structure Tests ====================

    def test_routing_result_structure(self, router):
        """Test that routing result has expected structure."""
        result = router.route("Write code")

        # Required fields
        assert "model_id" in result
        assert "provider" in result
        assert "tier" in result
        assert "task_category" in result
        assert "complexity" in result
        assert "confidence" in result
        assert "mode" in result
        assert "routing_latency_ms" in result
        assert "savings_estimate" in result
        assert "selected_model_info" in result

    def test_routing_latency(self, router):
        """Test that routing completes within target time."""
        result = router.route("Write a function")
        # Target is < 2ms, allow some margin for test environments
        assert result["routing_latency_ms"] < 50

    def test_savings_estimate_structure(self, router):
        """Test savings estimate structure."""
        result = router.route("Write code")
        savings = result["savings_estimate"]

        # Should have baseline comparisons
        for baseline in ["claude_3_5_sonnet", "gpt_4o"]:
            if baseline in savings:
                assert "baseline_cost_usd" in savings[baseline]
                assert "selected_cost_usd" in savings[baseline]
                assert "savings_usd" in savings[baseline]
                assert "savings_percent" in savings[baseline]

    # ==================== Context-Aware Routing Tests ====================

    def test_route_with_context(self, router):
        """Test routing with context."""
        context = {
            "file_count": 5,
            "has_error_trace": True,
            "conversation_length": 10,
        }
        result = router.route("Fix the issue", context=context)
        assert "model_id" in result

    def test_route_with_user_default_model(self, router):
        """Test routing with user's default model for comparison."""
        result = router.route(
            "Write a function",
            user_default_model="anthropic/claude-opus-4.5",
        )
        # Should calculate savings vs user default
        if "user_default" in result["savings_estimate"]:
            assert "savings_usd" in result["savings_estimate"]["user_default"]


@pytest.mark.unit
class TestModuleFunctions:
    """Test module-level convenience functions."""

    def test_get_router_singleton(self):
        """Test that get_router returns the same instance."""
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_route_code_prompt_function(self):
        """Test the convenience route_code_prompt function."""
        result = route_code_prompt("Write a sorting algorithm")
        assert "model_id" in result
        assert "tier" in result

    def test_get_routing_metadata(self):
        """Test get_routing_metadata formats correctly."""
        routing_result = route_code_prompt("Write code")
        metadata = get_routing_metadata(routing_result)

        assert "router_mode" in metadata
        assert "task_category" in metadata
        assert "selected_model" in metadata
        assert "savings" in metadata

    def test_get_model_tiers(self):
        """Test get_model_tiers returns valid data."""
        tiers = get_model_tiers()
        assert isinstance(tiers, dict)
        # Should have at least some tiers defined
        assert len(tiers) > 0

    def test_get_fallback_model(self):
        """Test get_fallback_model returns valid model."""
        fallback = get_fallback_model()
        assert "id" in fallback
        assert fallback["id"] == "zai/glm-4.7"

    def test_get_baselines(self):
        """Test get_baselines returns baseline configs."""
        baselines = get_baselines()
        assert isinstance(baselines, dict)


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_prompt(self, router):
        """Test routing with empty prompt."""
        result = router.route("")
        assert "model_id" in result
        # Should still return a valid result

    def test_very_long_prompt(self, router):
        """Test routing with very long prompt."""
        long_prompt = "debug " * 1000
        result = router.route(long_prompt)
        assert "model_id" in result

    def test_unicode_prompt(self, router):
        """Test routing with unicode characters."""
        result = router.route("修复这个错误")
        assert "model_id" in result

    def test_none_context(self, router):
        """Test routing with None context."""
        result = router.route("Fix bug", context=None)
        assert "model_id" in result

    def test_invalid_mode_defaults_to_auto(self, router):
        """Test that invalid mode is handled gracefully."""
        # This should not raise an error - use an invalid mode string
        # Note: Passing an invalid mode to route() is a type error at runtime,
        # but we test parse_router_model_string which handles invalid modes
        is_code, mode = parse_router_model_string("router:code:invalid_mode")
        assert is_code is True
        assert mode == "auto"  # Invalid mode falls back to auto


@pytest.mark.unit
class TestTierSelection:
    """Test tier selection logic in detail."""

    def test_tier_1_for_architecture(self, router):
        """Test that architecture always gets tier 1."""
        result = router.route("Design a scalable database architecture")
        assert result["tier"] == 1

    def test_tier_1_for_agentic_mode(self, router):
        """Test that agentic mode always selects tier 1."""
        result = router.route("Add a comment", mode="agentic")
        assert result["tier"] == 1

    def test_tier_respected_in_price_mode(self, router):
        """Test that quality gates are respected even in price mode."""
        result = router.route("Refactor this complex module", mode="price")
        # Refactoring has min_tier=2
        assert result["tier"] <= 2

    def test_quality_mode_bumps_tier(self, router):
        """Test that quality mode bumps up the tier."""
        auto_result = router.route("Write a function", mode="auto")
        quality_result = router.route("Write a function", mode="quality")

        # Quality mode should select equal or higher tier (lower number)
        assert quality_result["tier"] <= auto_result["tier"]
