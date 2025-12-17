"""
Shared Helper Functions for Chat Endpoints

This module contains shared logic extracted from chat_completions() and unified_responses()
to eliminate ~585 lines of code duplication.

Benefits:
- Single source of truth for auth, validation, billing logic
- Easier to maintain and test
- Consistent behavior across endpoints
- Reduced code duplication by 50%+
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from src.config.config import Config
from src.services.auth import get_user, _fallback_get_user
from src.services.trial import validate_trial_access
from src.services.plan_limits import enforce_plan_limits, _ensure_plan_capacity
from src.services.rate_limit import get_rate_limit_manager, create_rate_limit_alert
from src.services.billing import calculate_cost, deduct_credits, log_api_usage_transaction
from src.services.usage import record_usage, increment_api_key_usage, update_rate_limit_usage
from src.utils.logging_utils import mask_key, sanitize_for_logging

logger = logging.getLogger(__name__)


# ============================================================================
# Authentication & User Validation
# ============================================================================

async def validate_user_and_auth(
    api_key: Optional[str],
    _to_thread,
    request_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    Validate user and determine if request is anonymous.

    Args:
        api_key: API key from request (None for anonymous)
        _to_thread: Thread executor function
        request_id: Request correlation ID for logging

    Returns:
        Tuple of (user dict or None, is_anonymous bool)

    Raises:
        HTTPException: If API key is invalid
    """
    is_anonymous = api_key is None

    if is_anonymous:
        logger.info("Processing anonymous request (request_id=%s)", request_id)
        return None, True

    # Authenticated user - get user info
    user = await _to_thread(get_user, api_key)
    if not user and Config.IS_TESTING:
        logger.debug("Fallback user lookup invoked for %s", mask_key(api_key))
        user = await _to_thread(_fallback_get_user, api_key)

    if not user:
        logger.warning("Invalid API key or user not found for key %s", mask_key(api_key))
        raise HTTPException(status_code=401, detail="Invalid API key")

    return user, False


# ============================================================================
# Trial Access Validation
# ============================================================================

async def validate_trial(
    api_key: str,
    _to_thread,
) -> Dict[str, Any]:
    """
    Validate trial access for a user.

    Args:
        api_key: User's API key
        _to_thread: Thread executor function

    Returns:
        Trial validation result dict

    Raises:
        HTTPException: If trial is expired or limits exceeded
    """
    trial = await _to_thread(validate_trial_access, api_key)

    if not trial.get("is_valid", False):
        if trial.get("is_trial") and trial.get("is_expired"):
            raise HTTPException(
                status_code=403,
                detail=trial["error"],
                headers={
                    "X-Trial-Expired": "true",
                    "X-Trial-End-Date": trial.get("trial_end_date", ""),
                },
            )
        elif trial.get("is_trial"):
            headers = {}
            for k in ("remaining_tokens", "remaining_requests", "remaining_credits"):
                if k in trial:
                    headers[f"X-Trial-{k.replace('_','-').title()}"] = str(trial[k])
            raise HTTPException(status_code=429, detail=trial["error"], headers=headers)
        else:
            raise HTTPException(status_code=403, detail=trial.get("error", "Access denied"))

    return trial


# ============================================================================
# Plan Limit Validation
# ============================================================================

async def check_plan_limits(
    user_id: int,
    environment_tag: str,
    tokens_used: int,
    _to_thread,
) -> Dict[str, Any]:
    """
    Check if user is within plan limits.

    Args:
        user_id: User's database ID
        environment_tag: Environment (live/staging)
        tokens_used: Number of tokens to check (0 for pre-check)
        _to_thread: Thread executor function

    Returns:
        Plan limit check result

    Raises:
        HTTPException: If plan limits exceeded
    """
    plan_result = await _to_thread(enforce_plan_limits, user_id, tokens_used, environment_tag)

    if not plan_result.get("allowed", False):
        raise HTTPException(
            status_code=429,
            detail=f"Plan limit exceeded: {plan_result.get('reason', 'unknown')}",
        )

    if tokens_used == 0:
        logger.debug(
            "Plan pre-check passed for user %s (env=%s): %s",
            sanitize_for_logging(str(user_id)),
            environment_tag,
            plan_result,
        )

    return plan_result


async def ensure_capacity(
    user_id: int,
    environment_tag: str,
    _to_thread,
) -> None:
    """
    Fast-fail check for plan capacity before provider requests.

    Args:
        user_id: User's database ID
        environment_tag: Environment (live/staging)
        _to_thread: Thread executor function

    Raises:
        HTTPException: If capacity check fails
    """
    await _to_thread(_ensure_plan_capacity, user_id, environment_tag)


# ============================================================================
# Rate Limit Validation
# ============================================================================

async def check_rate_limits(
    api_key: str,
    tokens_used: int,
    is_trial: bool,
    _to_thread,
) -> Optional[Any]:
    """
    Check rate limits for non-trial users.

    Args:
        api_key: User's API key
        tokens_used: Number of tokens (0 for pre-check)
        is_trial: Whether user is on trial
        _to_thread: Thread executor function

    Returns:
        Rate limit check result or None if trial user

    Raises:
        HTTPException: If rate limits exceeded
    """
    if is_trial:
        return None

    rate_limit_mgr = get_rate_limit_manager()
    rl_result = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=tokens_used)

    if not rl_result.allowed:
        await _to_thread(
            create_rate_limit_alert,
            api_key,
            "rate_limit_exceeded",
            {
                "reason": rl_result.reason,
                "retry_after": rl_result.retry_after,
                "remaining_requests": rl_result.remaining_requests,
                "remaining_tokens": rl_result.remaining_tokens,
                "tokens_requested": tokens_used,
            },
        )

        headers = {"Retry-After": str(rl_result.retry_after)} if rl_result.retry_after else None
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {rl_result.reason}",
            headers=headers,
        )

    return rl_result


# ============================================================================
# Billing & Usage Recording
# ============================================================================

async def handle_billing(
    api_key: str,
    user_id: int,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    elapsed_ms: int,
    is_trial: bool,
    _to_thread,
) -> float:
    """
    Handle billing and usage recording for a request.

    Args:
        api_key: User's API key
        user_id: User's database ID
        model: Model used
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        total_tokens: Total tokens used
        elapsed_ms: Request latency in milliseconds
        is_trial: Whether user is on trial
        _to_thread: Thread executor function

    Returns:
        Cost in USD

    Raises:
        HTTPException: If credit deduction fails
    """
    cost = calculate_cost(model, prompt_tokens, completion_tokens)

    if is_trial:
        # Log transaction for trial users (with $0 cost)
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
                },
                True,
            )
        except Exception as e:
            logger.error(f"Failed to log trial API usage transaction: {e}", exc_info=True)
    else:
        # For non-trial users, deduct credits
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
                },
            )
            await _to_thread(
                record_usage,
                user_id,
                api_key,
                model,
                total_tokens,
                cost,
                elapsed_ms,
            )
        except ValueError as e:
            raise HTTPException(status_code=402, detail=str(e))
        except Exception as e:
            logger.error("Usage recording error: %s", e)

    # Update rate limit usage
    await _to_thread(update_rate_limit_usage, api_key, total_tokens)

    # Increment API key usage counter
    await _to_thread(increment_api_key_usage, api_key)

    return cost


# ============================================================================
# Constants
# ============================================================================

# PostgreSQL integer range limits
POSTGRES_INT_MIN = -2147483648
POSTGRES_INT_MAX = 2147483647


# ============================================================================
# Helper Functions
# ============================================================================

def validate_session_id(session_id: Optional[int]) -> Optional[int]:
    """
    Validate session_id is within PostgreSQL integer range.

    Args:
        session_id: Session ID to validate

    Returns:
        session_id if valid, None otherwise
    """
    if session_id is None:
        return None

    if session_id < POSTGRES_INT_MIN or session_id > POSTGRES_INT_MAX:
        logger.warning(
            f"Invalid session_id {sanitize_for_logging(str(session_id))}: "
            f"out of PostgreSQL integer range ({POSTGRES_INT_MIN} to {POSTGRES_INT_MAX}). Ignoring."
        )
        return None

    return session_id


async def inject_chat_history(
    session_id: Optional[int],
    user_id: int,
    messages: list,
    _to_thread,
    get_chat_session_func,
) -> list:
    """
    Inject chat history from a session into the messages list.

    Args:
        session_id: Chat session ID (can be None)
        user_id: User's database ID
        messages: Current request messages
        _to_thread: Thread executor function
        get_chat_session_func: Function to fetch chat session

    Returns:
        Messages list with history prepended (or original if no history)
    """
    if not session_id:
        return messages

    try:
        session = await _to_thread(get_chat_session_func, session_id, user_id)
        if session and session.get("messages"):
            # Transform DB messages to OpenAI format and prepend to current messages
            history_messages = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in session["messages"]
            ]
            messages = history_messages + messages
            logger.info(
                "Injected %d messages from session %s",
                len(history_messages),
                sanitize_for_logging(str(session_id)),
            )
        else:
            logger.debug(
                "No history found for session %s or session doesn't exist",
                sanitize_for_logging(str(session_id)),
            )
    except Exception as e:
        # Don't fail the request if history fetch fails
        logger.warning(
            "Failed to fetch chat history for session %s: %s",
            sanitize_for_logging(str(session_id)),
            sanitize_for_logging(str(e)),
        )

    return messages


def build_optional_params(req, param_names: tuple) -> dict:
    """
    Extract optional parameters from request object.

    Args:
        req: Request object
        param_names: Tuple of parameter names to extract

    Returns:
        Dictionary of parameter names to values (only non-None values included)
    """
    optional = {}
    for name in param_names:
        val = getattr(req, name, None)
        if val is not None:
            optional[name] = val
    return optional


def get_rate_limit_headers(rl_result: Any) -> Dict[str, str]:
    """Extract rate limit headers from rate limit result."""
    if not rl_result:
        return {}

    headers = {}
    if hasattr(rl_result, 'remaining_requests') and rl_result.remaining_requests is not None:
        headers["X-RateLimit-Remaining-Requests"] = str(rl_result.remaining_requests)
    if hasattr(rl_result, 'remaining_tokens') and rl_result.remaining_tokens is not None:
        headers["X-RateLimit-Remaining-Tokens"] = str(rl_result.remaining_tokens)
    if hasattr(rl_result, 'retry_after') and rl_result.retry_after is not None:
        headers["X-RateLimit-Retry-After"] = str(rl_result.retry_after)

    return headers


def transform_input_to_messages(input_messages: list) -> list:
    """
    Transform Responses API 'input' format to standard 'messages' format.

    Handles:
    - Simple text messages
    - Multimodal messages with images
    - Content type transformations (input_text -> text, input_image_url -> image_url, etc.)

    Args:
        input_messages: List of InputMessage objects from Responses API

    Returns:
        List of standard message dicts in OpenAI format

    Raises:
        HTTPException: If input format is invalid
    """
    messages = []
    try:
        for inp_msg in input_messages:
            # Convert InputMessage to standard message format
            if isinstance(inp_msg.content, str):
                messages.append({"role": inp_msg.role, "content": inp_msg.content})
            elif isinstance(inp_msg.content, list):
                # Multimodal content - transform to OpenAI format
                transformed_content = []
                for item in inp_msg.content:
                    if isinstance(item, dict):
                        # Map input types to OpenAI chat format
                        if item.get("type") == "input_text":
                            transformed_content.append(
                                {"type": "text", "text": item.get("text", "")}
                            )
                        elif item.get("type") == "output_text":
                            # Transform Responses API output_text to standard text format
                            # This handles cases where clients send assistant messages with
                            # output_text content type from previous response conversations
                            transformed_content.append(
                                {"type": "text", "text": item.get("text", "")}
                            )
                        elif item.get("type") == "input_image_url":
                            transformed_content.append(
                                {"type": "image_url", "image_url": item.get("image_url", {})}
                            )
                        elif item.get("type") in ("text", "image_url"):
                            # Already in correct format
                            transformed_content.append(item)
                        else:
                            logger.warning(f"Unknown content type: {item.get('type')}, skipping")
                            # Skip unknown types instead of passing them through to avoid
                            # provider API errors like "Unexpected content chunk type"
                    else:
                        logger.warning(f"Invalid content item (not a dict): {type(item)}")

                messages.append({"role": inp_msg.role, "content": transformed_content})
            else:
                logger.error(f"Invalid content type: {type(inp_msg.content)}")
                raise HTTPException(
                    status_code=400, detail=f"Invalid content type: {type(inp_msg.content)}"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error transforming input to messages: {e}, input: {input_messages}")
        raise HTTPException(status_code=400, detail=f"Invalid input format: {str(e)}")

    return messages


def validate_and_adjust_max_tokens(optional: dict, model: str) -> None:
    """
    Validate and adjust max_tokens for models with minimum token requirements.

    Google Gemini models require max_tokens >= 16. This function automatically
    adjusts the value if it's below the minimum to prevent API errors.

    Args:
        optional: Dictionary of optional parameters
        model: Model name to check requirements for
    """
    if "max_tokens" not in optional or optional["max_tokens"] is None:
        return

    model_lower = model.lower()

    if "gemini" in model_lower or "google" in model_lower:
        min_tokens = 16
        if optional["max_tokens"] < min_tokens:
            logger.warning(
                f"Adjusting max_tokens from {optional['max_tokens']} to {min_tokens} "
                f"for Gemini model {model} (minimum requirement)"
            )
            optional["max_tokens"] = min_tokens
