"""Post-processing pipeline for chat completions — billing, metrics, and background tasks."""

import asyncio
import logging

from src.db.activity import get_provider_from_model, log_activity
from src.db.api_keys import increment_api_key_usage
from src.db.chat_completion_requests import save_chat_completion_request_with_cost
from src.db.chat_history import get_chat_session, save_chat_message
from src.db.plans import enforce_plan_limits
from src.services.anonymous_rate_limiter import record_anonymous_request
from src.services.passive_health_monitor import capture_model_health
from src.services.pricing import calculate_cost_async
from src.services.prometheus_metrics import (
    credits_used,
    get_trace_exemplar,
    model_inference_duration,
    model_inference_requests,
    tokens_used,
)
from src.services.redis_metrics import get_redis_metrics
from src.utils.ai_tracing import AIRequestType, AITracer
from src.utils.errors import APIExceptions
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def _ensure_plan_capacity(user_id: int, environment_tag: str) -> dict:
    """Run a lightweight plan-limit precheck before making upstream calls."""
    plan_check = await _to_thread(enforce_plan_limits, user_id, 0, environment_tag)
    if not plan_check.get("allowed", False):
        raise APIExceptions.plan_limit_exceeded(reason=plan_check.get("reason", "unknown"))
    return plan_check


async def _handle_credits_and_usage(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
    is_streaming: bool = False,
    request_id: str | None = None,
    already_charged: bool = False,
) -> float:
    """
    Centralized credit/trial handling logic.

    This is a thin wrapper around the shared credit_handler module to maintain
    backward compatibility while ensuring consistent billing across all endpoints.

    Args:
        is_streaming: Whether this is a streaming request (affects retry behavior)
        request_id: Optional UUID idempotency key to prevent duplicate deductions
        already_charged: When True, the unified handler already deducted credits and
            recorded usage; skip the duplicate deduction here (rate-limit + shadow
            ledger bookkeeping still runs).

    Returns: cost (float)
    """
    from src.services.credit_handler import handle_credits_and_usage

    return await handle_credits_and_usage(
        api_key=api_key,
        user=user,
        model=model,
        trial=trial,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_ms=elapsed_ms,
        endpoint="/v1/chat/completions",
        is_streaming=is_streaming,
        request_id=request_id,
        already_charged=already_charged,
    )


async def _handle_credits_and_usage_with_fallback(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
    request_id: str | None = None,
) -> tuple[float, bool]:
    """
    Credit handling for streaming background tasks with fallback on failure.

    This wrapper is specifically designed for streaming requests where the response
    has already been sent to the client. It:
    1. Attempts credit deduction with full retry logic
    2. On failure, logs for reconciliation and returns (cost, False)
    3. Never raises - failures are tracked for manual reconciliation

    Args:
        request_id: Optional UUID idempotency key to prevent duplicate deductions

    Returns: tuple[float, bool] - (cost, success)
    """
    from src.services.credit_handler import handle_credits_and_usage_with_fallback

    return await handle_credits_and_usage_with_fallback(
        api_key=api_key,
        user=user,
        model=model,
        trial=trial,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_ms=elapsed_ms,
        endpoint="/v1/chat/completions",
        is_streaming=True,
        request_id=request_id,
    )


async def _record_inference_metrics_and_health(
    provider: str,
    model: str,
    elapsed_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    success: bool = True,
    error_message: str | None = None,
):
    """
    Record Prometheus metrics, Redis metrics, and passive health monitoring.

    This centralizes metrics recording for both streaming and non-streaming requests.
    """
    try:
        # Record Prometheus metrics with trace exemplars for metrics->traces correlation
        status = "success" if success else "error"
        exemplar = get_trace_exemplar()

        # Request count
        model_inference_requests.labels(provider=provider, model=model, status=status).inc(
            1, exemplar=exemplar
        )

        # Duration
        model_inference_duration.labels(provider=provider, model=model).observe(
            elapsed_seconds, exemplar=exemplar
        )

        # Token usage
        if prompt_tokens > 0:
            tokens_used.labels(provider=provider, model=model, token_type="input").inc(
                prompt_tokens, exemplar=exemplar
            )

        if completion_tokens > 0:
            tokens_used.labels(provider=provider, model=model, token_type="output").inc(
                completion_tokens, exemplar=exemplar
            )

        # Credits consumed
        if cost > 0:
            credits_used.labels(provider=provider, model=model).inc(cost, exemplar=exemplar)

        # Record Redis metrics (real-time dashboards)
        redis_metrics = get_redis_metrics()
        await redis_metrics.record_request(
            provider=provider,
            model=model,
            latency_ms=int(elapsed_seconds * 1000),
            success=success,
            cost=cost,
            tokens_input=prompt_tokens,
            tokens_output=completion_tokens,
            error_message=error_message,
        )

        # Passive health monitoring (background task)
        response_time_ms = int(elapsed_seconds * 1000)
        health_status = "success" if success else "error"

        # Create usage dict for health monitoring
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

        # Call passive health monitor in background (non-blocking)
        # Note: capture_model_health is already async, so we just create a task for it
        asyncio.create_task(
            capture_model_health(
                provider=provider,
                model=model,
                response_time_ms=response_time_ms,
                status=health_status,
                error_message=error_message,
                usage=usage,
            )
        )

        logger.debug(
            f"Recorded metrics for {provider}/{model}: "
            f"duration={elapsed_seconds:.3f}s, "
            f"tokens={prompt_tokens}+{completion_tokens}, "
            f"cost=${cost:.4f}, "
            f"status={status}"
        )

    except Exception as e:
        # Never let metrics recording break the main flow
        logger.warning(f"Failed to record inference metrics: {e}", exc_info=True)


async def _process_stream_completion_background(
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    accumulated_content,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    elapsed,
    provider,
    is_anonymous=False,
    request_id=None,
    client_ip=None,
    api_key_id=None,
):
    """
    Background task for post-stream processing (100-200ms faster [DONE] event!)

    This runs asynchronously after the stream completes, allowing the [DONE]
    event to be sent immediately without waiting for database operations.

    For anonymous users, only metrics recording is performed (no credits, usage tracking, or history).
    """
    try:
        # Add distributed tracing for streaming completion
        async with AITracer.trace_inference(
            provider=provider,
            model=model,
            request_type=AIRequestType.CHAT_COMPLETION,
            operation_name=f"stream_completion_{provider}_{model}",
        ) as trace_ctx:
            # Calculate cost for tracing
            cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)

            # Set token usage and cost on trace span
            trace_ctx.set_token_usage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            trace_ctx.set_cost(cost)

            # Set response model (for streaming, use requested model as we don't capture response model)
            trace_ctx.set_response_model(
                response_model=model,
                finish_reason="stop",  # Streaming completed successfully
            )

            # Set user info if authenticated
            if not is_anonymous and user:
                trace_ctx.set_user_info(
                    user_id=str(user.get("id")),
                    tier="trial" if trial.get("is_trial") else "paid",
                )

            # Add streaming-specific event
            trace_ctx.add_event(
                "stream_completed",
                {
                    "content_length": len(accumulated_content),
                    "elapsed_seconds": elapsed,
                },
            )

            # Skip user-specific operations for anonymous requests
            if is_anonymous:
                logger.info("Skipping user-specific post-processing for anonymous request")

                # Record anonymous usage for rate limiting (IMPORTANT: prevents abuse)
                if client_ip:
                    try:
                        await _to_thread(record_anonymous_request, client_ip, model)
                    except Exception as e:
                        logger.warning(f"Failed to record anonymous request: {e}")

                # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
                cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)
                await _record_inference_metrics_and_health(
                    provider=provider,
                    model=model,
                    elapsed_seconds=elapsed,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=cost,
                    success=True,
                    error_message=None,
                )
                # Capture health metrics (passive monitoring)
                try:
                    await capture_model_health(
                        provider=provider,
                        model=model,
                        response_time_ms=elapsed * 1000,
                        status="success",
                        usage={
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                        },
                    )
                except Exception as e:
                    logger.debug(f"Failed to capture health metric: {e}")
                return

            # Handle credits and usage (centralized helper with fallback for streaming)
            # Use the fallback handler which:
            # 1. Has built-in retry logic with exponential backoff
            # 2. Logs failures for reconciliation instead of crashing
            # 3. Records metrics for monitoring credit deduction reliability
            cost, credit_deduction_success = await _handle_credits_and_usage_with_fallback(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=int(elapsed * 1000),
                request_id=request_id,
            )

            if not credit_deduction_success:
                logger.warning(
                    f"Credit deduction failed for streaming request. "
                    f"User: {user.get('id')}, Model: {model}, Cost: ${cost:.6f}. "
                    f"Logged for reconciliation."
                )
                # NOTE: Credit deduction failed, so no credits were taken.
                # No refund needed here - the reconciliation log handles this case.

            # Increment API key usage counter
            await _to_thread(increment_api_key_usage, api_key)

            # Record Prometheus metrics and passive health monitoring
            await _record_inference_metrics_and_health(
                provider=provider,
                model=model,
                elapsed_seconds=elapsed,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                success=True,
                error_message=None,
            )

            # Log activity
            try:
                provider_name = get_provider_from_model(model)
                speed = total_tokens / elapsed if elapsed > 0 else 0
                await _to_thread(
                    log_activity,
                    user_id=user["id"],
                    model=model,
                    provider=provider_name,
                    tokens=total_tokens,
                    cost=cost,
                    speed=speed,
                    finish_reason="stop",
                    app="API",
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "endpoint": "/v1/chat/completions",
                        "stream": True,
                        "session_id": session_id,
                        "gateway": provider,
                    },
                )
            except Exception as e:
                logger.error(
                    f"Failed to log activity for user {user['id']}, model {model}: {e}",
                    exc_info=True,
                )

            # Save chat history
            # Validate session_id before attempting to save
            if session_id:
                if session_id < -2147483648 or session_id > 2147483647:
                    logger.warning(
                        "Invalid session_id %s in streaming response: out of PostgreSQL integer range. Skipping history save.",
                        sanitize_for_logging(str(session_id)),
                    )
                    session_id = None

            if session_id:
                try:
                    session = await _to_thread(get_chat_session, session_id, user["id"])
                    if session:
                        last_user = None
                        for m in reversed(messages):
                            if m.get("role") == "user":
                                last_user = m
                                break
                        if last_user:
                            user_content = last_user.get("content", "")
                            if isinstance(user_content, list):
                                text_parts = []
                                for item in user_content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        text_parts.append(item.get("text", ""))
                                user_content = (
                                    " ".join(text_parts) if text_parts else "[multimodal content]"
                                )

                            await _to_thread(
                                save_chat_message,
                                session_id,
                                "user",
                                user_content,
                                model,
                                0,
                                user["id"],
                            )

                        if accumulated_content:
                            await _to_thread(
                                save_chat_message,
                                session_id,
                                "assistant",
                                accumulated_content,
                                model,
                                total_tokens,
                                user["id"],
                            )
                except Exception as e:
                    logger.error(
                        f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                        exc_info=True,
                    )

            # Capture health metrics (passive monitoring)
            try:
                await capture_model_health(
                    provider=provider,
                    model=model,
                    response_time_ms=elapsed * 1000,
                    status="success",
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to capture health metric: {e}")

            # Save chat completion request metadata to database with cost tracking
            if request_id:
                try:
                    # Calculate cost breakdown for analytics
                    from src.services.pricing import get_model_pricing

                    pricing_info = get_model_pricing(model)
                    input_cost = prompt_tokens * pricing_info.get("prompt", 0)
                    output_cost = completion_tokens * pricing_info.get("completion", 0)
                    total_cost = input_cost + output_cost

                    await _to_thread(
                        save_chat_completion_request_with_cost,
                        request_id=request_id,
                        model_name=model,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        processing_time_ms=int(elapsed * 1000),
                        cost_usd=total_cost,
                        input_cost_usd=input_cost,
                        output_cost_usd=output_cost,
                        pricing_source="calculated",
                        status="completed",
                        error_message=None,
                        user_id=user["id"] if user else None,
                        provider_name=provider,
                        model_id=None,
                        api_key_id=api_key_id,
                        is_anonymous=is_anonymous,
                    )
                except Exception as e:
                    logger.debug(f"Failed to save chat completion request: {e}")

    except Exception as e:
        logger.error(f"Background stream processing error: {e}", exc_info=True)
