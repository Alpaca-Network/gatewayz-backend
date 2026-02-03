"""
Code-Optimized Prompt Router

Routes code-related prompts to the most cost-effective model based on
task complexity and quality requirements.

Supports multiple routing modes:
- router:code         - Auto-select best price/performance
- router:code:price   - Optimize for lowest cost
- router:code:quality - Optimize for highest quality
- router:code:agentic - Force premium tier for agentic tasks
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from src.services.code_classifier import get_classifier

logger = logging.getLogger(__name__)

# Router mode types
RouterMode = Literal["auto", "price", "quality", "agentic"]

# Load quality priors
_QUALITY_PRIORS_PATH = Path(__file__).parent / "code_quality_priors.json"
_quality_priors: dict[str, Any] | None = None


def _load_quality_priors() -> dict[str, Any]:
    """Load quality priors from JSON file with caching."""
    global _quality_priors
    if _quality_priors is None:
        try:
            with open(_QUALITY_PRIORS_PATH) as f:
                _quality_priors = json.load(f)
            logger.info(f"Code router loaded quality priors v{_quality_priors.get('version', 'unknown')}")
        except Exception as e:
            logger.error(f"Failed to load code quality priors: {e}")
            logger.warning("Using minimal fallback configuration - routing may use fallback model only")
            # Capture to Sentry for monitoring (non-critical but worth tracking)
            try:
                from src.utils.sentry_context import capture_error
                capture_error(
                    e,
                    context={"file": str(_QUALITY_PRIORS_PATH)},
                    level="warning",
                )
            except ImportError:
                pass  # Sentry not available
            _quality_priors = {
                "model_tiers": {},
                "fallback_model": {"id": "zai/glm-4.7", "provider": "zai"},
                "baselines": {},
            }
    return _quality_priors


def get_model_tiers() -> dict[str, Any]:
    """Get model tiers configuration."""
    return _load_quality_priors().get("model_tiers", {})


def get_fallback_model() -> dict[str, Any]:
    """Get fallback model configuration."""
    return _load_quality_priors().get("fallback_model", {"id": "zai/glm-4.7", "provider": "zai"})


def get_baselines() -> dict[str, Any]:
    """Get baseline models for savings calculation."""
    return _load_quality_priors().get("baselines", {})


class CodeRouter:
    """
    Routes code-related prompts to optimal models based on task classification.

    Features:
    - Task-based model selection
    - Multiple routing modes (auto, price, quality, agentic)
    - Quality gates to prevent inappropriate downgrades
    - Cost savings tracking against baselines
    - Prometheus metrics integration
    """

    def __init__(self):
        self.classifier = get_classifier()
        self.model_tiers = get_model_tiers()
        self.fallback_model = get_fallback_model()
        self.baselines = get_baselines()

        # Build model lookup for fast access
        self._build_model_lookup()

    def _build_model_lookup(self) -> None:
        """Build a flat lookup of all models by ID."""
        self._model_lookup: dict[str, dict[str, Any]] = {}
        self._tier_models: dict[int, list[dict[str, Any]]] = {}

        for tier_str, tier_config in self.model_tiers.items():
            tier = int(tier_str)
            self._tier_models[tier] = tier_config.get("models", [])
            for model in tier_config.get("models", []):
                self._model_lookup[model["id"]] = {**model, "tier": tier}

    def route(
        self,
        prompt: str,
        mode: RouterMode = "auto",
        context: dict[str, Any] | None = None,
        user_default_model: str | None = None,
    ) -> dict[str, Any]:
        """
        Route a code-related prompt to the optimal model.

        Args:
            prompt: The user's prompt
            mode: Routing mode (auto, price, quality, agentic)
            context: Optional context (conversation history, etc.)
            user_default_model: User's default model (for savings calculation)

        Returns:
            Routing result with:
            - model_id: Selected model ID
            - provider: Provider slug
            - tier: Selected tier number
            - task_category: Classified task category
            - complexity: Classified complexity
            - mode: Routing mode used
            - routing_latency_ms: Time taken for routing
            - savings_estimate: Estimated savings vs baselines
        """
        start_time = time.perf_counter()

        # Classify the task
        classification = self.classifier.classify(prompt, context)
        task_category = classification["category"]
        complexity = classification["complexity"]
        default_tier = classification["default_tier"]
        min_tier = classification["min_tier"]

        # Determine target tier based on mode
        target_tier = self._calculate_target_tier(
            default_tier=default_tier,
            min_tier=min_tier,
            mode=mode,
            complexity=complexity,
        )

        # Select best available model from tier
        selected_model = self._select_model_from_tier(target_tier, task_category, mode)

        # Calculate routing latency
        routing_latency_ms = (time.perf_counter() - start_time) * 1000

        # Calculate savings estimate
        savings_estimate = self._calculate_savings_estimate(
            selected_model=selected_model,
            user_default_model=user_default_model,
        )

        result = {
            "model_id": selected_model["id"],
            "provider": selected_model.get("provider", "openrouter"),
            "tier": target_tier,
            "task_category": task_category,
            "complexity": complexity,
            "confidence": classification["confidence"],
            "mode": mode,
            "routing_latency_ms": round(routing_latency_ms, 3),
            "savings_estimate": savings_estimate,
            "selected_model_info": {
                "name": selected_model.get("name"),
                "swe_bench": selected_model.get("swe_bench"),
                "human_eval": selected_model.get("human_eval"),
                "price_input": selected_model.get("price_input"),
                "price_output": selected_model.get("price_output"),
            },
        }

        # Track metrics
        self._track_routing_metrics(result, classification)

        logger.info(
            f"Code router selected {selected_model['id']} (tier {target_tier}) "
            f"for {task_category} task in {routing_latency_ms:.2f}ms"
        )

        return result

    def _calculate_target_tier(
        self,
        default_tier: int,
        min_tier: int,
        mode: RouterMode,
        complexity: str,
    ) -> int:
        """
        Calculate target tier based on classification and mode.

        Args:
            default_tier: Default tier from classification
            min_tier: Minimum required tier (quality gate)
            mode: Routing mode
            complexity: Task complexity

        Returns:
            Target tier number (1-4)
        """
        if mode == "agentic":
            # Always use premium tier for agentic mode
            return 1

        if mode == "quality":
            # Bump up one tier (but respect tier 1 limit)
            target = max(1, default_tier - 1)
        elif mode == "price":
            # Use default tier (but respect minimum)
            target = default_tier
        else:  # auto
            # Use default tier
            target = default_tier

        # Apply quality gate (ensure we don't go below minimum)
        target = min(target, min_tier)

        # Clamp to valid range
        return max(1, min(4, target))

    def _select_model_from_tier(
        self,
        tier: int,
        task_category: str,
        mode: RouterMode,
    ) -> dict[str, Any]:
        """
        Select the best available model from a tier.

        Args:
            tier: Target tier number
            task_category: Task category for strength matching
            mode: Routing mode

        Returns:
            Selected model configuration
        """
        tier_models = self._tier_models.get(tier, [])

        if not tier_models:
            # Fallback if tier has no models
            logger.warning(f"No models in tier {tier}, using fallback")
            return self.fallback_model

        # Score models based on task strengths
        scored_models = []
        for model in tier_models:
            score = 0.0
            strengths = model.get("strengths", [])

            # Boost score if model has matching strengths
            if task_category in strengths:
                score += 2.0
            if any(s in strengths for s in ["code_generation", "debugging", "refactoring"]):
                score += 0.5

            # For price mode, prefer cheaper models (consider both input and output prices)
            if mode == "price":
                price_input = model.get("price_input", 1.0)
                price_output = model.get("price_output", 1.0)
                # Weight combined price: assume typical 2:1 input:output token ratio
                combined_price = (price_input * 2 + price_output) / 3
                score -= combined_price * 0.1  # Penalize expensive models

            # For quality mode, prefer higher benchmark scores
            if mode == "quality":
                swe_bench = model.get("swe_bench", 0)
                score += swe_bench * 0.05

            scored_models.append((score, model))

        # Sort by score (descending) and return best
        scored_models.sort(key=lambda x: x[0], reverse=True)
        return scored_models[0][1]

    def _calculate_savings_estimate(
        self,
        selected_model: dict[str, Any],
        user_default_model: str | None = None,
    ) -> dict[str, Any]:
        """
        Calculate estimated savings vs baselines.

        Args:
            selected_model: Selected model configuration
            user_default_model: User's default model ID

        Returns:
            Savings estimate dict with per-baseline savings
        """
        selected_input = selected_model.get("price_input", 0)
        selected_output = selected_model.get("price_output", 0)

        # Assume average request: 1000 input tokens, 500 output tokens
        avg_input_tokens = 1000
        avg_output_tokens = 500

        selected_cost = (
            (selected_input * avg_input_tokens / 1_000_000)
            + (selected_output * avg_output_tokens / 1_000_000)
        )

        savings: dict[str, Any] = {}

        for baseline_key, baseline in self.baselines.items():
            baseline_input = baseline.get("price_input", 0)
            baseline_output = baseline.get("price_output", 0)
            baseline_cost = (
                (baseline_input * avg_input_tokens / 1_000_000)
                + (baseline_output * avg_output_tokens / 1_000_000)
            )
            savings[baseline_key] = {
                "baseline_cost_usd": round(baseline_cost, 6),
                "selected_cost_usd": round(selected_cost, 6),
                "savings_usd": round(max(0, baseline_cost - selected_cost), 6),
                "savings_percent": round(
                    max(0, (baseline_cost - selected_cost) / baseline_cost * 100)
                    if baseline_cost > 0
                    else 0,
                    1,
                ),
            }

        # Calculate savings vs user default if provided
        if user_default_model and user_default_model in self._model_lookup:
            user_model = self._model_lookup[user_default_model]
            user_input = user_model.get("price_input", 0)
            user_output = user_model.get("price_output", 0)
            user_cost = (
                (user_input * avg_input_tokens / 1_000_000)
                + (user_output * avg_output_tokens / 1_000_000)
            )
            savings["user_default"] = {
                "baseline_cost_usd": round(user_cost, 6),
                "selected_cost_usd": round(selected_cost, 6),
                "savings_usd": round(max(0, user_cost - selected_cost), 6),
                "savings_percent": round(
                    max(0, (user_cost - selected_cost) / user_cost * 100)
                    if user_cost > 0
                    else 0,
                    1,
                ),
            }

        return savings

    def _track_routing_metrics(
        self,
        result: dict[str, Any],
        classification: dict[str, Any],
    ) -> None:
        """Track routing metrics via Prometheus."""
        try:
            from src.services.prometheus_metrics import (
                code_router_latency_seconds,
                code_router_requests_total,
                code_router_savings_dollars,
            )

            # Track routing request
            code_router_requests_total.labels(
                task_category=result["task_category"],
                complexity=result["complexity"],
                mode=result["mode"],
                selected_model=result["model_id"],
                selected_tier=str(result["tier"]),
            ).inc()

            # Track latency
            code_router_latency_seconds.observe(result["routing_latency_ms"] / 1000)

            # Track savings
            for baseline, savings_data in result.get("savings_estimate", {}).items():
                savings_usd = savings_data.get("savings_usd", 0)
                if savings_usd > 0:
                    code_router_savings_dollars.labels(
                        baseline=baseline,
                        task_category=result["task_category"],
                    ).inc(savings_usd)

        except ImportError:
            # Metrics not available
            pass
        except Exception as e:
            logger.debug(f"Failed to track routing metrics: {e}")


# Module-level router instance (lazy initialization)
_router: CodeRouter | None = None


def get_router() -> CodeRouter:
    """Get the singleton router instance."""
    global _router
    if _router is None:
        _router = CodeRouter()
    return _router


def route_code_prompt(
    prompt: str,
    mode: RouterMode = "auto",
    context: dict[str, Any] | None = None,
    user_default_model: str | None = None,
) -> dict[str, Any]:
    """
    Convenience function to route a code prompt.

    Args:
        prompt: The user's prompt
        mode: Routing mode
        context: Optional context
        user_default_model: User's default model

    Returns:
        Routing result
    """
    return get_router().route(prompt, mode, context, user_default_model)


def parse_router_model_string(model_string: str) -> tuple[bool, RouterMode]:
    """
    Parse a router model string to determine if it's a code router request.

    Args:
        model_string: Model string (e.g., "router:code:price")

    Returns:
        Tuple of (is_code_router, mode)

    Examples:
        "router:code" -> (True, "auto")
        "router:code:price" -> (True, "price")
        "router:code:quality" -> (True, "quality")
        "router:code:agentic" -> (True, "agentic")
        "gpt-4" -> (False, "auto")
    """
    model_string_lower = model_string.lower()
    if not model_string_lower.startswith("router:code"):
        return (False, "auto")

    parts = model_string_lower.split(":")
    if len(parts) == 2:
        # "router:code" -> auto mode
        return (True, "auto")
    elif len(parts) == 3:
        # "router:code:<mode>"
        mode = parts[2].lower()
        if mode in ("price", "quality", "agentic"):
            return (True, mode)  # type: ignore
        else:
            logger.warning(f"Unknown router mode '{mode}', using auto")
            return (True, "auto")

    return (False, "auto")


def get_routing_metadata(
    routing_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Format routing result as metadata for API response.

    Args:
        routing_result: Result from route_code_prompt()

    Returns:
        Formatted metadata dict for inclusion in API response
    """
    return {
        "router_mode": f"code:{routing_result['mode']}",
        "task_category": routing_result["task_category"],
        "complexity": routing_result["complexity"],
        "confidence": routing_result["confidence"],
        "selected_model": routing_result["model_id"],
        "selected_tier": routing_result["tier"],
        "routing_latency_ms": routing_result["routing_latency_ms"],
        "savings": {
            baseline: f"${data['savings_usd']:.4f}"
            for baseline, data in routing_result.get("savings_estimate", {}).items()
        },
        "model_info": routing_result.get("selected_model_info", {}),
    }
