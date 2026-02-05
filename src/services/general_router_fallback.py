"""
Fallback logic for general router.

Provides fallback model selection when NotDiamond is unavailable or fails.
"""

import logging

logger = logging.getLogger(__name__)

# Mode-specific fallback models
FALLBACK_MODELS = {
    "quality": "openai/gpt-4o",
    "cost": "openai/gpt-4o-mini",
    "latency": "groq/llama-3.3-70b-versatile",
    "balanced": "anthropic/claude-sonnet-4",
}

# System-wide default fallback
DEFAULT_FALLBACK = "anthropic/claude-sonnet-4"


def get_fallback_models() -> dict[str, str]:
    """
    Get fallback model configuration.

    Returns:
        Dict mapping mode names to fallback model IDs
    """
    return FALLBACK_MODELS.copy()


def get_fallback_model(
    mode: str,
    user_default: str | None = None,
) -> str:
    """
    Get fallback model when NotDiamond unavailable.

    Priority order:
    1. Mode-specific fallback
    2. User default model (if provided)
    3. System default fallback

    Args:
        mode: Routing mode (quality, cost, latency, balanced)
        user_default: Optional user's default model

    Returns:
        Gatewayz model ID to use as fallback
    """
    # Try mode-specific fallback
    if mode in FALLBACK_MODELS:
        model = FALLBACK_MODELS[mode]
        logger.info(f"Using mode-specific fallback for {mode}: {model}")
        return model

    # Try user default
    if user_default:
        logger.info(f"Using user default as fallback: {user_default}")
        return user_default

    # System default
    logger.info(f"Using system default as fallback: {DEFAULT_FALLBACK}")
    return DEFAULT_FALLBACK


def get_fallback_provider(model_id: str) -> str:
    """
    Extract provider from Gatewayz model ID.

    Args:
        model_id: Gatewayz model ID (e.g., "openai/gpt-4o")

    Returns:
        Provider identifier (e.g., "openai")
    """
    if "/" in model_id:
        return model_id.split("/")[0]
    return "openrouter"  # Default to OpenRouter as aggregator
