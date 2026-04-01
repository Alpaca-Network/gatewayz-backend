"""
Fallback logic for general router.

Provides fallback model selection when NotDiamond is unavailable or fails.
"""

import logging

logger = logging.getLogger(__name__)


def _get_default_fallback() -> str:
    from src.db.system_config import get_config

    return get_config("default_fallback_model", "anthropic/claude-sonnet-4")


def get_fallback_models() -> dict[str, str]:
    """
    Get fallback model configuration.

    Returns:
        Dict mapping mode names to fallback model IDs
    """
    from src.db.system_config import get_config

    return {
        "quality": get_config("general_router_fallback_quality", "openai/gpt-4o"),
        "cost": get_config("general_router_fallback_cost", "openai/gpt-4o-mini"),
        "latency": get_config("general_router_fallback_latency", "groq/llama-3.3-70b-versatile"),
        "balanced": get_config("general_router_fallback_balanced", "anthropic/claude-sonnet-4"),
    }


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
    fallback_models = get_fallback_models()
    if mode in fallback_models:
        model = fallback_models[mode]
        logger.info(f"Using mode-specific fallback for {mode}: {model}")
        return model

    # Try user default
    if user_default:
        logger.info(f"Using user default as fallback: {user_default}")
        return user_default

    # System default
    default_fallback = _get_default_fallback()
    logger.info(f"Using system default as fallback: {default_fallback}")
    return default_fallback


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
