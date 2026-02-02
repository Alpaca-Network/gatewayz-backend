"""
Unified Credit Handling Service

This module provides centralized credit deduction and trial tracking logic
used by all chat completion endpoints (OpenAI, Anthropic, AI SDK).

Consolidating credit handling in one place ensures consistent billing behavior
across all API endpoints and makes auditing easier.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def handle_credits_and_usage(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
    endpoint: str = "/v1/chat/completions",
) -> float:
    """
    Centralized credit/trial handling logic for all chat endpoints.

    This function handles:
    1. Cost calculation based on model pricing
    2. Trial override detection (prevents stale trial flags from causing free usage)
    3. Trial usage tracking
    4. Credit deduction for paid users
    5. Usage recording for analytics
    6. Rate limit usage updates

    Args:
        api_key: User's API key
        user: User data dict containing id, tier, subscription info
        model: Model ID used for the request
        trial: Trial status dict with is_trial, is_expired flags
        total_tokens: Total tokens used (prompt + completion)
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        elapsed_ms: Request processing time in milliseconds
        endpoint: API endpoint for logging (default: /v1/chat/completions)

    Returns:
        float: Calculated cost in USD

    Raises:
        ValueError: If credit deduction fails due to insufficient credits
        Exception: On other billing errors (re-raised after logging)
    """
    # Import dependencies here to avoid circular imports
    from src.db.rate_limits import update_rate_limit_usage
    from src.db.trials import track_trial_usage
    from src.db.users import deduct_credits, log_api_usage_transaction, record_usage
    from src.services.pricing import calculate_cost_async
    from src.utils.sentry_context import capture_payment_error

    # Helper to run sync functions in thread pool
    async def _to_thread(func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    # Calculate cost using async pricing lookup (supports live API fetch)
    cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)
    is_trial = trial.get("is_trial", False)

    # Defense-in-depth: Override is_trial flag if user has active subscription
    # This protects against webhook delays or failures that leave is_trial=TRUE
    if is_trial and user:
        has_active_subscription = (
            user.get("stripe_subscription_id") is not None
            and user.get("subscription_status") == "active"
        ) or user.get("tier") in ("pro", "max", "admin")

        if has_active_subscription:
            logger.warning(
                "BILLING_OVERRIDE: User %s has is_trial=TRUE but has active subscription "
                "(tier=%s, sub_status=%s, stripe_sub_id=%s). Forcing paid path. Endpoint: %s",
                user.get("id"),
                user.get("tier"),
                user.get("subscription_status"),
                user.get("stripe_subscription_id"),
                endpoint,
            )
            is_trial = False  # Override to paid path

    # Track trial usage (only for legitimate trial users, not paid users with stale flags)
    if is_trial and not trial.get("is_expired"):
        try:
            await _to_thread(
                track_trial_usage,
                api_key,
                total_tokens,
                1,
                model_id=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception as e:
            logger.warning("Failed to track trial usage: %s", e)

    # Log transaction and deduct credits
    if is_trial:
        try:
            await _to_thread(
                log_api_usage_transaction,
                api_key,
                0.0,
                f"API usage - {model} (Trial)",
                {
                    "model": model,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": 0.0,
                    "is_trial": True,
                    "endpoint": endpoint,
                },
                True,
            )
        except Exception as e:
            logger.error(f"Failed to log trial API usage transaction: {e}", exc_info=True)
            capture_payment_error(
                e,
                operation="trial_usage_logging",
                user_id=user.get("id"),
                details={"model": model, "tokens": total_tokens, "is_trial": True, "endpoint": endpoint},
            )
    else:
        try:
            await _to_thread(
                deduct_credits,
                api_key,
                cost,
                f"API usage - {model}",
                {
                    "model": model,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": cost,
                    "endpoint": endpoint,
                },
            )
            await _to_thread(
                record_usage,
                user["id"],
                api_key,
                model,
                total_tokens,
                cost,
                elapsed_ms,
            )
            await _to_thread(update_rate_limit_usage, api_key, total_tokens)
        except Exception as e:
            logger.error("Usage recording error: %s", e)
            capture_payment_error(
                e,
                operation="credit_deduction",
                user_id=user.get("id"),
                amount=cost,
                details={
                    "model": model,
                    "tokens": total_tokens,
                    "cost_usd": cost,
                    "api_key": api_key[:10] + "..." if api_key else None,
                    "endpoint": endpoint,
                },
            )
            raise  # Re-raise to ensure billing errors are not silently ignored

    return cost
