"""Streaming SSE generator for the chat route (extracted from chat.py).

Holds ``stream_generator`` and its wall-clock deadline constant. Re-imported into
``src/routes/chat.py`` so ``src.routes.chat.stream_generator`` keeps resolving
(tests and the handler reference it there). Behaviour is unchanged — this is a
verbatim move with the dependencies it needs imported from their source modules.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (used by the moved generator)
import logging
import time  # noqa: F401  (used by the moved generator)

from fastapi import Request  # noqa: F401

from src.config import Config  # noqa: F401
from src.db.chat_completion_requests_enhanced import (  # noqa: F401
    save_chat_completion_request_with_cost,
)
from src.db.plans import enforce_plan_limits  # noqa: F401
from src.handlers.post_processing import _process_stream_completion_background  # noqa: F401
from src.routes.chat_helpers import _to_thread  # noqa: F401
from src.services.prometheus_metrics import track_time_to_first_chunk  # noqa: F401
from src.services.stream_normalizer import (  # noqa: F401
    StreamNormalizer,
    create_done_sse,
    create_error_sse_chunk,
)

logger = logging.getLogger(__name__)


import os as _os

# FREEZE FIX: Hard wall-clock deadline for the entire streaming response.
# Without this, a provider that accepts the connection but sends data slowly (or not at all)
# holds the asyncio event loop indefinitely, making ALL endpoints (including /metrics and
# /health) unresponsive. Between-chunk check fires on the next received chunk; combined with
# the per-provider httpx read_timeout it covers the two main hang patterns:
#   1. Provider sends headers then goes completely silent (httpx read_timeout fires)
#   2. Provider trickles bytes slowly over many minutes (wall-clock deadline fires between chunks)
MAX_STREAM_DURATION = int(_os.getenv("MAX_STREAM_DURATION_SECONDS", "300"))


async def stream_generator(
    stream,
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    rate_limit_mgr=None,
    provider="openrouter",
    tracker=None,
    is_anonymous=False,
    is_async_stream=False,  # PERF: Flag to indicate if stream is async
    request_id=None,
    api_key_id=None,
    client_ip=None,
    request: Request | None = None,
):
    """Generate SSE stream from OpenAI stream response (OPTIMIZED: background post-processing)

    Args:
        is_async_stream: If True, stream is an async iterator and will be consumed with
                        `async for` instead of `for`. This prevents blocking the event
                        loop while waiting for chunks from slow AI providers.
    """
    accumulated_content = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    start_time = time.monotonic()
    # NOTE: Previously had dead code: `rate_limit_mgr is not None and not trial.get("is_trial", False)`
    # This expression evaluated but was never assigned or used - removed as no-op
    streaming_ctx = None
    first_chunk_sent = False  # TTFC tracking
    ttfc_start = time.monotonic()  # TTFC tracking
    chunk_count = 0  # Initialized before try so it's available in except for refund metadata
    dropped_chunks = 0  # Track chunks that failed normalization
    credit_deduction_success = False  # Track whether credits were actually deducted

    # Initialize normalizer
    normalizer = StreamNormalizer(provider=provider, model=model)

    try:
        # Track streaming duration if tracker is provided
        if tracker:
            streaming_ctx = tracker.streaming()
            streaming_ctx.__enter__()

        # PERF: Use async iteration for async streams to avoid blocking the event loop
        # This is critical for reducing perceived TTFC as it allows the server to handle
        # other requests while waiting for the AI provider to start streaming

        # Sentinel value to signal iterator exhaustion (PEP 479 compliance)
        # StopIteration cannot be raised into a Future, so we use a sentinel instead
        _STREAM_EXHAUSTED = object()

        def _safe_next(iterator):
            """Wrapper for next() that returns a sentinel instead of raising StopIteration.

            This is necessary because StopIteration cannot be raised into a Future
            (PEP 479), which causes issues when using asyncio.to_thread(next, iterator).
            """
            try:
                return next(iterator)
            except StopIteration:
                return _STREAM_EXHAUSTED

        async def iterate_stream():
            """Helper to support both sync and async iteration"""
            try:
                if is_async_stream:
                    async for chunk in stream:
                        yield chunk
                else:
                    # Use non-blocking iteration for sync streams to avoid blocking the event loop
                    iterator = iter(stream)
                    while True:
                        try:
                            # Run the blocking next() call in a thread using safe wrapper
                            # to avoid "StopIteration interacts badly with generators" error
                            chunk = await asyncio.to_thread(_safe_next, iterator)
                            if chunk is _STREAM_EXHAUSTED:
                                break
                            yield chunk
                        except Exception as e:
                            logger.error(f"Error during sync stream iteration: {e}")
                            raise e
            finally:
                # FREEZE FIX: Always release the underlying provider connection back to
                # the pool — whether the stream completed normally, was cancelled by the
                # watchdog, or raised an exception. Without this, timed-out or aborted
                # streams hold their httpx connection open indefinitely.
                try:
                    if is_async_stream and hasattr(stream, "aclose"):
                        await stream.aclose()
                    elif not is_async_stream and hasattr(stream, "close"):
                        stream.close()
                except Exception:
                    pass  # Never let cleanup block generator teardown

        # FREEZE FIX: Wall-clock deadline for the entire stream (between-chunk check).
        # Fires when the provider is slow but still sending chunks. The per-provider
        # httpx read_timeout covers the "provider sends headers then goes silent" case.
        _stream_deadline = time.monotonic() + MAX_STREAM_DURATION

        async for chunk in iterate_stream():
            # CRITICAL: Check for client disconnect to prevent zombie requests (499)
            if request and await request.is_disconnected():
                logger.warning(f"[StreamGenerator] Client disconnected (request_id={request_id})")
                break

            # FREEZE FIX: Wall-clock deadline — abort if stream exceeds MAX_STREAM_DURATION.
            # This fires between chunk arrivals; the httpx read_timeout covers intra-chunk hangs.
            if time.monotonic() > _stream_deadline:
                _elapsed_s = time.monotonic() - start_time
                logger.error(
                    f"[STREAM WATCHDOG] Stream exceeded {MAX_STREAM_DURATION}s wall-clock limit "
                    f"({_elapsed_s:.1f}s elapsed) for {provider}/{model}. Terminating."
                )
                yield create_error_sse_chunk(
                    error_message=(
                        f"Stream timeout: provider did not complete the response within "
                        f"{MAX_STREAM_DURATION}s. Please retry or contact support."
                    ),
                    error_type="stream_timeout",
                    provider=provider,
                    model=model,
                )
                yield create_done_sse()
                return

            chunk_count += 1

            # TTFC: Track time to first chunk for performance monitoring
            if not first_chunk_sent:
                ttfc = time.monotonic() - ttfc_start
                first_chunk_sent = True
                # Record TTFC metric
                track_time_to_first_chunk(provider=provider, model=model, ttfc=ttfc)
                # Log TTFC for debugging slow streams with enhanced context
                if ttfc > 2.0:
                    severity = "CRITICAL" if ttfc > 10.0 else "WARNING"
                    logger.warning(
                        f"⚠️ [TTFC {severity}] Slow first chunk: {ttfc:.2f}s for {provider}/{model} "
                        f"(threshold: 2.0s, timeout: {Config.GOOGLE_VERTEX_TIMEOUT if provider == 'google-vertex' else 'N/A'}s)"
                    )

                    # Sentry alerting for critical TTFC (>10s)
                    if ttfc > 10.0:
                        try:
                            import sentry_sdk

                            sentry_sdk.capture_message(
                                f"Critical TTFC: {ttfc:.2f}s for {provider}/{model}",
                                level="warning",
                                extras={
                                    "ttfc_seconds": ttfc,
                                    "provider": provider,
                                    "model": model,
                                    "threshold": 10.0,
                                    "severity": "CRITICAL",
                                    "timeout_config": (
                                        Config.GOOGLE_VERTEX_TIMEOUT
                                        if provider == "google-vertex"
                                        else None
                                    ),
                                },
                            )
                        except Exception as sentry_error:
                            logger.debug(f"Failed to send Sentry alert for TTFC: {sentry_error}")
                else:
                    logger.info(f"✓ [TTFC] First chunk in {ttfc:.2f}s for {provider}/{model}")

            logger.debug(f"[STREAM] Processing chunk {chunk_count} for model {model}")

            normalized_chunk = normalizer.normalize_chunk(chunk)

            # Check for usage in chunk (some providers send it in final chunk)
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
                total_tokens = chunk.usage.total_tokens

            if normalized_chunk:
                yield normalized_chunk.to_sse()
            else:
                # Only count as a real drop if it's not an Anthropic control event
                # (message_start, content_block_stop, ping, etc. legitimately produce no output)
                is_anthropic_noop = False
                if hasattr(chunk, "type") and chunk.type in {
                    "message_start",
                    "message_stop",
                    "message_delta",
                    "content_block_start",
                    "content_block_stop",
                    "ping",
                }:
                    is_anthropic_noop = True

                if not is_anthropic_noop:
                    dropped_chunks += 1
                    logger.warning(
                        "[STREAM_DROP] Chunk %d dropped for %s/%s (request_id=%s)",
                        chunk_count,
                        provider,
                        model,
                        request_id,
                    )
                    try:
                        from src.services.prometheus_metrics import stream_chunks_dropped

                        stream_chunks_dropped.labels(provider=provider, model=model).inc()
                    except ImportError:
                        pass

        accumulated_content = normalizer.get_accumulated_content()
        logger.info(
            "[STREAM] Stream completed: %d chunks, %d dropped, content_len=%d (request_id=%s)",
            chunk_count,
            dropped_chunks,
            len(accumulated_content),
            request_id,
        )

        # Warn client if significant chunk loss occurred
        # Note: chunk_count already includes dropped chunks (incremented for every chunk received)
        if dropped_chunks > 0 and chunk_count > 0 and dropped_chunks > chunk_count * 0.5:
            yield create_error_sse_chunk(
                error_message=f"Warning: {dropped_chunks} of {chunk_count} chunks could not be normalized from provider {provider}",
                error_type="stream_normalization_warning",
                provider=provider,
                model=model,
                request_id=request_id,
            )

        # DEFENSIVE: Detect empty streams and log as error
        if chunk_count == 0:
            logger.error(
                f"[EMPTY STREAM] Provider {provider} returned zero chunks for model {model}. "
                f"This indicates a provider routing or model ID transformation issue."
            )
            yield create_error_sse_chunk(
                error_message=f"Provider returned empty stream for model {model}. Please try again or contact support.",
                error_type="empty_stream_error",
                provider=provider,
                model=model,
                status=502,
                request_id=request_id,
            )
            yield create_done_sse()
            return
        elif accumulated_content == "" and chunk_count > 0:
            logger.warning(
                f"[EMPTY CONTENT] Provider {provider} returned {chunk_count} chunks but no content for model {model}."
            )

        # If no usage was provided, estimate based on content using improved tokenizer.
        # Some providers return prompt_tokens/completion_tokens but omit total_tokens;
        # in that case we should derive total_tokens rather than overwriting the
        # provider-supplied counts with estimates.
        if total_tokens == 0:
            if prompt_tokens > 0 or completion_tokens > 0:
                # Provider gave partial usage -- just fill in the total
                total_tokens = prompt_tokens + completion_tokens
                estimation_source = "provider_partial"
                logger.info(
                    f"[TOKEN_ESTIMATION] Provider '{provider}' returned partial "
                    f"usage for model '{model}' (prompt={prompt_tokens}, "
                    f"completion={completion_tokens}). "
                    f"Derived total_tokens={total_tokens}."
                )
            else:
                from src.utils.token_estimator import (
                    count_completion_tokens,
                    count_tokens_messages,
                    get_estimation_method,
                )

                estimation_source = get_estimation_method()
                completion_tokens = count_completion_tokens(accumulated_content)
                prompt_tokens = count_tokens_messages(messages)
                total_tokens = prompt_tokens + completion_tokens

                # Log warning with provider/model for identifying which
                # providers lack usage data
                logger.warning(
                    f"[TOKEN_ESTIMATION] Provider '{provider}' did not return "
                    f"usage data for model '{model}'. "
                    f"Estimated via {estimation_source}: "
                    f"prompt_tokens={prompt_tokens}, "
                    f"completion_tokens={completion_tokens}, "
                    f"total_tokens={total_tokens}. "
                    f"Accumulated content: {len(accumulated_content)} chars, "
                    f"{len(accumulated_content.split())} words. "
                    f"Billing is approximate until this provider reports usage."
                )

            # Track metrics for monitoring
            try:
                from src.services.prometheus_metrics import record_token_count_source

                record_token_count_source(
                    provider=provider,
                    model=model,
                    source=estimation_source,
                )
            except Exception:
                pass  # Never let metrics break the main flow
        else:
            # Provider returned usage data - record that fact and optionally
            # compute an estimate for calibration purposes.
            try:
                from src.services.prometheus_metrics import record_token_count_source

                record_token_count_source(provider=provider, model=model, source="provider")

                # When we have actual counts, also compute estimates so we can
                # measure estimation accuracy for future calibration.
                from src.utils.token_estimator import (
                    count_completion_tokens,
                    count_tokens_messages,
                    get_estimation_method,
                )

                estimation_method = get_estimation_method()
                est_prompt = count_tokens_messages(messages)
                est_completion = count_completion_tokens(accumulated_content)

                from src.services.prometheus_metrics import record_token_estimation_accuracy

                record_token_estimation_accuracy(
                    provider=provider,
                    estimation_method=estimation_method,
                    estimated_prompt=est_prompt,
                    estimated_completion=est_completion,
                    actual_prompt=prompt_tokens,
                    actual_completion=completion_tokens,
                )
            except Exception:
                pass  # Never let calibration metrics break the main flow

        elapsed = max(0.001, time.monotonic() - start_time)

        # OPTIMIZATION: Quick plan limit check (critical - must be synchronous)
        # Skip plan limit check for anonymous users (user is None)
        if not is_anonymous and user is not None:
            post_plan = await _to_thread(
                enforce_plan_limits, user["id"], total_tokens, environment_tag
            )
            if not post_plan.get("allowed", False):
                yield create_error_sse_chunk(
                    error_message=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}",
                    error_type="plan_limit_exceeded",
                    status=429,
                    request_id=request_id,
                )
                yield create_done_sse()
                return

        # OPTIMIZATION: Send [DONE] immediately, process credits/logging in background!
        # This makes the stream complete 100-200ms faster for the client
        yield create_done_sse()

        # Schedule background processing (non-blocking)
        asyncio.create_task(
            _process_stream_completion_background(
                user=user,
                api_key=api_key,
                model=model,
                trial=trial,
                environment_tag=environment_tag,
                session_id=session_id,
                messages=messages,
                accumulated_content=accumulated_content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed=elapsed,
                provider=provider,
                is_anonymous=is_anonymous,
                request_id=request_id,
                client_ip=client_ip,
                api_key_id=api_key_id,
            )
        )

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)

        # Extract meaningful error message for the client
        error_str = str(e).lower()
        error_message = "Streaming error occurred"
        error_type = "stream_error"

        # Check for rate limit errors
        if "rate limit" in error_str or "429" in error_str or "too many" in error_str:
            error_message = "Rate limit exceeded. Please wait a moment and try again."
            error_type = "rate_limit_error"
        # Check for authentication errors
        elif "401" in error_str or "unauthorized" in error_str or "authentication" in error_str:
            error_message = "Authentication failed. Please check your API key or sign in again."
            error_type = "auth_error"
        # Check for provider/upstream errors
        elif (
            "upstream" in error_str
            or "provider" in error_str
            or "503" in error_str
            or "502" in error_str
        ):
            error_message = f"Provider temporarily unavailable: {str(e)[:200]}"
            error_type = "provider_error"
        # Check for timeout errors
        elif "timeout" in error_str or "timed out" in error_str:
            error_message = "Request timed out. The model may be overloaded. Please try again."
            error_type = "timeout_error"
        # Check for model not found errors
        elif "not found" in error_str or "404" in error_str:
            error_message = f"Model or resource not found: {str(e)[:200]}"
            error_type = "not_found_error"
        # For other errors, include a sanitized version of the error message
        else:
            # Include the actual error message but truncate it for safety
            sanitized_msg = str(e)[:300].replace("\n", " ").replace("\r", " ")
            error_message = f"Streaming error: {sanitized_msg}"

        # Auto-refund for clear provider failures if credits were already deducted.
        # In the current streaming architecture, credits are deducted in the background
        # task AFTER the stream completes, so this is primarily a defensive measure for
        # edge cases and future code paths. Only refund for obvious provider-side failures
        # (5xx, timeout), NEVER for user errors (4xx, auth, rate limit).
        # Guard: only attempt refund if credits were actually deducted successfully.
        if (
            credit_deduction_success
            and not is_anonymous
            and user
            and error_type in ("provider_error", "timeout_error")
            and total_tokens > 0
        ):
            try:
                from src.services.credit_handler import refund_credits
                from src.services.pricing import calculate_cost_async

                estimated_cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)
                if estimated_cost > 0:
                    refund_success = await refund_credits(
                        user_id=user["id"],
                        api_key=api_key,
                        amount=estimated_cost,
                        reason=error_type,
                        original_request_id=request_id,
                        metadata={
                            "model": model,
                            "provider": provider,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "error_type": error_type,
                            "error_message": str(e)[:200],
                            "stream_chunks_received": chunk_count,
                        },
                    )
                    if refund_success:
                        logger.info(
                            f"Auto-refunded ${estimated_cost:.6f} to user {user.get('id')} "
                            f"for failed streaming request (reason: {error_type})"
                        )
                    else:
                        logger.warning(
                            f"Auto-refund of ${estimated_cost:.6f} failed for user {user.get('id')} "
                            f"(reason: {error_type}). Manual review needed."
                        )
            except Exception as refund_err:
                logger.error(
                    f"Error during auto-refund attempt for user {user.get('id')}: {refund_err}",
                    exc_info=True,
                )

        # Save failed request to database
        if request_id:
            try:
                # Calculate elapsed time from stream start
                error_elapsed = time.monotonic() - start_time

                # Save failed streaming request with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=model,
                    input_tokens=prompt_tokens,  # Use tokens accumulated so far
                    output_tokens=completion_tokens,  # May be partial
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"{error_type}: {error_message}",
                    user_id=user["id"] if user else None,
                    provider_name=provider,
                    model_id=None,
                    api_key_id=api_key_id,
                    is_anonymous=is_anonymous,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed streaming request: {save_err}")

        yield create_error_sse_chunk(
            error_message=error_message,
            error_type=error_type,
            provider=provider if "provider" in dir() else None,
            model=model if "model" in dir() else None,
            request_id=request_id if "request_id" in dir() else None,
        )
        yield create_done_sse()
    finally:
        # Record streaming duration
        if streaming_ctx:
            streaming_ctx.__exit__(None, None, None)
        # Record performance percentages if tracker is provided
        if tracker:
            tracker.record_percentages()
