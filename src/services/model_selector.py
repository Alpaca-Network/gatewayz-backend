"""
Model Selector for Prompt Router.

Implements stable, deterministic model selection with:
- Hash-based sticky routing per conversation
- Hysteresis to prevent model switching on small score differences
- Scoring based on quality priors + realtime metrics

Target latency: < 0.3ms
"""

import hashlib
import logging

from src.schemas.router import (
    ClassificationResult,
    ModelCapabilities,
    RouterOptimization,
)

logger = logging.getLogger(__name__)

# Score difference threshold for hysteresis
# Don't switch models unless score difference is > this value
HYSTERESIS_THRESHOLD = 5.0

# Default cheap model (fail-open fallback)
DEFAULT_CHEAP_MODEL = "openai/gpt-4o-mini"

# Realtime metrics penalty/bonus multipliers
RETRY_RATE_PENALTY_MULTIPLIER = 20  # Penalty per 1.0 retry rate
FORMAT_FAILURE_PENALTY_MULTIPLIER = 30  # Penalty per 1.0 format failure rate
SUCCESS_RATE_BONUS_MULTIPLIER = 20  # Bonus per 0.1 above baseline
SUCCESS_RATE_BASELINE = 0.9  # Expected baseline success rate

# Quality priors from benchmarks (treated as priors, not truth)
# Values are 0-100, will be blended with realtime metrics
QUALITY_PRIORS: dict[str, dict[str, float]] = {
    # OpenAI models
    "openai/gpt-4o": {
        "simple_qa": 92,
        "complex_reasoning": 95,
        "code_generation": 94,
        "code_review": 93,
        "creative_writing": 90,
        "summarization": 91,
        "translation": 88,
        "math_calculation": 93,
        "data_analysis": 90,
        "conversation": 90,
        "tool_use": 95,
        "unknown": 88,
    },
    "openai/gpt-4o-mini": {
        "simple_qa": 85,
        "complex_reasoning": 78,
        "code_generation": 82,
        "code_review": 80,
        "creative_writing": 84,
        "summarization": 86,
        "translation": 82,
        "math_calculation": 80,
        "data_analysis": 78,
        "conversation": 85,
        "tool_use": 88,
        "unknown": 80,
    },
    # Anthropic models
    "anthropic/claude-3.5-sonnet": {
        "simple_qa": 93,
        "complex_reasoning": 94,
        "code_generation": 96,
        "code_review": 95,
        "creative_writing": 92,
        "summarization": 93,
        "translation": 85,
        "math_calculation": 88,
        "data_analysis": 90,
        "conversation": 92,
        "tool_use": 93,
        "unknown": 90,
    },
    "anthropic/claude-3-haiku": {
        "simple_qa": 82,
        "complex_reasoning": 72,
        "code_generation": 78,
        "code_review": 75,
        "creative_writing": 80,
        "summarization": 84,
        "translation": 78,
        "math_calculation": 72,
        "data_analysis": 70,
        "conversation": 82,
        "tool_use": 80,
        "unknown": 75,
    },
    "anthropic/claude-3-sonnet": {
        "simple_qa": 88,
        "complex_reasoning": 85,
        "code_generation": 88,
        "code_review": 86,
        "creative_writing": 87,
        "summarization": 89,
        "translation": 82,
        "math_calculation": 82,
        "data_analysis": 84,
        "conversation": 88,
        "tool_use": 88,
        "unknown": 84,
    },
    # Google models
    "google/gemini-flash-1.5": {
        "simple_qa": 80,
        "complex_reasoning": 75,
        "code_generation": 78,
        "code_review": 75,
        "creative_writing": 78,
        "summarization": 82,
        "translation": 80,
        "math_calculation": 78,
        "data_analysis": 76,
        "conversation": 80,
        "tool_use": 75,
        "unknown": 76,
    },
    "google/gemini-pro-1.5": {
        "simple_qa": 88,
        "complex_reasoning": 86,
        "code_generation": 85,
        "code_review": 84,
        "creative_writing": 85,
        "summarization": 88,
        "translation": 85,
        "math_calculation": 85,
        "data_analysis": 86,
        "conversation": 87,
        "tool_use": 82,
        "unknown": 84,
    },
    # DeepSeek models
    "deepseek/deepseek-chat": {
        "simple_qa": 78,
        "complex_reasoning": 82,
        "code_generation": 90,
        "code_review": 88,
        "creative_writing": 72,
        "summarization": 78,
        "translation": 75,
        "math_calculation": 85,
        "data_analysis": 82,
        "conversation": 76,
        "tool_use": 80,
        "unknown": 78,
    },
    "deepseek/deepseek-coder": {
        "simple_qa": 70,
        "complex_reasoning": 75,
        "code_generation": 94,
        "code_review": 92,
        "creative_writing": 60,
        "summarization": 70,
        "translation": 65,
        "math_calculation": 80,
        "data_analysis": 78,
        "conversation": 68,
        "tool_use": 82,
        "unknown": 72,
    },
    # Meta Llama models
    "meta-llama/llama-3.1-8b-instant": {
        "simple_qa": 75,
        "complex_reasoning": 68,
        "code_generation": 72,
        "code_review": 70,
        "creative_writing": 74,
        "summarization": 76,
        "translation": 72,
        "math_calculation": 68,
        "data_analysis": 66,
        "conversation": 76,
        "tool_use": 65,
        "unknown": 70,
    },
    "meta-llama/llama-3.1-70b": {
        "simple_qa": 85,
        "complex_reasoning": 82,
        "code_generation": 84,
        "code_review": 82,
        "creative_writing": 83,
        "summarization": 85,
        "translation": 80,
        "math_calculation": 80,
        "data_analysis": 82,
        "conversation": 84,
        "tool_use": 78,
        "unknown": 80,
    },
    "meta-llama/llama-3.1-405b": {
        "simple_qa": 90,
        "complex_reasoning": 88,
        "code_generation": 88,
        "code_review": 86,
        "creative_writing": 87,
        "summarization": 89,
        "translation": 85,
        "math_calculation": 86,
        "data_analysis": 87,
        "conversation": 88,
        "tool_use": 82,
        "unknown": 85,
    },
    # Mistral models
    "mistral/mistral-small": {
        "simple_qa": 78,
        "complex_reasoning": 72,
        "code_generation": 75,
        "code_review": 73,
        "creative_writing": 76,
        "summarization": 80,
        "translation": 82,
        "math_calculation": 72,
        "data_analysis": 70,
        "conversation": 78,
        "tool_use": 72,
        "unknown": 74,
    },
    "mistral/mistral-large": {
        "simple_qa": 86,
        "complex_reasoning": 84,
        "code_generation": 85,
        "code_review": 83,
        "creative_writing": 84,
        "summarization": 87,
        "translation": 88,
        "math_calculation": 82,
        "data_analysis": 84,
        "conversation": 86,
        "tool_use": 84,
        "unknown": 83,
    },
    # Cohere
    "cohere/command-r-plus": {
        "simple_qa": 84,
        "complex_reasoning": 80,
        "code_generation": 78,
        "code_review": 76,
        "creative_writing": 82,
        "summarization": 86,
        "translation": 80,
        "math_calculation": 75,
        "data_analysis": 80,
        "conversation": 84,
        "tool_use": 82,
        "unknown": 78,
    },
}

# Realtime metrics cache (updated by background task)
# Maps model_id -> {"retry_rate": float, "format_failure_rate": float, "success_rate": float}
_realtime_metrics_cache: dict[str, dict[str, float]] = {}


def select_model(
    candidates: list[str],
    classification: ClassificationResult,
    capabilities_registry: dict[str, ModelCapabilities],
    optimization: RouterOptimization = RouterOptimization.BALANCED,
    conversation_id: str | None = None,
    excluded_models: list[str] | None = None,
    preferred_models: list[str] | None = None,
) -> tuple[str, str]:
    """
    Select the best model from candidates.

    Implements:
    - Scoring based on quality + cost
    - Stable selection via conversation hash
    - Hysteresis to prevent unnecessary switching

    Target latency: < 0.3ms

    Args:
        candidates: List of healthy, capable model IDs
        classification: Prompt classification result
        capabilities_registry: Model capabilities lookup
        optimization: Price/quality/balanced target
        conversation_id: Optional ID for sticky routing
        excluded_models: Models to exclude
        preferred_models: Models to prefer when scores are similar

    Returns:
        Tuple of (selected_model_id, selection_reason)
    """
    if not candidates:
        return DEFAULT_CHEAP_MODEL, "no_candidates"

    # Filter excluded models
    if excluded_models:
        candidates = [m for m in candidates if m not in excluded_models]
        if not candidates:
            return DEFAULT_CHEAP_MODEL, "all_candidates_excluded"

    # Score all candidates
    scored = []
    category = classification.category.value

    for model_id in candidates:
        score = _compute_model_score(
            model_id,
            category,
            capabilities_registry.get(model_id),
            optimization,
        )

        # Boost preferred models slightly
        if preferred_models and model_id in preferred_models:
            score += 3.0

        scored.append((model_id, score))

    # Sort by score descending
    scored.sort(key=lambda x: -x[1])

    if not scored:
        return DEFAULT_CHEAP_MODEL, "no_scored_candidates"

    top_model, top_score = scored[0]

    # Stable selection using conversation hash (prevents roulette)
    if conversation_id and len(scored) > 1:
        # Find candidates within hysteresis threshold of top score
        similar_candidates = [(m, s) for m, s in scored if top_score - s < HYSTERESIS_THRESHOLD]

        if len(similar_candidates) > 1:
            # Deterministic selection based on conversation ID
            hash_val = _hash_for_selection(conversation_id, category)
            idx = hash_val % len(similar_candidates)
            selected, _ = similar_candidates[idx]
            return selected, "stable_selection"

    return top_model, "top_scorer"


def _compute_model_score(
    model_id: str,
    category: str,
    capabilities: ModelCapabilities | None,
    optimization: RouterOptimization,
) -> float:
    """
    Compute score for a model given category and optimization target.

    Score components:
    - Quality prior (from benchmarks)
    - Cost score (inverted - lower cost = higher score)
    - Realtime adjustments (retry rate, format failures)

    Returns score 0-100.
    """
    # Get quality prior
    model_priors = QUALITY_PRIORS.get(model_id, {})
    quality = model_priors.get(category, 50.0)  # Default to 50 if unknown

    # Get cost score (higher = cheaper)
    cost_score = 50.0  # Default
    if capabilities:
        # Normalize cost: $0.001/1K = 90, $0.01/1K = 50, $0.1/1K = 10
        cost = capabilities.cost_per_1k_input
        if cost <= 0.0001:
            cost_score = 95.0
        elif cost <= 0.0005:
            cost_score = 85.0
        elif cost <= 0.001:
            cost_score = 75.0
        elif cost <= 0.005:
            cost_score = 60.0
        elif cost <= 0.01:
            cost_score = 45.0
        elif cost <= 0.05:
            cost_score = 30.0
        else:
            cost_score = 15.0

    # Apply realtime adjustments
    realtime = _realtime_metrics_cache.get(model_id)
    if realtime:
        # Penalize high retry rates (users retrying = bad experience)
        retry_penalty = realtime.get("retry_rate", 0) * RETRY_RATE_PENALTY_MULTIPLIER
        quality -= retry_penalty

        # Penalize format failures (JSON invalid, etc.)
        format_penalty = realtime.get("format_failure_rate", 0) * FORMAT_FAILURE_PENALTY_MULTIPLIER
        quality -= format_penalty

        # Boost high success rates
        success_bonus = (
            realtime.get("success_rate", SUCCESS_RATE_BASELINE) - SUCCESS_RATE_BASELINE
        ) * SUCCESS_RATE_BONUS_MULTIPLIER
        quality += success_bonus

    # Clamp quality to valid range
    quality = max(0, min(100, quality))

    # Weight by optimization preference
    if optimization == RouterOptimization.PRICE:
        return cost_score * 0.7 + quality * 0.3
    elif optimization == RouterOptimization.QUALITY:
        return quality * 0.8 + cost_score * 0.2
    elif optimization == RouterOptimization.FAST:
        # For fast, prefer smaller/cheaper models (usually faster)
        return cost_score * 0.6 + quality * 0.4
    else:  # BALANCED
        return quality * 0.5 + cost_score * 0.5


def _hash_for_selection(conversation_id: str, category: str) -> int:
    """
    Generate deterministic hash for stable selection.
    Same conversation + category = same hash = same model choice.
    """
    data = f"{conversation_id}:{category}"
    hash_bytes = hashlib.md5(data.encode()).digest()
    return int.from_bytes(hash_bytes[:4], byteorder="big")


def update_realtime_metrics(model_id: str, metrics: dict[str, float]) -> None:
    """
    Update realtime metrics for a model.
    Called by background task or after request completion.
    """
    _realtime_metrics_cache[model_id] = metrics


def get_realtime_metrics(model_id: str) -> dict[str, float] | None:
    """Get current realtime metrics for a model."""
    return _realtime_metrics_cache.get(model_id)


def update_realtime_metrics_batch(metrics: dict[str, dict[str, float]]) -> None:
    """Update realtime metrics for multiple models."""
    _realtime_metrics_cache.update(metrics)


def get_quality_prior(model_id: str, category: str) -> float:
    """Get quality prior for a model and category."""
    model_priors = QUALITY_PRIORS.get(model_id, {})
    return model_priors.get(category, 50.0)


def list_models_by_category_score(
    category: str,
    candidates: list[str] | None = None,
) -> list[tuple[str, float]]:
    """
    Get models sorted by quality score for a category.
    Useful for debugging and analytics.
    """
    if candidates is None:
        candidates = list(QUALITY_PRIORS.keys())

    scored = []
    for model_id in candidates:
        score = get_quality_prior(model_id, category)
        scored.append((model_id, score))

    scored.sort(key=lambda x: -x[1])
    return scored
