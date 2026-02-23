"""
Unified Credit Handling Service

This module provides centralized credit deduction and trial tracking logic
used by all chat completion endpoints (OpenAI, Anthropic, AI SDK).

Consolidating credit handling in one place ensures consistent billing behavior
across all API endpoints and makes auditing easier.

Key features:
- Retry logic for transient failures (database, network)
- Comprehensive Sentry alerts for billing failures
- Prometheus metrics for monitoring credit deduction reliability
- Background task wrapper for streaming requests with guaranteed delivery
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Retry configuration for credit deduction
CREDIT_DEDUCTION_MAX_RETRIES = 3
CREDIT_DEDUCTION_RETRY_DELAYS = [0.5, 1.0, 2.0]  # Exponential backoff in seconds


def _record_credit_metrics(
    status: str,
    cost: float,
    endpoint: str,
    is_streaming: bool,
    latency_seconds: float | None = None,
    attempt_number: int | None = None,
) -> None:
    """Record Prometheus metrics for credit deduction operations."""
    try:
        from src.services.prometheus_metrics import (
            credit_deduction_total,
            credit_deduction_amount_usd,
            credit_deduction_latency,
            credit_deduction_retry_count,
        )

        streaming_str = "true" if is_streaming else "false"

        # Record deduction attempt
        credit_deduction_total.labels(
            status=status,
            endpoint=endpoint,
            is_streaming=streaming_str,
        ).inc()

        # Record amount
        if cost > 0:
            credit_deduction_amount_usd.labels(
                status=status,
                endpoint=endpoint,
            ).inc(cost)

        # Record latency
        if latency_seconds is not None:
            credit_deduction_latency.labels(
                endpoint=endpoint,
                is_streaming=streaming_str,
            ).observe(latency_seconds)

        # Record retry attempt
        if attempt_number is not None and attempt_number > 1:
            credit_deduction_retry_count.labels(
                attempt_number=str(attempt_number),
                endpoint=endpoint,
            ).inc()

    except Exception as e:
        logger.debug(f"Failed to record credit metrics: {e}")


def _record_missed_deduction(cost: float, reason: str) -> None:
    """Record metrics for missed credit deductions."""
    try:
        from src.services.prometheus_metrics import missed_credit_deductions_usd

        if cost > 0:
            missed_credit_deductions_usd.labels(reason=reason).inc(cost)
    except Exception as e:
        logger.debug(f"Failed to record missed deduction metric: {e}")


def _record_background_task_failure(failure_type: str, endpoint: str) -> None:
    """Record metrics for streaming background task failures."""
    try:
        from src.services.prometheus_metrics import streaming_background_task_failures

        streaming_background_task_failures.labels(
            failure_type=failure_type,
            endpoint=endpoint,
        ).inc()
    except Exception as e:
        logger.debug(f"Failed to record background task failure metric: {e}")


def _send_critical_billing_alert(
    error: Exception,
    user_id: int | None,
    cost: float,
    model: str,
    endpoint: str,
    attempt_number: int,
    is_streaming: bool,
    additional_context: dict[str, Any] | None = None,
) -> None:
    """
    Send critical Sentry alert for billing failures.

    This function is called when credit deduction fails after all retries,
    which could result in lost revenue.
    """
    try:
        import sentry_sdk
        from src.utils.sentry_context import capture_payment_error

        context = {
            "model": model,
            "cost_usd": cost,
            "endpoint": endpoint,
            "attempt_number": attempt_number,
            "max_retries": CREDIT_DEDUCTION_MAX_RETRIES,
            "is_streaming": is_streaming,
            "error_type": type(error).__name__,
            "error_message": str(error)[:500],
        }
        if additional_context:
            context.update(additional_context)

        # Add breadcrumb for context before capturing the error
        sentry_sdk.add_breadcrumb(
            category="billing",
            message=f"Credit deduction failed for user {user_id}",
            level="error",
            data={
                "model": model,
                "cost_usd": cost,
                "endpoint": endpoint,
                "user_id": user_id,
                "attempt_number": attempt_number,
                "is_streaming": is_streaming,
            },
        )

        # Capture with payment error context (includes alerting for significant costs)
        capture_payment_error(
            error,
            operation="credit_deduction_failed",
            user_id=str(user_id) if user_id else None,
            amount=cost,
            details=context,
        )

    except Exception as e:
        logger.error(f"Failed to send Sentry alert for billing failure: {e}")


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
    is_streaming: bool = False,
) -> float:
    """
    Centralized credit/trial handling logic for all chat endpoints.

    This function handles:
    1. Cost calculation based on model pricing
    2. Trial override detection (prevents stale trial flags from causing free usage)
    3. Trial usage tracking
    4. Credit deduction for paid users (with retry logic)
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
        is_streaming: Whether this is a streaming request (affects retry behavior)

    Returns:
        float: Calculated cost in USD

    Raises:
        ValueError: If credit deduction fails due to insufficient credits
        Exception: On other billing errors (re-raised after logging)
    """
    # Import dependencies here to avoid circular imports
    from src.db.rate_limits import update_rate_limit_usage
    from src.db.trials import track_trial_usage_for_key
    from src.db.users import deduct_credits, log_api_usage_transaction, record_usage
    from src.services.pricing import calculate_cost_async
    from src.utils.sentry_context import capture_payment_error

    start_time = time.monotonic()

    # Helper to run sync functions in thread pool
    async def _to_thread(func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    # Calculate cost using async pricing lookup (supports live API fetch)
    cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)
    is_trial = trial.get("is_trial", False)

    # Defense-in-depth: Override is_trial flag if user has any subscription indicators
    # This protects against webhook delays or failures that leave is_trial=TRUE
    # DEFENSIVE APPROACH: Assume trial is stale if ANY subscription indicator is present
    if is_trial and user:
        # Check for multiple subscription indicators (defensive approach)
        has_stripe_subscription_id = user.get("stripe_subscription_id") is not None
        has_stripe_customer_id = user.get("stripe_customer_id") is not None
        has_paid_tier = user.get("tier") in ("pro", "max", "admin")
        has_subscription_allowance = (user.get("subscription_allowance") or 0) > 0
        has_active_status = user.get("subscription_status") == "active"  # noqa: F841

        # Count how many indicators suggest the user is NOT on trial
        subscription_indicators = [
            has_stripe_subscription_id,
            has_stripe_customer_id,
            has_paid_tier,
            has_subscription_allowance,
        ]
        indicator_count = sum(subscription_indicators)

        # If ANY subscription indicator is present, assume trial flag is stale
        # This is defensive to prevent paid users from getting free service
        has_subscription_indicators = indicator_count > 0

        if has_subscription_indicators:
            logger.warning(
                "BILLING_OVERRIDE: User %s has is_trial=TRUE but shows subscription indicators (%d/4). "
                "Forcing paid path to prevent free service for paid subscribers. "
                "Details: tier=%s, sub_status=%s, stripe_sub_id=%s, stripe_customer_id=%s, "
                "allowance=$%.2f, endpoint=%s",
                user.get("id"),
                indicator_count,
                user.get("tier"),
                user.get("subscription_status"),
                user.get("stripe_subscription_id"),
                user.get("stripe_customer_id"),
                user.get("subscription_allowance", 0),
                endpoint,
            )
            is_trial = False  # Override to paid path

            # Send Sentry alert for high indicator count (likely webhook failure)
            if indicator_count >= 3:
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(
                        f"Trial flag override for user {user.get('id')} with {indicator_count}/4 subscription indicators",
                        level="warning",
                        extras={
                            "user_id": user.get("id"),
                            "tier": user.get("tier"),
                            "subscription_status": user.get("subscription_status"),
                            "has_stripe_subscription_id": has_stripe_subscription_id,
                            "has_stripe_customer_id": has_stripe_customer_id,
                            "has_paid_tier": has_paid_tier,
                            "has_subscription_allowance": has_subscription_allowance,
                            "endpoint": endpoint,
                        }
                    )
                except Exception:
                    pass

    # Track trial usage (only for legitimate trial users, not paid users with stale flags)
    if is_trial and not trial.get("is_expired"):
        try:
            await _to_thread(
                track_trial_usage_for_key,
                api_key,
                total_tokens,
                1,  # requests_used
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
            # Record successful trial transaction
            latency = time.monotonic() - start_time
            _record_credit_metrics("success", 0.0, endpoint, is_streaming, latency)
        except Exception as e:
            logger.error(f"Failed to log trial API usage transaction: {e}", exc_info=True)
            capture_payment_error(
                e,
                operation="trial_usage_logging",
                user_id=user.get("id"),
                details={
                    "model": model,
                    "tokens": total_tokens,
                    "is_trial": True,
                    "endpoint": endpoint,
                },
            )
    else:
        # Paid user - deduct credits with retry logic
        last_error = None
        deduction_successful = False

        for attempt in range(1, CREDIT_DEDUCTION_MAX_RETRIES + 1):
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
                        "is_streaming": is_streaming,
                        "attempt_number": attempt,
                    },
                )

                # CRITICAL: Once deduct_credits succeeds, mark as successful immediately
                # to prevent duplicate deductions if subsequent operations fail.
                # The deduction is the critical billing operation - usage logging and
                # rate limit updates are secondary and should not trigger a retry.
                deduction_successful = True

                # Record success metrics
                latency = time.monotonic() - start_time
                status = "success" if attempt == 1 else "retried"
                _record_credit_metrics(status, cost, endpoint, is_streaming, latency, attempt)

                if attempt > 1:
                    logger.info(
                        f"Credit deduction succeeded on attempt {attempt} for user {user.get('id')}, "
                        f"cost=${cost:.6f}, model={model}"
                    )
                break

            except ValueError as e:
                # Insufficient credits or validation error - don't retry, raise immediately
                latency = time.monotonic() - start_time
                _record_credit_metrics("failed", cost, endpoint, is_streaming, latency, attempt)
                logger.error(f"Credit deduction validation error: {e}")
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
                        "error_type": "validation",
                    },
                )
                raise

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Credit deduction attempt {attempt}/{CREDIT_DEDUCTION_MAX_RETRIES} failed: {e}"
                )

                # Record retry metric
                _record_credit_metrics("retried", cost, endpoint, is_streaming, None, attempt)

                if attempt < CREDIT_DEDUCTION_MAX_RETRIES:
                    # Wait before retry with exponential backoff
                    delay = CREDIT_DEDUCTION_RETRY_DELAYS[attempt - 1]
                    logger.info(f"Retrying credit deduction in {delay}s...")
                    await asyncio.sleep(delay)

        # If all retries failed, handle the failure
        if not deduction_successful:
            latency = time.monotonic() - start_time
            _record_credit_metrics(
                "failed", cost, endpoint, is_streaming, latency, CREDIT_DEDUCTION_MAX_RETRIES
            )
            _record_missed_deduction(cost, "retry_exhausted")

            # Send critical alert
            _send_critical_billing_alert(
                error=last_error or Exception("Unknown error"),
                user_id=user.get("id"),
                cost=cost,
                model=model,
                endpoint=endpoint,
                attempt_number=CREDIT_DEDUCTION_MAX_RETRIES,
                is_streaming=is_streaming,
                additional_context={
                    "api_key_prefix": api_key[:10] + "..." if api_key else None,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )

            logger.error(
                f"CRITICAL: Credit deduction failed after {CREDIT_DEDUCTION_MAX_RETRIES} attempts. "
                f"User {user.get('id')} owes ${cost:.6f} for {model}. Error: {last_error}"
            )

            # Re-raise to ensure billing errors are not silently ignored
            raise RuntimeError(
                f"Credit deduction failed after {CREDIT_DEDUCTION_MAX_RETRIES} attempts: {last_error}"
            ) from last_error

        # Secondary operations: record usage and update rate limits
        # These are done AFTER the retry loop to prevent duplicate deductions.
        # Failures here are logged but don't affect the billing outcome.
        try:
            await _to_thread(
                record_usage,
                user["id"],
                api_key,
                model,
                total_tokens,
                cost,
                elapsed_ms,
            )
        except Exception as e:
            logger.warning(
                f"Failed to record usage after successful deduction for user {user.get('id')}: {e}"
            )

        try:
            await _to_thread(update_rate_limit_usage, api_key, total_tokens)
        except Exception as e:
            logger.warning(
                f"Failed to update rate limit usage after successful deduction for user {user.get('id')}: {e}"
            )

    return cost


async def handle_credits_and_usage_with_fallback(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
    endpoint: str = "/v1/chat/completions",
    is_streaming: bool = True,
) -> tuple[float, bool]:
    """
    Wrapper for streaming background tasks that handles failures gracefully.

    This function is specifically designed for streaming requests where:
    1. The response has already been sent to the client
    2. We MUST attempt credit deduction but cannot block/fail the request
    3. Failures must be tracked for manual reconciliation
    4. Additional retries beyond the standard retry mechanism to catch edge cases

    Args:
        (same as handle_credits_and_usage)

    Returns:
        tuple[float, bool]: (cost, success) - cost calculated, whether deduction succeeded
    """
    # ENHANCED: Add extra retry layer for streaming background tasks
    # Standard handle_credits_and_usage already retries 3 times, but we add
    # one more layer here since streaming failures mean complete revenue loss
    MAX_BACKGROUND_RETRIES = 2  # Try twice at this level
    last_exception = None

    for attempt in range(1, MAX_BACKGROUND_RETRIES + 1):
        try:
            cost = await handle_credits_and_usage(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=elapsed_ms,
                endpoint=endpoint,
                is_streaming=is_streaming,
            )

            if attempt > 1:
                logger.info(
                    f"Streaming credit deduction succeeded on background retry {attempt}/{MAX_BACKGROUND_RETRIES} "
                    f"for user {user.get('id')}, cost=${cost:.6f}"
                )

            return cost, True

        except Exception as e:
            last_exception = e
            logger.warning(
                f"Streaming background credit deduction attempt {attempt}/{MAX_BACKGROUND_RETRIES} failed: {e}"
            )

            # Wait before retry (exponential backoff)
            if attempt < MAX_BACKGROUND_RETRIES:
                retry_delay = 1.0 * attempt  # 1s, 2s
                await asyncio.sleep(retry_delay)

    # All retries exhausted - handle failure
    e = last_exception

    # Calculate cost for tracking even if deduction failed
    try:
        from src.services.pricing import calculate_cost_async

        cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)
    except Exception:
        # Fallback cost estimation
        cost = (prompt_tokens + completion_tokens) * 0.00002

    # Record the failure
    _record_background_task_failure("credit_deduction", endpoint)
    _record_missed_deduction(cost, "background_task_failure")

    # Log for manual reconciliation
    logger.error(
        f"BILLING_RECONCILIATION_NEEDED: Streaming credit deduction failed after {MAX_BACKGROUND_RETRIES} background retries. "
        f"User: {user.get('id')}, API Key: {api_key[:10]}..., "
        f"Model: {model}, Cost: ${cost:.6f}, "
        f"Tokens: {total_tokens} (prompt: {prompt_tokens}, completion: {completion_tokens}), "
        f"Endpoint: {endpoint}, Error: {e}"
    )

    # Send critical Sentry alert for streaming failures
    try:
        import sentry_sdk
        sentry_sdk.capture_message(
            f"CRITICAL: Streaming credit deduction failed completely for user {user.get('id')}",
            level="error",
            extras={
                "user_id": user.get("id"),
                "api_key_prefix": api_key[:10] + "..." if api_key else None,
                "model": model,
                "cost_usd": cost,
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "endpoint": endpoint,
                "error": str(e),
                "background_retries": MAX_BACKGROUND_RETRIES,
            }
        )
    except Exception:
        pass

    # Try to log to a reconciliation table for later processing
    try:
        await _log_failed_deduction_for_reconciliation(
            user_id=user.get("id"),
            api_key=api_key,
            model=model,
            cost=cost,
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            endpoint=endpoint,
            error=str(e),
        )
    except Exception as log_error:
        logger.error(f"Failed to log deduction for reconciliation: {log_error}")

    return cost, False


async def _log_failed_deduction_for_reconciliation(
    user_id: int | None,
    api_key: str,
    model: str,
    cost: float,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    endpoint: str,
    error: str,
) -> None:
    """
    Log failed credit deductions to database for later reconciliation.

    This creates an audit trail of missed deductions that can be processed
    manually or by a background job.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try to insert into a reconciliation table
        # If the table doesn't exist, this will fail silently
        await asyncio.to_thread(
            lambda: client.table("credit_deduction_failures")
            .insert(
                {
                    "user_id": user_id,
                    "api_key_prefix": api_key[:10] + "..." if api_key else None,
                    "model": model,
                    "cost_usd": cost,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": endpoint,
                    "error_message": error[:1000],
                    "status": "pending",
                }
            )
            .execute()
        )
        logger.info(f"Logged failed deduction for reconciliation: user={user_id}, cost=${cost:.6f}")

    except Exception as e:
        # Log at warning level so failures to record reconciliation data are visible
        logger.warning(f"Could not log to reconciliation table: {e}")
