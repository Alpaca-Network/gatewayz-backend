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

# Default max_tokens fallback when no DB or pattern match is found
DEFAULT_MAX_TOKENS = 4096


def get_model_max_tokens(model_id: str) -> int:
    """
    Get the maximum output tokens for a model.

    Queries the in-memory model capabilities cache (DB-backed).
    Falls back to pattern matching against a hardcoded dict when the cache
    is unavailable or the model is not in the DB.

    Args:
        model_id: Model identifier (e.g., "gpt-4o", "claude-3-opus")

    Returns:
        Maximum output tokens for the model
    """
    try:
        from src.services.model_capabilities_cache import get_max_output_tokens

        return get_max_output_tokens(model_id, default=DEFAULT_MAX_TOKENS)
    except Exception:
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


def calculate_affordable_max_tokens(
    model_id: str,
    input_tokens: int,
    user_credits: float,
) -> int | None:
    """Calculate the maximum output tokens a user can afford.

    Subtracts the input cost from the user's balance, then divides the
    remainder by the per-token completion rate (with markup) to get the
    maximum affordable output tokens.

    Returns:
        Maximum affordable output tokens, or *None* if the user cannot
        afford any output at all.
    """
    from src.config.config import Config
    from src.services.pricing import get_model_pricing

    try:
        # Input cost includes markup via calculate_cost()
        input_cost = calculate_cost(model_id, input_tokens, 0)
    except ValueError:
        # Pricing anomaly for input — cannot estimate
        return None

    remaining = user_credits - input_cost
    if remaining <= 0:
        logger.debug(
            "User cannot afford output for %s: credits=%.6f, input_cost=%.6f",
            model_id,
            user_credits,
            input_cost,
        )
        return None

    pricing = get_model_pricing(model_id)
    completion_rate = pricing.get("completion", 0.0)
    markup = Config.PRICING_MARKUP

    effective_rate = completion_rate * markup
    if effective_rate <= 0:
        # Free output model — no cap needed
        return 2**31 - 1

    affordable = int(remaining / effective_rate)
    if affordable <= 0:
        return None

    logger.debug(
        "Affordable max_tokens for %s: %d (remaining=%.6f, rate=%.10f)",
        model_id,
        affordable,
        remaining,
        effective_rate,
    )
    return affordable


def check_credit_sufficiency(
    user_credits: float,
    max_cost: float,
    model_id: str,
    max_tokens: int,
    is_trial: bool = False,
    input_tokens: int = 0,
) -> dict[str, any]:
    """
    Check if user has sufficient credits for the maximum possible cost.

    When the user cannot afford the full ``max_tokens`` but CAN afford some
    output, returns ``allowed=True`` with a ``capped_max_tokens`` value that
    the caller should use instead.

    Args:
        user_credits: User's current credit balance
        max_cost: Maximum possible cost for the request
        model_id: Model being used
        max_tokens: Maximum output tokens
        is_trial: Whether user is on trial (trial users skip credit checks)
        input_tokens: Estimated input token count (for affordability calc)

    Returns:
        Dictionary with allowed, reason, max_cost, available_credits, and
        optionally capped_max_tokens / original_max_tokens.
    """
    # Check sufficiency — can afford full max_tokens
    if user_credits >= max_cost:
        return {
            "allowed": True,
            "reason": "Sufficient credits",
            "max_cost": max_cost,
            "available_credits": user_credits,
            "remaining_after_max": user_credits - max_cost,
        }

    # Cannot afford full max_tokens — check if partial output is affordable
    affordable = calculate_affordable_max_tokens(model_id, input_tokens, user_credits)

    if affordable is not None and affordable > 0:
        # User can afford some output — allow with capped max_tokens
        capped_cost = calculate_cost(model_id, input_tokens, affordable)
        logger.info(
            "Credit cap: %s max_tokens %d→%d (credits=%.4f, capped_cost=%.6f)",
            model_id,
            max_tokens,
            affordable,
            user_credits,
            capped_cost,
        )
        return {
            "allowed": True,
            "reason": "Capped max_tokens to affordable limit",
            "max_cost": capped_cost,
            "available_credits": user_credits,
            "original_max_tokens": max_tokens,
            "capped_max_tokens": affordable,
        }

    # Cannot afford any output at all — block
    shortfall = max_cost - user_credits
    return {
        "allowed": False,
        "reason": "Insufficient credits",
        "max_cost": max_cost,
        "available_credits": user_credits,
        "shortfall": shortfall,
        "capped_max_tokens": None,
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

    # Check sufficiency (with affordability-based capping)
    check_result = check_credit_sufficiency(
        user_credits,
        max_cost,
        model_id,
        max_output_tokens,
        is_trial,
        input_tokens=input_tokens,
    )

    # Add token info to result
    check_result["input_tokens"] = input_tokens
    check_result["max_output_tokens"] = max_output_tokens

    return check_result
