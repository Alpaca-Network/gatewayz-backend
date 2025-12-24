import asyncio
import json
import logging
import secrets
import time
import uuid
from contextvars import ContextVar
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.utils.performance_tracker import PerformanceTracker
import importlib

import src.db.activity as activity_module
import src.db.api_keys as api_keys_module
import src.db.chat_history as chat_history_module
import src.db.plans as plans_module
import src.db.rate_limits as rate_limits_module
import src.db.users as users_module

# Direct imports (replaces wrapper functions)
from src.db.api_keys import increment_api_key_usage
from src.db.plans import enforce_plan_limits
from src.db.rate_limits import create_rate_limit_alert, update_rate_limit_usage
from src.db.users import get_user, deduct_credits, log_api_usage_transaction, record_usage
from src.db.chat_history import create_chat_session, save_chat_message, get_chat_session
from src.db.activity import log_activity, get_provider_from_model
from src.db.chat_completion_requests import save_chat_completion_request
from src.config import Config
from src.schemas import ProxyRequest, ResponseRequest
from src.security.deps import get_api_key, get_optional_api_key
from src.services.passive_health_monitor import capture_model_health
from src.utils.rate_limit_headers import get_rate_limit_headers
from src.services.prometheus_metrics import (
    model_inference_requests,
    model_inference_duration,
    tokens_used,
    credits_used,
    track_time_to_first_chunk,
)
from src.services.redis_metrics import get_redis_metrics
from src.services.stream_normalizer import (
    StreamNormalizer,
    create_error_sse_chunk,
    create_done_sse,
)
from src.utils.sentry_context import capture_payment_error, capture_provider_error

# Request correlation ID for distributed tracing
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
# Make braintrust optional for test environments
try:
    from braintrust import current_span, start_span, traced

    BRAINTRUST_AVAILABLE = True
except ImportError:
    BRAINTRUST_AVAILABLE = False

    # Create no-op decorators and functions when braintrust is not available
    def traced(name=None, type=None):
        def decorator(func):
            return func

        return decorator

    class MockSpan:
        def log(self, *args, **kwargs):
            pass

        def end(self):
            pass

    def start_span(name=None, type=None):
        return MockSpan()

    def current_span():
        return MockSpan()



# ============================================================================
# Provider Registry System
# ============================================================================
# All provider imports and registrations are now handled by the provider registry.
# This replaces 283 lines of repetitive import code with a clean, configuration-driven approach.
#
# To add a new provider:
# 1. Create the provider client in src/services/{provider}_client.py
# 2. Add configuration to src/config/providers.py
# 3. That's it! The provider will be auto-loaded.
#
# See docs/PROVIDER_REGISTRY_REFACTORING.md for details.
# ============================================================================

from src.services.provider_loader import load_all_providers
from src.services.provider_registry import get_provider_registry
from src.config.providers import PROVIDER_CONFIGS, DEFAULT_PROVIDER, AUTO_DETECT_PROVIDERS, normalize_provider_name

# Load all providers at module initialization
# This replaces all the _safe_import_provider calls and manual registrations
load_all_providers()

# Shared helper functions for chat endpoints (eliminates ~320 lines of duplication)
from src.routes.helpers.chat import (
    validate_user_and_auth,
    validate_trial,
    check_plan_limits,
    ensure_capacity,
    check_rate_limits,
    handle_billing,
    get_rate_limit_headers as get_rl_headers,
    validate_session_id,
    inject_chat_history,
    build_optional_params,
    transform_input_to_messages,
    validate_and_adjust_max_tokens,
    POSTGRES_INT_MIN,
    POSTGRES_INT_MAX,
)

# Stream processing helpers (extracted from stream_generator to reduce complexity)
from src.services.stream_helpers import (
    estimate_tokens_from_content,
    calculate_stream_timing_data,
    map_streaming_error_message,
)

import src.services.rate_limiting as rate_limiting_service
import src.services.trial_validation as trial_module
from src.services.rate_limiting import get_rate_limit_manager
from src.services.trial_validation import validate_trial_access, track_trial_usage
from src.services.model_transformations import detect_provider_from_model_id, transform_model_id
from src.services.pricing import calculate_cost
from src.services.provider_failover import (
    build_provider_failover_chain,
    enforce_model_failover_rules,
    filter_by_circuit_breaker,
    map_provider_error,
    should_failover,
)
from src.utils.security_validators import sanitize_for_logging
from src.utils.token_estimator import estimate_message_tokens
from src.utils.logging_utils import mask_key


# ============================================================================
# Helper Functions
# ============================================================================

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize provider import errors tracking (used at end of module)
_provider_import_errors = {}

# Log module initialization to help debug route loading
logger.info("🔄 Chat module initialized - router created")
logger.info(f"   Router type: {type(router)}")

# Provider timeouts are now configured in src/config/providers.py
# Access via: get_provider_registry().get_timeout(provider_name)


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def _ensure_plan_capacity(user_id: int, environment_tag: str) -> dict[str, Any]:
    """Run a lightweight plan-limit precheck before making upstream calls."""
    plan_check = await _to_thread(enforce_plan_limits, user_id, 0, environment_tag)
    if not plan_check.get("allowed", False):
        raise HTTPException(
            status_code=429,
            detail=f"Plan limit exceeded: {plan_check.get('reason', 'unknown')}",
        )
    return plan_check


def _fallback_get_user(api_key: str):
    try:
        supabase_module = importlib.import_module("src.config.supabase_config")
        client = supabase_module.get_supabase_client()
        result = client.table("users").select("*").eq("api_key", api_key).execute()
        if result.data:
            logging.getLogger(__name__).debug("Fallback user lookup succeeded for %s", api_key)
            return result.data[0]
        logging.getLogger(__name__).debug(
            "Fallback lookup found no data; table snapshot=%s",
            client.table("users").select("*").execute().data,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Fallback user lookup error for %s: %s", mask_key(api_key), exc
        )
        return None
    return None


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
        # Record Prometheus metrics
        status = "success" if success else "error"

        # Request count
        model_inference_requests.labels(
            provider=provider,
            model=model,
            status=status
        ).inc()

        # Duration
        model_inference_duration.labels(
            provider=provider,
            model=model
        ).observe(elapsed_seconds)

        # Token usage
        if prompt_tokens > 0:
            tokens_used.labels(
                provider=provider,
                model=model,
                token_type="input"
            ).inc(prompt_tokens)

        if completion_tokens > 0:
            tokens_used.labels(
                provider=provider,
                model=model,
                token_type="output"
            ).inc(completion_tokens)

        # Credits consumed
        if cost > 0:
            credits_used.labels(
                provider=provider,
                model=model
            ).inc(cost)

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
            error_message=error_message
        )

        # Passive health monitoring (background task)
        response_time_ms = int(elapsed_seconds * 1000)
        health_status = "success" if success else "error"

        # Create usage dict for health monitoring
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens
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
                usage=usage
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
):
    """
    Background task for post-stream processing (100-200ms faster [DONE] event!)

    This runs asynchronously after the stream completes, allowing the [DONE]
    event to be sent immediately without waiting for database operations.

    For anonymous users, only metrics recording is performed (no credits, usage tracking, or history).
    """
    try:
        # Skip user-specific operations for anonymous requests
        if is_anonymous:
            logger.info("Skipping user-specific post-processing for anonymous request")
            # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
            cost = calculate_cost(model, prompt_tokens, completion_tokens)
            await _record_inference_metrics_and_health(
                provider=provider,
                model=model,
                elapsed_seconds=elapsed,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                success=True,
                error_message=None
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

        # Track trial usage
        if trial.get("is_trial") and not trial.get("is_expired"):
            try:
                await _to_thread(track_trial_usage, api_key, total_tokens, 1)
            except Exception as e:
                logger.warning("Failed to track trial usage: %s", e)

        cost = calculate_cost(model, prompt_tokens, completion_tokens)
        is_trial = trial.get("is_trial", False)

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
                    },
                    True,
                )
            except Exception as e:
                logger.error(f"Failed to log trial API usage transaction: {e}", exc_info=True)
                # Capture to Sentry - trial tracking failure
                capture_payment_error(
                    e,
                    operation="trial_usage_logging",
                    user_id=user.get("id"),
                    details={"model": model, "tokens": total_tokens, "is_trial": True}
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
                    },
                )
                await _to_thread(
                    record_usage,
                    user["id"],
                    api_key,
                    model,
                    total_tokens,
                    cost,
                    int(elapsed * 1000),
                )
                await _to_thread(update_rate_limit_usage, api_key, total_tokens)
            except Exception as e:
                logger.error("Usage recording error in background: %s", e)
                # Capture to Sentry - CRITICAL revenue loss if credits not deducted!
                capture_payment_error(
                    e,
                    operation="credit_deduction",
                    user_id=user.get("id"),
                    amount=cost,
                    details={
                        "model": model,
                        "tokens": total_tokens,
                        "cost_usd": cost,
                        "api_key": api_key[:10] + "..." if api_key else None
                    }
                )

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
            error_message=None
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
                f"Failed to log activity for user {user['id']}, model {model}: {e}", exc_info=True
            )

        # Save chat history
        # Validate session_id before attempting to save
        session_id = validate_session_id(session_id)

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

        # Save chat completion request to database
        if request_id:
            try:
                user_id_str = str(user.get("id")) if user and user.get("id") else None
                await _to_thread(
                    save_chat_completion_request,
                    request_id=request_id,
                    model_name=model,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    processing_time_ms=int(elapsed * 1000),
                    status="completed",
                    error_message=None,
                    user_id=user_id_str,
                    provider_name=provider,
                )
            except Exception as e:
                logger.debug(f"Failed to save chat completion request: {e}")

    except Exception as e:
        logger.error(f"Background stream processing error: {e}", exc_info=True)


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
    request_received_at=None,  # Wall-clock timestamp when request was received
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
    rate_limit_mgr is not None and not trial.get("is_trial", False)
    streaming_ctx = None
    first_chunk_sent = False  # TTFC tracking
    ttfc_start = time.monotonic()  # TTFC tracking
    ttfc_seconds = None  # Store TTFC for response timing data

    # Initialize normalizer
    normalizer = StreamNormalizer(provider=provider, model=model)

    try:
        # Track streaming duration if tracker is provided
        if tracker:
            streaming_ctx = tracker.streaming()
            streaming_ctx.__enter__()

        chunk_count = 0

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

        async for chunk in iterate_stream():
            chunk_count += 1

            # TTFC: Track time to first chunk for performance monitoring
            if not first_chunk_sent:
                ttfc = time.monotonic() - ttfc_start
                ttfc_seconds = ttfc  # Save for timing data response
                first_chunk_sent = True
                # Record TTFC metric
                track_time_to_first_chunk(provider=provider, model=model, ttfc=ttfc)
                # Log TTFC for debugging slow streams
                if ttfc > 2.0:
                    logger.warning(
                        f"[TTFC] Slow first chunk: {ttfc:.2f}s for {provider}/{model} (threshold: 2.0s)"
                    )
                else:
                    logger.info(f"[TTFC] First chunk in {ttfc:.2f}s for {provider}/{model}")

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
                logger.debug(f"[STREAM] Chunk {chunk_count} resulted in no normalized output")

        accumulated_content = normalizer.get_accumulated_content()
        logger.info(
            f"[STREAM] Stream completed with {chunk_count} chunks, accumulated content length: {len(accumulated_content)}"
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
                model=model
            )
            yield create_done_sse()
            return
        elif accumulated_content == "" and chunk_count > 0:
            logger.warning(
                f"[EMPTY CONTENT] Provider {provider} returned {chunk_count} chunks but no content for model {model}."
            )

        # If no usage was provided, estimate based on content
        if total_tokens == 0:
            prompt_tokens, completion_tokens, total_tokens = estimate_tokens_from_content(
                accumulated_content, messages
            )

        elapsed = max(0.001, time.monotonic() - start_time)

        # OPTIMIZATION: Quick plan limit check (critical - must be synchronous)
        # Skip plan limit check for anonymous users (user is None)
        if not is_anonymous and user is not None:
            post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
            if not post_plan.get("allowed", False):
                yield create_error_sse_chunk(
                    error_message=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}",
                    error_type="plan_limit_exceeded"
                )
                yield create_done_sse()
                return

        # Calculate timing metrics for client and send before [DONE]
        timing_data = calculate_stream_timing_data(
            ttfc_seconds, elapsed, completion_tokens, prompt_tokens, total_tokens, request_received_at
        )
        yield f"data: {json.dumps(timing_data)}\n\n"

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
                request_id=request_id_var.get(),
            )
        )

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)

        # Map exception to user-friendly error message
        error_message, error_type = map_streaming_error_message(e)

        yield create_error_sse_chunk(
            error_message=error_message,
            error_type=error_type,
            provider=provider if 'provider' in dir() else None,
            model=model if 'model' in dir() else None
        )
        yield create_done_sse()
    finally:
        # Record streaming duration
        if streaming_ctx:
            streaming_ctx.__exit__(None, None, None)
        # Record performance percentages if tracker is provided
        if tracker:
            tracker.record_percentages()


# Log route registration for debugging
logger.info("📍 Registering /chat/completions endpoint")


@router.post("/chat/completions", tags=["chat"])
@traced(name="chat_completions", type="llm")
async def chat_completions(
    req: ProxyRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Depends(get_optional_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    # === 0) Setup / sanity ===
    # Capture request arrival timestamp for client latency calculation
    request_received_at = time.time()

    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    # Never print keys; log masked
    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            api_key = auth_header.split(" ", 1)[1].strip()

    # Determine if this is an authenticated or anonymous request
    is_anonymous = api_key is None

    logger.info(
        "chat_completions start (request_id=%s, api_key=%s, model=%s, anonymous=%s)",
        request_id,
        mask_key(api_key) if api_key else "anonymous",
        req.model,
        is_anonymous,
        extra={"request_id": request_id},
    )

    # Start Braintrust span for this request
    span = start_span(name=f"chat_{req.model}", type="llm")

    # Initialize performance tracker
    tracker = PerformanceTracker(endpoint="/v1/chat/completions")

    try:
        # === 1) User + plan/trial prechecks (REFACTORED: using shared helpers) ===
        with tracker.stage("auth_validation"):
            # Validate user and determine if anonymous
            user, is_anonymous = await validate_user_and_auth(api_key, _to_thread, request_id)

            if is_anonymous:
                # Anonymous user - skip trial validation and plan limits
                trial = {"is_valid": True, "is_trial": False}
                environment_tag = "live"
            else:
                # Authenticated user - get environment and validate trial
                environment_tag = user.get("environment_tag", "live")
                trial = await validate_trial(api_key, _to_thread)

        rate_limit_mgr = get_rate_limit_manager()
        should_release_concurrency = not trial.get("is_trial", False) and not is_anonymous

        # Allow disabling rate limiting for testing (DEV ONLY)
        import os
        disable_rate_limiting = os.getenv("DISABLE_RATE_LIMITING", "false").lower() == "true"

        # Initialize rate limit variables
        rl_pre = None
        rl_final = None

        # Rate limiting (only for authenticated non-trial users)
        if not is_anonymous and not disable_rate_limiting:
            rl_pre = await check_rate_limits(
                api_key,
                tokens_used=0,
                is_trial=trial.get("is_trial", False),
                _to_thread=_to_thread
            )

        # Credit check (only for authenticated non-trial users)
        if not is_anonymous and not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise HTTPException(status_code=402, detail="Insufficient credits")

        # === 2) Build upstream request ===
        with tracker.stage("request_parsing"):
            messages = [m.model_dump() for m in req.messages]

        # === 2.1) Inject conversation history if session_id provided ===
        # Chat history is only available for authenticated users
        if not is_anonymous:
            session_id = validate_session_id(session_id)
            messages = await inject_chat_history(session_id, user["id"], messages, _to_thread, get_chat_session)
        elif session_id and is_anonymous:
            logger.debug("Ignoring session_id for anonymous request")

        # === 2.2) Plan limit pre-check with estimated tokens (only for authenticated users) ===
        # This is the ONLY pre-request plan check - replaces the previous 3 duplicate checks!
        if not is_anonymous:
            estimated_tokens = estimate_message_tokens(messages, getattr(req, "max_tokens", None))
            await check_plan_limits(user["id"], environment_tag, estimated_tokens, _to_thread)

        # Store original model for response
        original_model = req.model

        with tracker.stage("request_preparation"):
            param_names = ("max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty", "tools")
            optional = build_optional_params(req, param_names)

            # Validate and adjust max_tokens for models with minimum requirements
            validate_and_adjust_max_tokens(optional, original_model)

            # Auto-detect provider if not specified
            req_provider_missing = req.provider is None or (
                isinstance(req.provider, str) and not req.provider
            )
            provider = normalize_provider_name((req.provider or "openrouter").lower())
            provider_locked = not req_provider_missing

            override_provider = detect_provider_from_model_id(original_model)
            if override_provider:
                override_provider = normalize_provider_name(override_provider.lower())
                if provider_locked and override_provider != provider:
                    logger.info(
                        "Skipping provider override for model %s: request locked provider to '%s'",
                        sanitize_for_logging(original_model),
                        sanitize_for_logging(provider),
                    )
                else:
                    if override_provider != provider:
                        logger.info(
                            f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                        )
                        provider = override_provider
                    # Mark provider as determined even if it matches the default
                    # This prevents the fallback logic from incorrectly routing to wrong providers
                    req_provider_missing = False

            if req_provider_missing:
                # Try to detect provider from model ID using the transformation module
                detected_provider = detect_provider_from_model_id(original_model)
                if detected_provider:
                    provider = normalize_provider_name(detected_provider)
                    logger.info(
                        "Auto-detected provider '%s' for model %s",
                        sanitize_for_logging(provider),
                        sanitize_for_logging(original_model),
                    )
                else:
                    # Fallback to checking cached models
                    from src.services.models import get_cached_models

                    # Try each provider with transformation (using configured auto-detect list)
                    for test_provider in AUTO_DETECT_PROVIDERS:
                        transformed = transform_model_id(original_model, test_provider)
                        provider_models = get_cached_models(test_provider) or []
                        if any(m.get("id") == transformed for m in provider_models):
                            provider = test_provider
                            logger.info(
                                f"Auto-detected provider '{provider}' for model {original_model} (transformed to {transformed})"
                            )
                            break
                    # Otherwise default to DEFAULT_PROVIDER (configured provider)

            provider_chain = build_provider_failover_chain(provider)
            provider_chain = enforce_model_failover_rules(original_model, provider_chain)
            provider_chain = filter_by_circuit_breaker(original_model, provider_chain)
            model = original_model

        # Diagnostic logging for tools parameter
        if "tools" in optional:
            logger.info(
                "Tools parameter detected: tools_count=%d, provider=%s, model=%s",
                len(optional["tools"]) if isinstance(optional["tools"], list) else 0,
                sanitize_for_logging(provider),
                sanitize_for_logging(original_model),
            )
            logger.debug("Tools content: %s", sanitize_for_logging(str(optional["tools"])[:500]))

        # === 3) Call upstream (streaming or non-streaming) ===
        if req.stream:
            last_http_exc = None
            for idx, attempt_provider in enumerate(provider_chain):
                attempt_model = transform_model_id(original_model, attempt_provider)
                if attempt_model != original_model:
                    logger.info(
                        f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                    )

                request_model = attempt_model
                is_async_stream = False  # Default to sync, only OpenRouter uses async currently
                try:
                    # ============================================================================
                    # Provider Registry - Streaming Requests
                    # Replaces 150 lines of if/elif chains with registry lookup
                    # ============================================================================
                    provider_config = get_provider_registry().get(attempt_provider)
                    if not provider_config:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Unknown provider: {attempt_provider}. Available: {', '.join(get_provider_registry().list_providers())}"
                        )

                    # PERF: Use async streaming when available to prevent event loop blocking
                    # while waiting for the AI provider to return the first chunk
                    if provider_config.supports_async_streaming and provider_config.make_request_stream_async:
                        try:
                            stream = await provider_config.make_request_stream_async(
                                messages,
                                request_model,
                                **optional,
                            )
                            is_async_stream = True
                            logger.debug(f"Using async streaming for {attempt_provider} model {request_model}")
                        except Exception as async_err:
                            # Fallback to sync streaming if async fails
                            logger.warning(f"Async streaming failed for {attempt_provider}, falling back to sync: {async_err}")
                            stream = await _to_thread(
                                provider_config.make_request_stream,
                                messages,
                                request_model,
                                **optional,
                            )
                            is_async_stream = False
                    else:
                        # Use standard sync streaming
                        stream = await _to_thread(
                            provider_config.make_request_stream,
                            messages,
                            request_model,
                            **optional,
                        )

                    provider = attempt_provider
                    model = request_model
                    # Get rate limit headers if available (pre-stream check)
                    stream_headers = {}
                    if rl_pre is not None:
                        stream_headers.update(get_rate_limit_headers(rl_pre))

                    # PERF: Add timing headers for debugging stream startup latency
                    if tracker:
                        prep_time_ms = tracker.get_total_duration() * 1000
                        stream_headers["X-Prep-Time-Ms"] = f"{prep_time_ms:.1f}"
                    stream_headers["X-Provider"] = provider
                    stream_headers["X-Model"] = model
                    stream_headers["X-Requested-Model"] = original_model

                    # SSE streaming headers to prevent buffering by proxies/nginx
                    stream_headers["X-Accel-Buffering"] = "no"
                    stream_headers["Cache-Control"] = "no-cache, no-transform"
                    stream_headers["Connection"] = "keep-alive"

                    return StreamingResponse(
                        stream_generator(
                            stream,
                            user,
                            api_key,
                            model,
                            trial,
                            environment_tag,
                            session_id,
                            messages,
                            rate_limit_mgr,
                            provider,
                            tracker,
                            is_anonymous,
                            is_async_stream=is_async_stream,
                            request_received_at=request_received_at,
                        ),
                        media_type="text/event-stream",
                        headers=stream_headers,
                    )
                except Exception as exc:
                    if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
                        logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                        # Capture timeout to Sentry
                        capture_provider_error(
                            exc,
                            provider=attempt_provider,
                            model=request_model,
                            endpoint="/v1/chat/completions",
                            request_id=request_id_var.get()
                        )
                    elif isinstance(exc, httpx.RequestError):
                        logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                        # Capture network error to Sentry
                        capture_provider_error(
                            exc,
                            provider=attempt_provider,
                            model=request_model,
                            endpoint="/v1/chat/completions",
                            request_id=request_id_var.get()
                        )
                    elif isinstance(exc, httpx.HTTPStatusError):
                        logger.debug(
                            "Upstream HTTP error (%s): %s",
                            attempt_provider,
                            exc.response.status_code,
                        )
                        # Capture HTTP errors to Sentry (except 4xx client errors)
                        if exc.response.status_code >= 500:
                            capture_provider_error(
                                exc,
                                provider=attempt_provider,
                                model=request_model,
                                endpoint="/v1/chat/completions",
                                request_id=request_id_var.get()
                            )
                    else:
                        logger.error("Unexpected upstream error (%s): %s", attempt_provider, exc)
                        # Capture unexpected errors to Sentry
                        capture_provider_error(
                            exc,
                            provider=attempt_provider,
                            model=request_model,
                            endpoint="/v1/chat/completions",
                            request_id=request_id_var.get()
                        )
                    http_exc = map_provider_error(attempt_provider, request_model, exc)

                    last_http_exc = http_exc
                    if idx < len(provider_chain) - 1 and should_failover(http_exc):
                        next_provider = provider_chain[idx + 1]
                        logger.warning(
                            "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                            attempt_provider,
                            http_exc.status_code,
                            http_exc.detail,
                            next_provider,
                        )
                        continue

                    raise http_exc

            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        # Non-streaming response
        start = time.monotonic()
        processed = None
        last_http_exc = None

        for idx, attempt_provider in enumerate(provider_chain):
            attempt_model = transform_model_id(original_model, attempt_provider)
            if attempt_model != original_model:
                logger.info(
                    f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                )

            request_model = attempt_model
            # Get provider timeout from registry
            request_timeout = get_provider_registry().get_timeout(attempt_provider)
            if request_timeout != 30:  # 30 is the default timeout
                logger.debug(
                    "Using extended timeout %ss for provider %s", request_timeout, attempt_provider
                )

            try:
                # ============================================================================
                # Provider Registry - Non-streaming Requests
                # Replaces 200 lines of if/elif chains with registry lookup
                # ============================================================================
                provider_config = get_provider_registry().get(attempt_provider)
                if not provider_config:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown provider: {attempt_provider}. Available: {', '.join(get_provider_registry().list_providers())}"
                    )

                # Make request with provider-specific timeout
                resp_raw = await asyncio.wait_for(
                    _to_thread(
                        provider_config.make_request,
                        messages,
                        request_model,
                        **optional
                    ),
                    timeout=request_timeout,
                )
                # Process response
                processed = await _to_thread(provider_config.process_response, resp_raw)

                provider = attempt_provider
                model = request_model
                break
            except Exception as exc:
                if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
                    logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.RequestError):
                    logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.HTTPStatusError):
                    logger.debug(
                        "Upstream HTTP error (%s): %s", attempt_provider, exc.response.status_code
                    )
                else:
                    logger.error("Unexpected upstream error (%s): %s", attempt_provider, exc)
                http_exc = map_provider_error(attempt_provider, request_model, exc)

                last_http_exc = http_exc
                if idx < len(provider_chain) - 1 and should_failover(http_exc):
                    next_provider = provider_chain[idx + 1]
                    logger.warning(
                        "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                        attempt_provider,
                        http_exc.status_code,
                        http_exc.detail,
                        next_provider,
                    )
                    continue

                raise http_exc

        if processed is None:
            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Plan limits and usage tracking (only for authenticated users)
        if not is_anonymous:
            post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
            if not post_plan.get("allowed", False):
                raise HTTPException(
                    status_code=429, detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}"
                )

            if trial.get("is_trial") and not trial.get("is_expired"):
                try:
                    await _to_thread(track_trial_usage, api_key, total_tokens, 1)
                except Exception as e:
                    logger.warning("Failed to track trial usage: %s", e)

            if should_release_concurrency and rate_limit_mgr and not disable_rate_limiting:
                try:
                    await rate_limit_mgr.release_concurrency(api_key)
                except Exception as exc:
                    logger.debug(
                        "Failed to release concurrency before final check for %s: %s",
                        mask_key(api_key),
                        exc,
                    )
                rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
                if not rl_final.allowed:
                    await _to_thread(
                        create_rate_limit_alert,
                        api_key,
                        "rate_limit_exceeded",
                        {
                            "reason": rl_final.reason,
                            "retry_after": rl_final.retry_after,
                            "remaining_requests": rl_final.remaining_requests,
                            "remaining_tokens": rl_final.remaining_tokens,
                            "tokens_requested": total_tokens,
                        },
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded: {rl_final.reason}",
                        headers=(
                            {"Retry-After": str(rl_final.retry_after)} if rl_final.retry_after else None
                        ),
                    )

        is_trial = trial.get("is_trial", False)

        # Credit/usage tracking (only for authenticated users)
        if not is_anonymous:
            cost = await handle_billing(
                api_key=api_key,
                user_id=user["id"],
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed_ms=int(elapsed * 1000),
                is_trial=is_trial,
                _to_thread=_to_thread,
            )
        else:
            # For anonymous users, still calculate cost for metrics
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

        # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
        await _record_inference_metrics_and_health(
            provider=provider,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            success=True,
            error_message=None
        )

        # === 4.5) Log activity for tracking and analytics (only for authenticated users) ===
        if not is_anonymous:
            try:
                provider_name = get_provider_from_model(model)
                speed = total_tokens / elapsed if elapsed > 0 else 0
                await _to_thread(
                    log_activity,
                    user_id=user["id"],
                    model=model,
                    provider=provider_name,
                    tokens=total_tokens,
                    cost=cost if not trial.get("is_trial", False) else 0.0,
                    speed=speed,
                    finish_reason=(processed.get("choices") or [{}])[0].get("finish_reason", "stop"),
                    app="API",
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "endpoint": "/v1/chat/completions",
                        "session_id": session_id,
                        "gateway": provider,  # Track which gateway was used
                    },
                )
            except Exception as e:
                logger.error(
                    f"Failed to log activity for user {user['id']}, model {model}: {e}", exc_info=True
                )

        # === 5) History (use the last user message in this request only) ===
        # Chat history is only saved for authenticated users
        # Validate session_id before attempting to save
        if not is_anonymous:
            session_id = validate_session_id(session_id)

        if session_id and not is_anonymous:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session:
                    # save last user turn in this call
                    last_user = None
                    for m in reversed(messages):
                        if m.get("role") == "user":
                            last_user = m
                            break
                    if last_user:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "user",
                            last_user.get("content", ""),
                            model,
                            0,
                            user["id"],
                        )

                    # Safely extract assistant content (handle None values in choices)
                    choices = processed.get("choices") or [{}]
                    first_choice = choices[0] if choices else {}
                    message = first_choice.get("message") or {}
                    assistant_content = message.get("content", "")
                    if assistant_content:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "assistant",
                            assistant_content,
                            model,
                            total_tokens,
                            user["id"],
                        )
                else:
                    logger.warning("Session %s not found for user %s", session_id, user["id"])
            except Exception as e:
                logger.error(
                    f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                    exc_info=True,
                )

        # === 6) Attach gateway usage (non-sensitive) ===
        processed.setdefault("gateway_usage", {})
        processed["gateway_usage"].update(
            {
                "tokens_charged": total_tokens,
                "backend_processing_ms": int(elapsed * 1000),
                "backend_received_at": int(request_received_at * 1000),  # Unix timestamp in ms
                "backend_responded_at": int((request_received_at + elapsed) * 1000),  # Unix timestamp in ms
                # Legacy field for backwards compatibility
                "request_ms": int(elapsed * 1000),
            }
        )
        if not trial.get("is_trial", False):
            # If you can cheaply re-fetch balance, do it here; otherwise omit
            processed["gateway_usage"]["cost_usd"] = round(cost, 6)

        # === 7) Log to Braintrust ===
        try:
            messages_for_log = [
                m.model_dump() if hasattr(m, "model_dump") else m for m in req.messages
            ]
            # Safely extract output content for Braintrust logging
            bt_choices = processed.get("choices") or [{}]
            bt_first_choice = bt_choices[0] if bt_choices else {}
            bt_message = bt_first_choice.get("message") or {}
            bt_output = bt_message.get("content", "")
            span.log(
                input=messages_for_log,
                output=bt_output,
                metrics={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": int(elapsed * 1000),
                    "cost_usd": cost if not trial.get("is_trial", False) else 0.0,
                },
                metadata={
                    "model": model,
                    "provider": provider,
                    "user_id": user["id"],
                    "session_id": session_id,
                    "is_trial": trial.get("is_trial", False),
                    "environment": user.get("environment_tag", "live"),
                },
            )
            span.end()
        except Exception as e:
            logger.warning(f"Failed to log to Braintrust: {e}")

        # Capture health metrics (passive monitoring) - run as background task
        background_tasks.add_task(
            capture_model_health,
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

        # Save chat completion request to database - run as background task
        if not is_anonymous:
            user_id_str = str(user.get("id")) if user and user.get("id") else None
            background_tasks.add_task(
                save_chat_completion_request,
                request_id=request_id,
                model_name=model,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                processing_time_ms=int(elapsed * 1000),
                status="completed",
                error_message=None,
                user_id=user_id_str,
                provider_name=provider,
            )

        # Prepare headers including rate limit information
        headers = {}
        if rl_final is not None:
            headers.update(get_rate_limit_headers(rl_final))

        # Add timing headers for non-streaming responses
        server_responded_at = time.time()
        headers["X-Total-Time-Ms"] = str(round(elapsed * 1000, 1))
        if completion_tokens > 0 and elapsed > 0:
            tokens_per_second = completion_tokens / elapsed
            headers["X-Tokens-Per-Second"] = str(round(tokens_per_second, 1))
        headers["X-Input-Tokens"] = str(prompt_tokens)
        headers["X-Output-Tokens"] = str(completion_tokens)
        headers["X-Total-Tokens"] = str(total_tokens)
        headers["X-Server-Received-At"] = str(request_received_at)
        headers["X-Server-Responded-At"] = str(server_responded_at)

        return JSONResponse(content=processed, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"[{request_id}] Unhandled server error: {type(e).__name__}",
            extra={"request_id": request_id, "error_type": type(e).__name__},
        )
        # Don't leak internal details, but include request ID for support
        raise HTTPException(
            status_code=500, detail=f"Internal server error (request ID: {request_id})"
        )


@router.post("/responses", tags=["chat"])
@traced(name="unified_responses", type="llm")
async def unified_responses(
    req: ResponseRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    """
    Unified response API endpoint (OpenAI v1/responses compatible).
    This is the newer, more flexible alternative to v1/chat/completions.

    Key differences:
    - Uses 'input' instead of 'messages'
    - Returns 'output' instead of 'choices'
    - Supports response_format for structured JSON output
    - Future-ready for multimodal input/output
    """
    # Capture request arrival timestamp for client latency calculation
    request_received_at = time.time()

    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            api_key = auth_header.split(" ", 1)[1].strip()

    logger.info("unified_responses start (api_key=%s, model=%s)", mask_key(api_key), req.model)

    # Start Braintrust span for this request
    span = start_span(name=f"responses_{req.model}", type="llm")

    rate_limit_mgr = None
    should_release_concurrency = False
    stream_release_handled = False

    try:
        # === 1) User + plan/trial prechecks (REFACTORED: using shared helpers) ===
        # Validate user (unified_responses requires authentication, no anonymous support)
        user, _ = await validate_user_and_auth(api_key, _to_thread)
        environment_tag = user.get("environment_tag", "live")

        # Validate trial access
        trial = await validate_trial(api_key, _to_thread)

        rate_limit_mgr = get_rate_limit_manager()

        # Rate limiting (only for non-trial users)
        rl_pre = await check_rate_limits(
            api_key,
            tokens_used=0,
            is_trial=trial.get("is_trial", False),
            _to_thread=_to_thread
        )

        # Credit check (only for non-trial users)
        if not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise HTTPException(status_code=402, detail="Insufficient credits")

        # === 2) Transform 'input' to 'messages' format for upstream ===
        messages = transform_input_to_messages(req.input)

        # === 2.1) Inject conversation history if session_id provided ===
        session_id = validate_session_id(session_id)
        messages = await inject_chat_history(session_id, user["id"], messages, _to_thread, get_chat_session)

        # Plan limit pre-check with estimated tokens (ONLY pre-request plan check)
        estimated_tokens = estimate_message_tokens(messages, getattr(req, "max_tokens", None))
        await check_plan_limits(user["id"], environment_tag, estimated_tokens, _to_thread)

        # Store original model for response
        original_model = req.model

        param_names = ("max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty", "tools")
        optional = build_optional_params(req, param_names)

        # Validate and adjust max_tokens for models with minimum requirements
        validate_and_adjust_max_tokens(optional, original_model)

        # Add response_format if specified
        if req.response_format:
            if req.response_format.type == "json_object":
                optional["response_format"] = {"type": "json_object"}
            elif req.response_format.type == "json_schema" and req.response_format.json_schema:
                optional["response_format"] = {
                    "type": "json_schema",
                    "json_schema": req.response_format.json_schema,
                }

        # Auto-detect provider if not specified
        req_provider_missing = req.provider is None or (
            isinstance(req.provider, str) and not req.provider
        )
        provider = normalize_provider_name((req.provider or "openrouter").lower())
        provider_locked = not req_provider_missing

        override_provider = detect_provider_from_model_id(original_model)
        if override_provider:
            override_provider = normalize_provider_name(override_provider.lower())
            if provider_locked and override_provider != provider:
                logger.info(
                    "Skipping provider override for model %s: request locked provider to '%s'",
                    sanitize_for_logging(original_model),
                    sanitize_for_logging(provider),
                )
            else:
                if override_provider != provider:
                    logger.info(
                        f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                    )
                    provider = override_provider
                # Mark provider as determined even if it matches the default
                # This prevents the fallback logic from incorrectly routing to wrong providers
                req_provider_missing = False

        if req_provider_missing:
            # Try to detect provider from model ID using the transformation module
            detected_provider = detect_provider_from_model_id(original_model)
            if detected_provider:
                provider = detected_provider
                logger.info(
                    "Auto-detected provider '%s' for model %s",
                    sanitize_for_logging(provider),
                    sanitize_for_logging(original_model),
                )
            else:
                # Fallback to checking cached models
                from src.services.models import get_cached_models

                # Try each provider with transformation
                for test_provider in [
                    "huggingface",
                    "featherless",
                    "fireworks",
                    "together",
                    "google-vertex",
                ]:
                    transformed = transform_model_id(original_model, test_provider)
                    provider_models = get_cached_models(test_provider) or []
                    if any(m.get("id") == transformed for m in provider_models):
                        provider = test_provider
                        logger.info(
                            "Auto-detected provider '%s' for model %s (transformed to %s)",
                            sanitize_for_logging(provider),
                            sanitize_for_logging(original_model),
                            sanitize_for_logging(transformed),
                        )
                        break

        provider_chain = build_provider_failover_chain(provider)
        provider_chain = enforce_model_failover_rules(original_model, provider_chain)
        provider_chain = filter_by_circuit_breaker(original_model, provider_chain)
        model = original_model

        # Diagnostic logging for tools parameter
        if "tools" in optional:
            logger.info(
                "Tools parameter detected (unified_responses): tools_count=%d, provider=%s, model=%s",
                len(optional["tools"]) if isinstance(optional["tools"], list) else 0,
                sanitize_for_logging(provider),
                sanitize_for_logging(original_model),
            )
            logger.debug("Tools content: %s", sanitize_for_logging(str(optional["tools"])[:500]))

        # === 3) Call upstream (streaming or non-streaming) ===
        if req.stream:
            last_http_exc = None
            for idx, attempt_provider in enumerate(provider_chain):
                attempt_model = transform_model_id(original_model, attempt_provider)
                if attempt_model != original_model:
                    logger.info(
                        f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                    )

                request_model = attempt_model
                http_exc = None
                try:
                    if attempt_provider == "featherless":
                        stream = await _to_thread(
                            make_featherless_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )
                    elif attempt_provider == "fireworks":
                        stream = await _to_thread(
                            make_fireworks_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )
                    elif attempt_provider == "together":
                        stream = await _to_thread(
                            make_together_request_openai_stream, messages, request_model, **optional
                        )
                    elif attempt_provider == "huggingface":
                        stream = await _to_thread(
                            make_huggingface_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )
                    elif attempt_provider == "aimo":
                        stream = await _to_thread(
                            make_aimo_request_openai_stream, messages, request_model, **optional
                        )
                    elif attempt_provider == "xai":
                        stream = await _to_thread(
                            make_xai_request_openai_stream, messages, request_model, **optional
                        )
                    elif attempt_provider == "cerebras":
                        stream = await _to_thread(
                            make_cerebras_request_openai_stream, messages, request_model, **optional
                        )
                    elif attempt_provider == "chutes":
                        stream = await _to_thread(
                            make_chutes_request_openai_stream, messages, request_model, **optional
                        )
                    elif attempt_provider == "groq":
                        stream = await _to_thread(
                            make_groq_request_openai_stream, messages, request_model, **optional
                        )
                    else:
                        stream = await _to_thread(
                            make_openrouter_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )

                    async def response_stream_generator(stream=stream, request_model=request_model):
                        """Transform chat/completions stream to OpenAI Responses API format.

                        OpenAI Responses API uses SSE with event: and data: fields.
                        Events emitted:
                        - response.created: Initial response object
                        - response.output_item.added: New output item started
                        - response.output_text.delta: Text content delta
                        - response.output_item.done: Output item completed
                        - response.completed: Final response with usage
                        """
                        sequence_number = 0
                        # Generate stable response ID upfront for consistency across events
                        response_id = f"resp_{secrets.token_hex(12)}"
                        created_timestamp = int(time.time())
                        model_name = request_model
                        has_sent_created = False
                        has_error = False  # Track if any errors occurred during streaming
                        usage_data = None
                        # Track multiple choices (n > 1) separately by index
                        # Each choice gets its own item_id, accumulated_content, etc.
                        items_by_index: dict[int, dict] = {}  # choice_index -> item state

                        async for chunk_data in stream_generator(
                            stream,
                            user,
                            api_key,
                            request_model,
                            trial,
                            environment_tag,
                            session_id,
                            messages,
                            rate_limit_mgr,
                            provider=attempt_provider,
                            tracker=None,
                            request_received_at=request_received_at,
                        ):
                            if chunk_data.startswith("data: "):
                                data_str = chunk_data[6:].strip()
                                if data_str == "[DONE]":
                                    # Ensure response.created is always sent first
                                    if not has_sent_created:
                                        created_event = {
                                            "type": "response.created",
                                            "sequence_number": sequence_number,
                                            "response": {
                                                "id": response_id,
                                                "object": "response",
                                                "created_at": created_timestamp,
                                                "model": model_name,
                                                "status": "in_progress",
                                                "output": [],
                                            },
                                        }
                                        yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n"
                                        sequence_number += 1
                                        has_sent_created = True

                                    # Emit done events only for items that were announced as added
                                    for idx in sorted(items_by_index.keys()):
                                        item_state = items_by_index[idx]
                                        # Only emit done events for items that had item_added sent
                                        if not item_state["item_added_sent"]:
                                            continue
                                        done_event = {
                                            "type": "response.output_text.done",
                                            "sequence_number": sequence_number,
                                            "response_id": response_id,
                                            "item_id": item_state["item_id"],
                                            "output_index": idx,
                                            "content_index": 0,
                                            "text": item_state["content"],
                                        }
                                        yield f"event: response.output_text.done\ndata: {json.dumps(done_event)}\n\n"
                                        sequence_number += 1

                                        item_done_event = {
                                            "type": "response.output_item.done",
                                            "sequence_number": sequence_number,
                                            "response_id": response_id,
                                            "output_index": idx,
                                            "item": {
                                                "id": item_state["item_id"],
                                                "type": "message",
                                                "role": "assistant",
                                                "status": "completed",
                                                "content": [{"type": "output_text", "text": item_state["content"]}],
                                            },
                                        }
                                        yield f"event: response.output_item.done\ndata: {json.dumps(item_done_event)}\n\n"
                                        sequence_number += 1

                                    # Build output list only from items that were announced
                                    output_list = [
                                        {
                                            "id": items_by_index[idx]["item_id"],
                                            "type": "message",
                                            "role": "assistant",
                                            "status": "completed",
                                            "content": [{"type": "output_text", "text": items_by_index[idx]["content"]}],
                                        }
                                        for idx in sorted(items_by_index.keys())
                                        if items_by_index[idx]["item_added_sent"]
                                    ]

                                    # Emit response.completed with appropriate status
                                    response_status = "failed" if has_error else "completed"
                                    completed_event = {
                                        "type": "response.completed",
                                        "sequence_number": sequence_number,
                                        "response": {
                                            "id": response_id,
                                            "object": "response",
                                            "created_at": created_timestamp,
                                            "model": model_name,
                                            "status": response_status,
                                            "output": output_list,
                                        },
                                    }
                                    # Add usage if available
                                    if usage_data:
                                        completed_event["response"]["usage"] = usage_data
                                    yield f"event: response.completed\ndata: {json.dumps(completed_event)}\n\n"
                                    continue

                                try:
                                    chunk_json = json.loads(data_str)

                                    # Extract model name from chunk if available
                                    if chunk_json.get("model"):
                                        model_name = chunk_json["model"]

                                    # Extract usage if present (some providers include it in final chunk)
                                    if chunk_json.get("usage"):
                                        usage_data = chunk_json["usage"]

                                    # Check for errors first (handles cases where both error and empty choices exist)
                                    if chunk_json.get("error"):
                                        # Transform error to Responses API error event
                                        # Handle both dict and string error formats
                                        error_field = chunk_json["error"]
                                        if isinstance(error_field, dict):
                                            error_message = error_field.get("message", "Unknown error")
                                        else:
                                            error_message = str(error_field)
                                        error_event = {
                                            "type": "error",
                                            "sequence_number": sequence_number,
                                            "error": {
                                                "type": "server_error",
                                                "message": error_message,
                                            },
                                        }
                                        yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                        sequence_number += 1
                                        has_error = True
                                    elif "choices" in chunk_json and chunk_json["choices"]:
                                        for choice in chunk_json["choices"]:
                                            choice_index = choice.get("index", 0)

                                            # Emit response.created on first chunk
                                            if not has_sent_created:
                                                created_event = {
                                                    "type": "response.created",
                                                    "sequence_number": sequence_number,
                                                    "response": {
                                                        "id": response_id,
                                                        "object": "response",
                                                        "created_at": created_timestamp,
                                                        "model": model_name,
                                                        "status": "in_progress",
                                                        "output": [],
                                                    },
                                                }
                                                yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n"
                                                sequence_number += 1
                                                has_sent_created = True

                                            # Initialize item state for this choice if not seen before
                                            if choice_index not in items_by_index:
                                                items_by_index[choice_index] = {
                                                    "item_id": f"item_{secrets.token_hex(8)}",
                                                    "content": "",
                                                    "item_added_sent": False,
                                                }

                                            item_state = items_by_index[choice_index]

                                            # Emit response.output_item.added on first content for this choice
                                            if not item_state["item_added_sent"] and "delta" in choice:
                                                item_added_event = {
                                                    "type": "response.output_item.added",
                                                    "sequence_number": sequence_number,
                                                    "response_id": response_id,
                                                    "output_index": choice_index,
                                                    "item": {
                                                        "id": item_state["item_id"],
                                                        "type": "message",
                                                        "role": choice["delta"].get("role", "assistant"),
                                                        "status": "in_progress",
                                                        "content": [],
                                                    },
                                                }
                                                yield f"event: response.output_item.added\ndata: {json.dumps(item_added_event)}\n\n"
                                                sequence_number += 1
                                                item_state["item_added_sent"] = True

                                            # Emit response.output_text.delta for content
                                            if "delta" in choice and "content" in choice["delta"]:
                                                delta_content = choice["delta"]["content"]
                                                if delta_content:
                                                    item_state["content"] += delta_content
                                                    delta_event = {
                                                        "type": "response.output_text.delta",
                                                        "sequence_number": sequence_number,
                                                        "response_id": response_id,
                                                        "item_id": item_state["item_id"],
                                                        "output_index": choice_index,
                                                        "content_index": 0,
                                                        "delta": delta_content,
                                                    }
                                                    yield f"event: response.output_text.delta\ndata: {json.dumps(delta_event)}\n\n"
                                                    sequence_number += 1
                                except json.JSONDecodeError:
                                    # Emit as error event for malformed JSON
                                    error_event = {
                                        "type": "error",
                                        "sequence_number": sequence_number,
                                        "error": {
                                            "type": "invalid_response",
                                            "message": f"Malformed response chunk: {data_str[:100]}",
                                        },
                                    }
                                    yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                    sequence_number += 1
                                    has_error = True
                            elif chunk_data.startswith("event:") or chunk_data.strip() == "":
                                # Pass through SSE event lines and empty lines (SSE formatting)
                                yield chunk_data
                            else:
                                # Unknown format - emit as error
                                error_event = {
                                    "type": "error",
                                    "sequence_number": sequence_number,
                                    "error": {
                                        "type": "invalid_response",
                                        "message": "Unexpected chunk format",
                                    },
                                }
                                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                sequence_number += 1
                                has_error = True

                    stream_release_handled = True
                    provider = attempt_provider
                    model = request_model

                    # SSE streaming headers to prevent buffering by proxies/nginx
                    stream_headers = {
                        "X-Accel-Buffering": "no",
                        "Cache-Control": "no-cache, no-transform",
                        "Connection": "keep-alive",
                    }

                    return StreamingResponse(
                        response_stream_generator(),
                        media_type="text/event-stream",
                        headers=stream_headers,
                    )
                except Exception as exc:
                    http_exc = map_provider_error(attempt_provider, request_model, exc)

                if http_exc is None:
                    continue

                last_http_exc = http_exc
                if idx < len(provider_chain) - 1 and should_failover(http_exc):
                    next_provider = provider_chain[idx + 1]
                    logger.warning(
                        "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                        attempt_provider,
                        http_exc.status_code,
                        http_exc.detail,
                        next_provider,
                    )
                    continue

                raise http_exc

            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        # Non-streaming response
        start = time.monotonic()
        processed = None
        last_http_exc = None

        for idx, attempt_provider in enumerate(provider_chain):
            attempt_model = transform_model_id(original_model, attempt_provider)
            if attempt_model != original_model:
                logger.info(
                    f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                )

            request_model = attempt_model
            # Get provider timeout from registry
            request_timeout = get_provider_registry().get_timeout(attempt_provider)
            if request_timeout != 30:  # 30 is the default timeout
                logger.debug(
                    "Using extended timeout %ss for provider %s", request_timeout, attempt_provider
                )

            http_exc = None
            try:
                # ============================================================================
                # Provider Registry - Non-streaming Requests
                # Replaces if/elif chains with registry lookup
                # ============================================================================
                provider_config = get_provider_registry().get(attempt_provider)
                if not provider_config:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown provider: {attempt_provider}. Available: {', '.join(get_provider_registry().list_providers())}"
                    )

                # Make request with provider-specific timeout
                resp_raw = await asyncio.wait_for(
                    _to_thread(
                        provider_config.make_request,
                        messages,
                        request_model,
                        **optional
                    ),
                    timeout=request_timeout,
                )
                # Process response
                processed = await _to_thread(provider_config.process_response, resp_raw)

                provider = attempt_provider
                model = request_model
                break
            except Exception as exc:
                http_exc = map_provider_error(attempt_provider, request_model, exc)

            if http_exc is None:
                continue

            last_http_exc = http_exc
            if idx < len(provider_chain) - 1 and should_failover(http_exc):
                next_provider = provider_chain[idx + 1]
                logger.warning(
                    "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                    attempt_provider,
                    http_exc.status_code,
                    http_exc.detail,
                    next_provider,
                )
                continue

            raise http_exc

        if processed is None:
            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
        if not post_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}"
            )

        if trial.get("is_trial") and not trial.get("is_expired"):
            try:
                await _to_thread(track_trial_usage, api_key, total_tokens, 1)
            except Exception as e:
                logger.warning("Failed to track trial usage: %s", e)

        if not trial.get("is_trial", False):
            rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
            if not rl_final.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_final.reason,
                        "retry_after": rl_final.retry_after,
                        "remaining_requests": rl_final.remaining_requests,
                        "remaining_tokens": rl_final.remaining_tokens,
                        "tokens_requested": total_tokens,
                    },
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_final.reason}",
                    headers=(
                        {"Retry-After": str(rl_final.retry_after)} if rl_final.retry_after else None
                    ),
                )

        is_trial = trial.get("is_trial", False)

        # Credit/usage tracking using shared helper
        cost = await handle_billing(
            api_key=api_key,
            user_id=user["id"],
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            elapsed_ms=int(elapsed * 1000),
            is_trial=is_trial,
            _to_thread=_to_thread,
        )

        # Record Prometheus metrics and passive health monitoring
        await _record_inference_metrics_and_health(
            provider=provider,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            success=True,
            error_message=None
        )

        # === 4.5) Log activity for tracking and analytics ===
        try:
            provider_name = get_provider_from_model(model)
            speed = total_tokens / elapsed if elapsed > 0 else 0
            await _to_thread(
                log_activity,
                user_id=user["id"],
                model=model,
                provider=provider_name,
                tokens=total_tokens,
                cost=cost if not trial.get("is_trial", False) else 0.0,
                speed=speed,
                finish_reason=(processed.get("choices") or [{}])[0].get("finish_reason", "stop"),
                app="API",
                metadata={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": "/v1/responses",
                    "session_id": session_id,
                    "gateway": provider,  # Track which gateway was used
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to log activity for user {user['id']}, model {model}: {e}", exc_info=True
            )

        # === 5) History ===
        # Validate session_id before attempting to save
        session_id = validate_session_id(session_id)

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
                        # Extract text content from multimodal content if needed
                        user_content = last_user.get("content", "")
                        if isinstance(user_content, list):
                            # Extract text from multimodal content
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

                    # Safely extract assistant content (handle None values in choices)
                    choices = processed.get("choices") or [{}]
                    first_choice = choices[0] if choices else {}
                    message = first_choice.get("message") or {}
                    assistant_content = message.get("content", "")
                    if assistant_content:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "assistant",
                            assistant_content,
                            model,
                            total_tokens,
                            user["id"],
                        )
            except Exception as e:
                logger.error(
                    f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                    exc_info=True,
                )

        # === 6) Transform response format: choices -> output ===
        output = []
        for choice in processed.get("choices", []):
            output_item = {
                "index": choice.get("index", 0),
                "finish_reason": choice.get("finish_reason"),
            }

            # Transform message to response format
            if "message" in choice:
                msg = choice["message"]
                output_item["role"] = msg.get("role", "assistant")
                output_item["content"] = msg.get("content", "")

                # Include function/tool calls if present
                if "function_call" in msg:
                    output_item["function_call"] = msg["function_call"]
                if "tool_calls" in msg:
                    output_item["tool_calls"] = msg["tool_calls"]

            output.append(output_item)

        response = {
            "id": processed.get("id"),
            "object": "response",
            "created": processed.get("created"),
            "model": processed.get("model"),
            "output": output,
            "usage": usage,
        }

        # Add gateway usage metadata
        response["gateway_usage"] = {
            "tokens_charged": total_tokens,
            "backend_processing_ms": int(elapsed * 1000),
            "backend_received_at": int(request_received_at * 1000),  # Unix timestamp in ms
            "backend_responded_at": int((request_received_at + elapsed) * 1000),  # Unix timestamp in ms
            # Legacy field for backwards compatibility
            "request_ms": int(elapsed * 1000),
        }
        if not trial.get("is_trial", False):
            response["gateway_usage"]["cost_usd"] = round(cost, 6)

        # === 7) Log to Braintrust ===
        try:
            # Convert input messages to loggable format
            input_messages = []
            for inp_msg in req.input:
                if isinstance(inp_msg.content, str):
                    input_messages.append({"role": inp_msg.role, "content": inp_msg.content})
                else:
                    input_messages.append({"role": inp_msg.role, "content": str(inp_msg.content)})

            span.log(
                input=input_messages,
                output=response["output"][0].get("content", "") if response["output"] else "",
                metrics={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": int(elapsed * 1000),
                    "cost_usd": cost if not trial.get("is_trial", False) else 0.0,
                },
                metadata={
                    "model": model,
                    "provider": provider,
                    "user_id": user["id"],
                    "session_id": session_id,
                    "is_trial": trial.get("is_trial", False),
                    "environment": user.get("environment_tag", "live"),
                    "endpoint": "/v1/responses",
                },
            )
            span.end()
        except Exception as e:
            logger.warning(f"Failed to log to Braintrust: {e}")

        return response

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled server error in unified_responses")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if (
            should_release_concurrency
            and rate_limit_mgr
            and (not req.stream or not stream_release_handled)
        ):
            try:
                await rate_limit_mgr.release_concurrency(api_key)
            except Exception as exc:
                logger.debug("Failed to release concurrency for %s: %s", mask_key(api_key), exc)


# Log successful module load - this should appear in startup logs if chat.py loads correctly
logger.info("✅ Chat module fully loaded - all routes registered successfully")
logger.info(f"   Total routes in router: {len(router.routes)}")

# Log any provider import errors that occurred during safe imports
if _provider_import_errors:
    logger.warning(f"⚠  Provider import warnings ({len(_provider_import_errors)} failed):")
    for provider_name, error_msg in _provider_import_errors.items():
        logger.warning(f"     - {provider_name}: {error_msg}")
else:
    logger.info("✓ All provider clients loaded successfully")
