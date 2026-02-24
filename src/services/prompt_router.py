"""
Prompt Router - Main Orchestration Service.

Implements fail-open prompt-level routing with:
- Hard timeout (2ms) - returns default if exceeded
- Capability gating before scoring
- Health snapshot reads (single Redis GET)
- Stable model selection
- Compatible fallback chains

This is the main entry point for prompt-level routing.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.schemas.router import (
    ModelCapabilities,
    RequiredCapabilities,
    RouterDecision,
    RouterOptimization,
    UserRouterPreferences,
)
from src.services.capability_gating import extract_capabilities, filter_by_capabilities
from src.services.fallback_chain import build_fallback_chain
from src.services.health_snapshots import get_healthy_models_sync
from src.services.model_selector import select_model
from src.services.prompt_classifier_rules import classify_prompt

logger = logging.getLogger(__name__)

# Hard timeout for routing decision (milliseconds)
# If exceeded, fail open to default model
ROUTER_TIMEOUT_MS = 2.0

# Default cheap model for fail-open
DEFAULT_CHEAP_MODEL = "openai/gpt-4o-mini"
DEFAULT_PROVIDER = "openai"

# Curated list of known-stable models for fallback when health data unavailable
# These are high-reliability models that should always work
STABLE_FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-1.5-flash",
]

# Path to model capabilities data
CAPABILITIES_DATA_PATH = Path(__file__).parent.parent / "data" / "model_capabilities.json"


class PromptRouter:
    """
    Main prompt router with fail-open behavior.

    Usage:
        router = PromptRouter()
        decision = router.route(request)
        # Use decision.selected_model and decision.fallback_chain
    """

    def __init__(self):
        self._capabilities_registry: dict[str, ModelCapabilities] = {}
        self._load_capabilities()

    def _load_capabilities(self) -> None:
        """Load model capabilities from JSON file."""
        try:
            if CAPABILITIES_DATA_PATH.exists():
                with open(CAPABILITIES_DATA_PATH) as f:
                    data = json.load(f)

                for model_id, caps in data.get("models", {}).items():
                    self._capabilities_registry[model_id] = ModelCapabilities(
                        model_id=model_id,
                        provider=caps.get("provider", "unknown"),
                        tools=caps.get("tools", False),
                        json_mode=caps.get("json_mode", False),
                        json_schema=caps.get("json_schema", False),
                        vision=caps.get("vision", False),
                        max_context=caps.get("max_context", 8192),
                        tool_schema_adherence=caps.get("tool_schema_adherence", "medium"),
                        cost_per_1k_input=caps.get("cost_per_1k_input", 0.01),
                        cost_per_1k_output=caps.get("cost_per_1k_output", 0.01),
                    )

                logger.info(f"Loaded capabilities for {len(self._capabilities_registry)} models")
            else:
                logger.warning(f"Capabilities file not found: {CAPABILITIES_DATA_PATH}")
                self._load_default_capabilities()
        except Exception as e:
            logger.error(f"Failed to load capabilities: {e}")
            self._load_default_capabilities()

    def _load_default_capabilities(self) -> None:
        """Load minimal default capabilities for common models."""
        defaults = {
            "openai/gpt-4o-mini": ModelCapabilities(
                model_id="openai/gpt-4o-mini",
                provider="openai",
                tools=True,
                json_mode=True,
                json_schema=True,
                vision=True,
                max_context=128000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.00015,
                cost_per_1k_output=0.0006,
            ),
            "openai/gpt-4o": ModelCapabilities(
                model_id="openai/gpt-4o",
                provider="openai",
                tools=True,
                json_mode=True,
                json_schema=True,
                vision=True,
                max_context=128000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.0025,
                cost_per_1k_output=0.01,
            ),
            "anthropic/claude-3-haiku": ModelCapabilities(
                model_id="anthropic/claude-3-haiku",
                provider="anthropic",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=200000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.00025,
                cost_per_1k_output=0.00125,
            ),
            "anthropic/claude-3.5-sonnet": ModelCapabilities(
                model_id="anthropic/claude-3.5-sonnet",
                provider="anthropic",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=200000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
            ),
            "google/gemini-flash-1.5": ModelCapabilities(
                model_id="google/gemini-flash-1.5",
                provider="google",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=1000000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.000075,
                cost_per_1k_output=0.0003,
            ),
            "deepseek/deepseek-chat": ModelCapabilities(
                model_id="deepseek/deepseek-chat",
                provider="deepseek",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=64000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.00014,
                cost_per_1k_output=0.00028,
            ),
            "mistral/mistral-small": ModelCapabilities(
                model_id="mistral/mistral-small",
                provider="mistral",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=32000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.0002,
                cost_per_1k_output=0.0006,
            ),
            "meta-llama/llama-3.1-8b-instant": ModelCapabilities(
                model_id="meta-llama/llama-3.1-8b-instant",
                provider="meta",
                tools=False,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=8192,
                tool_schema_adherence="low",
                cost_per_1k_input=0.0001,
                cost_per_1k_output=0.0001,
            ),
        }
        self._capabilities_registry = defaults
        logger.info(f"Loaded default capabilities for {len(defaults)} models")

    def route(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        user_preferences: UserRouterPreferences | None = None,
        conversation_id: str | None = None,
        tier: str = "small",
    ) -> RouterDecision:
        """
        Route a request to the optimal model.

        FAIL-OPEN: If anything takes too long or fails, returns default cheap model.

        Target total latency: < 2ms

        Args:
            messages: Conversation messages
            tools: Optional tools/functions
            response_format: Optional response format
            user_preferences: User routing preferences
            conversation_id: Optional conversation ID for sticky routing
            tier: Routing tier ("small", "medium", "large")

        Returns:
            RouterDecision with selected model and fallback chain
        """
        start = time.perf_counter()

        try:
            # 1. Extract required capabilities (< 0.1ms, no I/O)
            max_cost = user_preferences.max_cost_per_1k_tokens if user_preferences else None
            required = extract_capabilities(
                messages=messages,
                tools=tools,
                response_format=response_format,
                max_cost_per_1k=max_cost,
            )

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_capability_extraction")

            # 2. Get healthy models (single Redis GET, < 0.5ms)
            try:
                healthy = get_healthy_models_sync(tier)
            except Exception as e:
                logger.warning(f"Health snapshot read failed: {e}")
                # Use curated stable models instead of entire registry to avoid unhealthy models
                healthy = [m for m in STABLE_FALLBACK_MODELS if m in self._capabilities_registry]
                if not healthy:
                    # Last resort: use all models from registry
                    healthy = list(self._capabilities_registry.keys())

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_health_check")

            # 3. Filter by capabilities (< 0.2ms, no I/O)
            candidates = filter_by_capabilities(
                models=healthy,
                capabilities_registry=self._capabilities_registry,
                required=required,
            )

            if not candidates:
                logger.warning("No candidates after capability gating")
                return self._fail_open("no_capable_models", required=required)

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_capability_filter")

            # 4. Classify prompt (< 1ms, no I/O)
            classification = classify_prompt(messages)

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_classification")

            # 5. Select model (< 0.3ms, no I/O)
            optimization = RouterOptimization.BALANCED
            excluded = []
            preferred = []

            if user_preferences:
                optimization = user_preferences.default_optimization
                excluded = user_preferences.excluded_models
                preferred = user_preferences.preferred_models

            selected_model, reason = select_model(
                candidates=candidates,
                classification=classification,
                capabilities_registry=self._capabilities_registry,
                optimization=optimization,
                conversation_id=conversation_id,
                excluded_models=excluded,
                preferred_models=preferred,
            )

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_selection")

            # 6. Build fallback chain (< 0.2ms, no I/O)
            fallback_chain = build_fallback_chain(
                primary_model=selected_model,
                required_capabilities=required,
                healthy_candidates=candidates,
                capabilities_registry=self._capabilities_registry,
                tier=tier,
                excluded_models=excluded,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Get provider from capabilities
            selected_caps = self._capabilities_registry.get(selected_model)
            selected_provider = selected_caps.provider if selected_caps else None

            # Estimate cost
            estimated_cost = None
            if selected_caps:
                estimated_cost = selected_caps.cost_per_1k_input

            return RouterDecision(
                selected_model=selected_model,
                selected_provider=selected_provider,
                fallback_chain=fallback_chain,
                classification=classification,
                required_capabilities=required,
                estimated_cost_per_1k=estimated_cost,
                decision_time_ms=elapsed_ms,
                reason=reason,
            )

        except Exception as e:
            logger.exception(f"Router failed with exception: {e}")
            return self._fail_open(f"exception:{type(e).__name__}")

    def _check_timeout(self, start: float) -> bool:
        """Check if we've exceeded the timeout budget."""
        elapsed_ms = (time.perf_counter() - start) * 1000
        return elapsed_ms > ROUTER_TIMEOUT_MS

    def _fail_open(
        self,
        reason: str,
        required: RequiredCapabilities | None = None,
    ) -> RouterDecision:
        """
        Return safe default when router can't complete.
        This is the fail-open behavior.
        """
        logger.warning(f"Router failing open: {reason}")

        return RouterDecision(
            selected_model=DEFAULT_CHEAP_MODEL,
            selected_provider=DEFAULT_PROVIDER,
            fallback_chain=[],
            classification=None,
            required_capabilities=required,
            estimated_cost_per_1k=0.00015,  # gpt-4o-mini cost
            decision_time_ms=0,
            reason=f"fail_open:{reason}",
        )

    def get_capabilities(self, model_id: str) -> ModelCapabilities | None:
        """Get capabilities for a model."""
        return self._capabilities_registry.get(model_id)

    def list_capable_models(self, required: RequiredCapabilities) -> list[str]:
        """List all models that satisfy given requirements."""
        return filter_by_capabilities(
            models=list(self._capabilities_registry.keys()),
            capabilities_registry=self._capabilities_registry,
            required=required,
        )


# Global router instance
_router: PromptRouter | None = None


def get_router() -> PromptRouter:
    """Get global router instance."""
    global _router
    if _router is None:
        _router = PromptRouter()
    return _router


def route_request(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    user_preferences: UserRouterPreferences | None = None,
    conversation_id: str | None = None,
    tier: str = "small",
) -> RouterDecision:
    """Convenience function to route a request."""
    router = get_router()
    return router.route(
        messages=messages,
        tools=tools,
        response_format=response_format,
        user_preferences=user_preferences,
        conversation_id=conversation_id,
        tier=tier,
    )


def is_auto_route_request(model: str) -> bool:
    """Check if a model string indicates auto-routing.

    Uses 'router' prefix to avoid confusion with OpenRouter's 'openrouter/auto' model.
    """
    if not model:
        return False
    model_lower = model.lower()
    return model_lower.startswith("router")


def parse_auto_route_options(model: str) -> tuple[str, RouterOptimization]:
    """
    Parse auto-route model string into tier and optimization.

    Examples:
        "router" -> ("small", BALANCED)
        "router:small" -> ("small", BALANCED)
        "router:medium" -> ("medium", BALANCED)
        "router:price" -> ("small", PRICE)
        "router:quality" -> ("medium", QUALITY)
    """
    if not model or not model.lower().startswith("router"):
        return ("small", RouterOptimization.BALANCED)

    parts = model.lower().split(":")
    if len(parts) == 1:
        return ("small", RouterOptimization.BALANCED)

    modifier = parts[1]

    # Tier modifiers
    if modifier == "small":
        return ("small", RouterOptimization.BALANCED)
    elif modifier == "medium":
        return ("medium", RouterOptimization.BALANCED)
    elif modifier == "large":
        return ("large", RouterOptimization.BALANCED)

    # Optimization modifiers
    elif modifier == "price":
        return ("small", RouterOptimization.PRICE)
    elif modifier == "quality":
        return ("medium", RouterOptimization.QUALITY)
    elif modifier == "fast":
        return ("small", RouterOptimization.FAST)
    elif modifier == "balanced":
        return ("small", RouterOptimization.BALANCED)

    return ("small", RouterOptimization.BALANCED)
