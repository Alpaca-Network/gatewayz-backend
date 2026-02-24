"""
General-Purpose Prompt Router (NotDiamond-Powered)

Routes general prompts to optimal models using NotDiamond's ML-based selection.

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
import time
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
    """Routes general prompts using NotDiamond API."""

    def __init__(self):
        """Initialize general router with NotDiamond client."""
        from src.services.general_router_fallback import get_fallback_models
        from src.services.notdiamond_client import get_notdiamond_client

        self.notdiamond_client = get_notdiamond_client()
        self.fallback_models = get_fallback_models()
        self.enabled = self.notdiamond_client.enabled

        if not self.enabled:
            logger.info("NotDiamond client not enabled, general router will use fallback mode")

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
                "notdiamond_session_id": "nd_xxx",
                "confidence": 0.95,
                "fallback_used": False,
                "selected_model_info": {...}
            }
        """
        start_time = time.perf_counter()

        # Check if NotDiamond is enabled
        if not self.enabled:
            logger.info("NotDiamond disabled, using fallback")
            return self._use_fallback(mode, user_default_model, "disabled")

        # Try NotDiamond routing
        try:
            nd_result = await self.notdiamond_client.select_model(
                messages=messages,
                mode=mode,
            )

            # Check if selected model is available in Gatewayz
            model_available = await self._check_model_available(nd_result["model_id"])

            if not model_available:
                logger.warning(f"NotDiamond selected unavailable model: {nd_result['model_id']}")
                return self._use_fallback(mode, user_default_model, "model_unavailable")

            # Success - calculate total latency
            routing_latency_ms = (time.perf_counter() - start_time) * 1000

            result = {
                "model_id": nd_result["model_id"],
                "provider": nd_result["provider"],
                "mode": mode,
                "routing_latency_ms": routing_latency_ms,
                "notdiamond_session_id": nd_result["session_id"],
                "notdiamond_latency_ms": nd_result["latency_ms"],
                "confidence": nd_result["confidence"],
                "fallback_used": False,
                "selected_model_info": {
                    "notdiamond_model": nd_result["notdiamond_model"],
                },
            }

            # Track metrics
            self._track_routing_metrics(result)

            logger.info(
                f"General router selected {result['model_id']} "
                f"(mode={mode}, confidence={result['confidence']:.2f}, "
                f"time={routing_latency_ms:.2f}ms)"
            )

            return result

        except Exception as e:
            logger.warning(f"NotDiamond routing failed: {e}")
            return self._use_fallback(mode, user_default_model, "exception")

    def _use_fallback(
        self,
        mode: str,
        user_default: str | None,
        reason: str,
    ) -> dict[str, Any]:
        """
        Use fallback model when NotDiamond unavailable.

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

    async def _check_model_available(self, model_id: str) -> bool:
        """
        Check if model is available in Gatewayz catalog.

        Args:
            model_id: Gatewayz model ID

        Returns:
            True if model is available, False otherwise
        """
        try:
            from src.services.models import get_cached_models

            models = get_cached_models()
            return any(m.get("id") == model_id for m in models)
        except Exception as e:
            logger.warning(f"Failed to check model availability: {e}")
            return True  # Assume available on error (fail open)

    def _track_routing_metrics(self, result: dict[str, Any]) -> None:
        """
        Track routing metrics to Prometheus.

        Args:
            result: Routing result dict
        """
        try:
            from src.services.prometheus_metrics import track_general_router_request

            track_general_router_request(
                mode=result["mode"],
                selected_model=result["model_id"],
                provider=result["provider"],
                latency_seconds=result["routing_latency_ms"] / 1000,
                confidence=result.get("confidence", 0),
            )
        except (ImportError, Exception) as e:
            logger.debug(f"Failed to track metrics: {e}")


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
        metadata.update(
            {
                "notdiamond_session_id": routing_result.get("notdiamond_session_id"),
                "confidence": routing_result.get("confidence"),
            }
        )
    else:
        metadata["fallback_reason"] = routing_result.get("fallback_reason")

    return metadata
