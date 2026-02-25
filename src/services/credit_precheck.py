"""
Credit Pre-flight Check Service

This module provides credit sufficiency checking BEFORE making provider requests.
Following OpenAI's model: use max_tokens to calculate maximum possible cost,
then verify user has sufficient credits to cover that maximum.

This prevents:
- Users starting expensive requests they can't afford
- Revenue loss from uncollectable charges
- Poor UX from post-generation billing failures
"""

import logging

from src.services.pricing import calculate_cost
from src.utils.token_estimator import estimate_message_tokens

logger = logging.getLogger(__name__)

# Default max_tokens values per model type
DEFAULT_MAX_TOKENS = 4096
MODEL_MAX_TOKENS = {
    # GPT models
    "gpt-4": 8192,
    "gpt-4-turbo": 4096,
    "gpt-4o": 4096,
    "gpt-4o-mini": 16384,
    "gpt-3.5-turbo": 4096,
    # Claude models
    "claude-3-opus": 4096,
    "claude-3-sonnet": 4096,
    "claude-3-haiku": 4096,
    "claude-3-5-sonnet": 8192,
    "claude-sonnet-4": 8192,
    # Other models
    "llama-3": 8192,
    "llama-3.1": 128000,
    "llama-3.2": 128000,
    "mistral": 8192,
    "mixtral": 32768,
}


def get_model_max_tokens(model_id: str) -> int:
    """
    Get the maximum output tokens for a model.

    Args:
        model_id: Model identifier (e.g., "gpt-4o", "claude-3-opus")

    Returns:
        Maximum output tokens for the model
    """
    # Check exact match first
    if model_id in MODEL_MAX_TOKENS:
        return MODEL_MAX_TOKENS[model_id]

    # Check partial matches (e.g., "gpt-4o-2024-05-13" matches "gpt-4o")
    model_lower = model_id.lower()
    for key, max_tokens in MODEL_MAX_TOKENS.items():
        if key.lower() in model_lower:
            return max_tokens

    # Default fallback
    return DEFAULT_MAX_TOKENS


def calculate_maximum_cost(
    model_id: str,
    messages: list[dict],
    max_tokens: int | None = None,
) -> tuple[float, int, int]:
    """
    Calculate the MAXIMUM possible cost for a request.

    This uses:
    - Actual input tokens (estimated from messages)
    - Maximum output tokens (from request or model default)

    Args:
        model_id: Model to use
        messages: Chat messages
        max_tokens: Maximum output tokens (from request parameter)

    Returns:
        Tuple of (max_cost, input_tokens, max_output_tokens)
    """
    # Estimate input tokens from messages
    input_tokens = estimate_message_tokens(messages, None)

    # Get effective max output tokens
    if max_tokens is not None and max_tokens > 0:
        max_output_tokens = max_tokens
    else:
        # Use model's default max
        max_output_tokens = get_model_max_tokens(model_id)

    # Calculate maximum possible cost
    max_cost = calculate_cost(model_id, input_tokens, max_output_tokens)

    logger.debug(
        f"Maximum cost calculation for {model_id}: "
        f"input_tokens={input_tokens}, max_output_tokens={max_output_tokens}, "
        f"max_cost=${max_cost:.6f}"
    )

    return max_cost, input_tokens, max_output_tokens


def check_credit_sufficiency(
    user_credits: float,
    max_cost: float,
    model_id: str,
    max_tokens: int,
    is_trial: bool = False,
) -> dict[str, any]:
    """
    Check if user has sufficient credits for the maximum possible cost.

    Args:
        user_credits: User's current credit balance
        max_cost: Maximum possible cost for the request
        model_id: Model being used
        max_tokens: Maximum output tokens
        is_trial: Whether user is on trial (trial users skip credit checks)

    Returns:
        Dictionary with:
        - allowed (bool): Whether request should be allowed
        - reason (str): Reason if not allowed
        - max_cost (float): Maximum cost
        - available_credits (float): User's credits
        - shortfall (float): Amount short (if insufficient)
    """
    # Trial users don't consume credits
    if is_trial:
        return {
            "allowed": True,
            "reason": "Trial user - no credit check",
            "max_cost": 0.0,
            "available_credits": user_credits,
        }

    # Check sufficiency
    if user_credits >= max_cost:
        return {
            "allowed": True,
            "reason": "Sufficient credits",
            "max_cost": max_cost,
            "available_credits": user_credits,
            "remaining_after_max": user_credits - max_cost,
        }
    else:
        shortfall = max_cost - user_credits
        return {
            "allowed": False,
            "reason": "Insufficient credits",
            "max_cost": max_cost,
            "available_credits": user_credits,
            "shortfall": shortfall,
            "suggestion": (
                f"Reduce max_tokens from {max_tokens} to lower the maximum cost, "
                f"or add ${shortfall:.4f} in credits."
            ),
        }


def estimate_and_check_credits(
    model_id: str,
    messages: list[dict],
    user_credits: float,
    max_tokens: int | None = None,
    is_trial: bool = False,
) -> dict[str, any]:
    """
    Complete pre-flight check: estimate maximum cost and verify sufficiency.

    This is the main entry point for credit pre-checks.

    Args:
        model_id: Model to use
        messages: Chat messages
        user_credits: User's current credit balance
        max_tokens: Maximum output tokens (from request)
        is_trial: Whether user is on trial

    Returns:
        Dictionary with check results (see check_credit_sufficiency)

    Example:
        >>> result = estimate_and_check_credits(
        ...     "gpt-4o",
        ...     [{"role": "user", "content": "Hello"}],
        ...     user_credits=5.0,
        ...     max_tokens=1000
        ... )
        >>> if not result["allowed"]:
        ...     raise HTTPException(402, detail="Insufficient credits. Please add credits to continue.")
    """
    # Calculate maximum cost
    max_cost, input_tokens, max_output_tokens = calculate_maximum_cost(
        model_id, messages, max_tokens
    )

    # Check sufficiency
    check_result = check_credit_sufficiency(
        user_credits, max_cost, model_id, max_output_tokens, is_trial
    )

    # Add token info to result
    check_result["input_tokens"] = input_tokens
    check_result["max_output_tokens"] = max_output_tokens

    return check_result
