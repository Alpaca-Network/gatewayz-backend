"""Auto-route alias detection — `router*` plus OpenRouter-style `auto` ergonomics.

`openrouter/auto` is intentionally NOT hijacked: it is a real passthrough model
served by OpenRouter, so it must reach the provider unchanged.
"""

from __future__ import annotations

from src.services.prompt_router import (
    RouterOptimization,
    is_auto_route_request,
    parse_auto_route_options,
)


class TestIsAutoRouteRequest:
    def test_router_prefix_triggers(self):
        assert is_auto_route_request("router")
        assert is_auto_route_request("router:price")
        assert is_auto_route_request("ROUTER:code")

    def test_bare_auto_triggers(self):
        assert is_auto_route_request("auto")
        assert is_auto_route_request("Auto")
        assert is_auto_route_request("auto:quality")

    def test_gatewayz_auto_triggers(self):
        assert is_auto_route_request("gatewayz/auto")

    def test_openrouter_auto_is_passthrough(self):
        # Real OpenRouter model — must NOT be captured by our router.
        assert not is_auto_route_request("openrouter/auto")

    def test_concrete_models_passthrough(self):
        assert not is_auto_route_request("openai/gpt-4o")
        assert not is_auto_route_request("anthropic/claude-3.5-sonnet")
        assert not is_auto_route_request("")
        assert not is_auto_route_request(None)


class TestParseAutoRouteOptions:
    def test_bare_auto_matches_bare_router(self):
        assert parse_auto_route_options("auto") == parse_auto_route_options("router")

    def test_auto_suffix_matches_router_suffix(self):
        assert parse_auto_route_options("auto:price") == (
            "small",
            RouterOptimization.PRICE,
        )
        assert parse_auto_route_options("auto:quality") == (
            "medium",
            RouterOptimization.QUALITY,
        )

    def test_gatewayz_auto_bare(self):
        assert parse_auto_route_options("gatewayz/auto") == parse_auto_route_options(
            "router"
        )
