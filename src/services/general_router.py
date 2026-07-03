"""
General-Purpose Prompt Router

Routes general prompts to models using per-mode heuristic fallback selection.

Modes:
- router:general          - Balanced (default)
- router:general:quality  - Optimize quality
- router:general:cost     - Optimize cost
- router:general:latency  - Optimize latency

Hyphenated aliases:
- gatewayz-general        → router:general
- gatewayz-general-quality → router:general:quality
- gatewayz-general-cost    → router:general:cost
- gatewayz-general-latency → router:general:latency
"""

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

RouterMode = Literal["balanced", "quality", "cost", "latency"]


def normalize_model_string(model_string: str) -> str:
    """
    Normalize hyphenated router aliases to colon-separated format.

    Args:
        model_string: Model string (may be hyphenated or colon-separated)

    Returns:
        Normalized model string in colon-separated format

    Examples:
        "gatewayz-general-quality" → "router:general:quality"
        "gatewayz-code-price" → "router:code:price"
        "router:general" → "router:general" (unchanged)
    """
    model_lower = model_string.lower().strip()

    # General router aliases - convert prefix and remaining hyphens to colons
    if model_lower.startswith("gatewayz-general"):
        normalized = model_lower.replace("gatewayz-general", "router:general", 1)
        # Convert any remaining hyphens to colons (e.g., -quality -> :quality)
        return normalized.replace("-", ":")

    # Code router aliases - convert prefix and remaining hyphens to colons
    if model_lower.startswith("gatewayz-code"):
        normalized = model_lower.replace("gatewayz-code", "router:code", 1)
        # Convert any remaining hyphens to colons (e.g., -price -> :price)
        return normalized.replace("-", ":")

    return model_string


def parse_router_model_string(model_string: str) -> tuple[bool, RouterMode]:
    """
    Parse general router model string to determine if it's a router request.

    Args:
        model_string: Model string (e.g., "router:general:quality")

    Returns:
        Tuple of (is_general_router, mode)

    Examples:
        "router:general" → (True, "balanced")
        "router:general:quality" → (True, "quality")
        "router:general:cost" → (True, "cost")
        "router:general:latency" → (True, "latency")
        "gpt-4" → (False, "balanced")
    """
    model_lower = model_string.lower()

    if not model_lower.startswith("router:general"):
        return (False, "balanced")

    parts = model_lower.split(":")
    if len(parts) == 2:
        # "router:general" → balanced mode
        return (True, "balanced")
    elif len(parts) == 3:
        # "router:general:<mode>"
        mode = parts[2].lower()
        if mode in ("quality", "cost", "latency"):
            return (True, mode)  # type: ignore
        else:
            logger.warning(f"Unknown router mode '{mode}', using balanced")
            return (True, "balanced")

    return (False, "balanced")


class GeneralRouter:
    """Routes general prompts to a model using heuristic fallback selection.

    The external ML router (NotDiamond) was removed; routing now always uses the
    per-mode fallback model configuration.
    """

    def __init__(self):
        """Initialize general router with fallback model configuration."""
        from src.services.general_router_fallback import get_fallback_models

        self.fallback_models = get_fallback_models()
        self.enabled = False
        logger.info("General router using heuristic fallback mode")

    async def route(
        self,
        messages: list[dict],
        mode: RouterMode = "balanced",
        context: dict[str, Any] | None = None,
        user_default_model: str | None = None,
    ) -> dict[str, Any]:
        """
        Route a general prompt to optimal model.

        Args:
            messages: List of message dicts with 'role' and 'content'
            mode: Routing mode (balanced, quality, cost, latency)
            context: Optional context dict (unused currently, reserved for future)
            user_default_model: Optional user's default model

        Returns:
            {
                "model_id": "openai/gpt-4o",
                "provider": "openai",
                "mode": "quality",
                "routing_latency_ms": 45.2,
                "fallback_used": True,
                "fallback_reason": "disabled",
            }
        """
        # Routing always uses the per-mode heuristic fallback selection.
        return self._use_fallback(mode, user_default_model, "disabled")

    def _use_fallback(
        self,
        mode: str,
        user_default: str | None,
        reason: str,
    ) -> dict[str, Any]:
        """
        Select a model using the per-mode heuristic fallback.

        Args:
            mode: Routing mode
            user_default: Optional user default model
            reason: Reason for fallback (disabled, model_unavailable, exception)

        Returns:
            Routing result dict with fallback model
        """
        from src.services.general_router_fallback import (
            get_fallback_model,
            get_fallback_provider,
        )

        fallback_model = get_fallback_model(mode, user_default)
        provider = get_fallback_provider(fallback_model)

        # Track fallback (optional - Prometheus may not be installed)
        try:
            from src.services.prometheus_metrics import track_general_router_fallback

            track_general_router_fallback(reason=reason, mode=mode)
        except ImportError:
            # Prometheus metrics are optional; skip tracking if not available
            logger.debug("Prometheus metrics not available, skipping fallback tracking")

        return {
            "model_id": fallback_model,
            "provider": provider,
            "mode": mode,
            "routing_latency_ms": 0.0,
            "fallback_used": True,
            "fallback_reason": reason,
        }

# Module-level singleton
_router: GeneralRouter | None = None


def get_router() -> GeneralRouter:
    """
    Get singleton router instance.

    Returns:
        Initialized GeneralRouter
    """
    global _router
    if _router is None:
        _router = GeneralRouter()
    return _router


async def route_general_prompt(
    messages: list[dict],
    mode: RouterMode = "balanced",
    context: dict[str, Any] | None = None,
    user_default_model: str | None = None,
) -> dict[str, Any]:
    """
    Convenience function to route a general prompt.

    Args:
        messages: List of message dicts
        mode: Routing mode
        context: Optional context
        user_default_model: Optional user default

    Returns:
        Routing result dict
    """
    return await get_router().route(messages, mode, context, user_default_model)


def get_routing_metadata(routing_result: dict[str, Any]) -> dict[str, Any]:
    """
    Format routing result as metadata for API response.

    Args:
        routing_result: Result from route_general_prompt()

    Returns:
        Formatted metadata dict for inclusion in API response
    """
    metadata = {
        "router": "general",
        "router_mode": routing_result["mode"],
        "selected_model": routing_result["model_id"],
        "routing_latency_ms": routing_result["routing_latency_ms"],
        "fallback_used": routing_result.get("fallback_used", False),
    }

    if not routing_result.get("fallback_used"):
        metadata["confidence"] = routing_result.get("confidence")
    else:
        metadata["fallback_reason"] = routing_result.get("fallback_reason")

    return metadata
