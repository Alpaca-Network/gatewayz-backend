"""Tests for general router service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.general_router import (
    GeneralRouter,
    get_routing_metadata,
    normalize_model_string,
    parse_router_model_string,
    route_general_prompt,
)


class TestNormalizeModelString:
    """Test model string normalization."""

    def test_normalize_general_router_hyphenated(self):
        """Test normalizing gatewayz-general aliases."""
        assert normalize_model_string("gatewayz-general") == "router:general"
        assert normalize_model_string("gatewayz-general-quality") == "router:general:quality"
        assert normalize_model_string("gatewayz-general-cost") == "router:general:cost"
        assert normalize_model_string("gatewayz-general-latency") == "router:general:latency"

    def test_normalize_code_router_hyphenated(self):
        """Test normalizing gatewayz-code aliases."""
        assert normalize_model_string("gatewayz-code") == "router:code"
        assert normalize_model_string("gatewayz-code-price") == "router:code:price"
        assert normalize_model_string("gatewayz-code-quality") == "router:code:quality"
        assert normalize_model_string("gatewayz-code-agentic") == "router:code:agentic"

    def test_normalize_already_normalized(self):
        """Test that already normalized strings pass through unchanged."""
        assert normalize_model_string("router:general") == "router:general"
        assert normalize_model_string("router:general:quality") == "router:general:quality"
        assert normalize_model_string("router:code:price") == "router:code:price"

    def test_normalize_regular_model(self):
        """Test that regular model IDs pass through unchanged."""
        assert normalize_model_string("gpt-4") == "gpt-4"
        assert normalize_model_string("anthropic/claude-3-opus") == "anthropic/claude-3-opus"
        assert normalize_model_string("openai/gpt-4o") == "openai/gpt-4o"

    def test_normalize_case_insensitive(self):
        """Test case insensitivity."""
        assert normalize_model_string("GATEWAYZ-GENERAL") == "router:general"
        assert normalize_model_string("Gatewayz-General-Quality") == "router:general:quality"


class TestParseRouterModelString:
    """Test router model string parsing."""

    def test_parse_router_general_balanced(self):
        """Test parsing router:general (balanced mode)."""
        is_router, mode = parse_router_model_string("router:general")
        assert is_router is True
        assert mode == "balanced"

    def test_parse_router_general_quality(self):
        """Test parsing router:general:quality."""
        is_router, mode = parse_router_model_string("router:general:quality")
        assert is_router is True
        assert mode == "quality"

    def test_parse_router_general_cost(self):
        """Test parsing router:general:cost."""
        is_router, mode = parse_router_model_string("router:general:cost")
        assert is_router is True
        assert mode == "cost"

    def test_parse_router_general_latency(self):
        """Test parsing router:general:latency."""
        is_router, mode = parse_router_model_string("router:general:latency")
        assert is_router is True
        assert mode == "latency"

    def test_parse_invalid_mode(self):
        """Test parsing invalid mode falls back to balanced."""
        is_router, mode = parse_router_model_string("router:general:invalid")
        assert is_router is True
        assert mode == "balanced"

    def test_parse_non_router_string(self):
        """Test parsing non-router strings."""
        is_router, mode = parse_router_model_string("gpt-4")
        assert is_router is False
        assert mode == "balanced"

    def test_parse_code_router_string(self):
        """Test that code router strings are not matched."""
        is_router, mode = parse_router_model_string("router:code")
        assert is_router is False
        assert mode == "balanced"

    def test_parse_case_insensitive(self):
        """Test case insensitivity."""
        is_router, mode = parse_router_model_string("ROUTER:GENERAL:QUALITY")
        assert is_router is True
        assert mode == "quality"


class TestGeneralRouterRoute:
    """Test GeneralRouter.route method."""

    @pytest.mark.asyncio
    async def test_route_uses_fallback_quality(self):
        """Router always uses heuristic fallback selection (quality mode)."""
        router = GeneralRouter()

        result = await router.route(messages=[{"role": "user", "content": "test"}], mode="quality")

        assert result["fallback_used"] is True
        assert result["fallback_reason"] == "disabled"
        assert result["model_id"] == "openai/gpt-4o"  # quality mode fallback

    @pytest.mark.asyncio
    async def test_route_uses_fallback_cost(self):
        """Router always uses heuristic fallback selection (cost mode)."""
        router = GeneralRouter()

        result = await router.route(messages=[{"role": "user", "content": "test"}], mode="cost")

        assert result["fallback_used"] is True
        assert result["fallback_reason"] == "disabled"
        assert result["model_id"] == "openai/gpt-4o-mini"  # cost mode fallback


class TestRoutingMetadata:
    """Test routing metadata formatting."""

    def test_get_routing_metadata_success(self):
        """Test metadata formatting for successful (non-fallback) routing."""
        routing_result = {
            "model_id": "openai/gpt-4o",
            "provider": "openai",
            "mode": "quality",
            "routing_latency_ms": 45.2,
            "confidence": 0.95,
            "fallback_used": False,
        }

        metadata = get_routing_metadata(routing_result)

        assert metadata["router"] == "general"
        assert metadata["router_mode"] == "quality"
        assert metadata["selected_model"] == "openai/gpt-4o"
        assert metadata["routing_latency_ms"] == 45.2
        assert metadata["confidence"] == 0.95
        assert metadata["fallback_used"] is False

    def test_get_routing_metadata_fallback(self):
        """Test metadata formatting for fallback routing."""
        routing_result = {
            "model_id": "openai/gpt-4o-mini",
            "provider": "openai",
            "mode": "cost",
            "routing_latency_ms": 0.0,
            "fallback_used": True,
            "fallback_reason": "disabled",
        }

        metadata = get_routing_metadata(routing_result)

        assert metadata["router"] == "general"
        assert metadata["fallback_used"] is True
        assert metadata["fallback_reason"] == "disabled"
        assert "notdiamond_session_id" not in metadata
        assert "confidence" not in metadata


@pytest.mark.asyncio
async def test_route_general_prompt_convenience_function():
    """Test the convenience function route_general_prompt."""
    with patch("src.services.general_router.get_router") as mock_get_router:
        mock_router = MagicMock()
        mock_router.route = AsyncMock(
            return_value={
                "model_id": "test/model",
                "provider": "test",
                "mode": "balanced",
                "routing_latency_ms": 10.0,
                "fallback_used": False,
            }
        )
        mock_get_router.return_value = mock_router

        result = await route_general_prompt(
            messages=[{"role": "user", "content": "test"}], mode="balanced"
        )

        assert result["model_id"] == "test/model"
        mock_router.route.assert_called_once()
