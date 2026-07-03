"""Upstream dispatch for the chat route (Phase 0d, Step 3 of chat_completions).

Holds the streaming and non-streaming provider-call-with-failover loops,
extracted verbatim from ``chat_completions``. Request-scoped state is passed in
as keyword-only arguments so the moved bodies stay unchanged.

The dynamically-injected OpenRouter provider functions and ``stream_generator``
are resolved through the ``src.routes.chat`` module at call time (``_chat.*``)
rather than imported directly, so that tests patching
``src.routes.chat.make_openrouter_request_openai`` etc. still bind. Behaviour is
unchanged from the original inline code.
"""

from __future__ import annotations

import asyncio  # noqa: F401  (used by moved bodies)
import logging
import time  # noqa: F401  (used by moved bodies)

import httpx  # noqa: F401  (used by moved bodies)
from fastapi import HTTPException  # noqa: F401
from fastapi.responses import StreamingResponse

from src.adapters.chat import OpenAIChatAdapter  # noqa: F401
from src.handlers.chat_handler import ChatInferenceHandler  # noqa: F401
from src.handlers.provider_registry import PROVIDER_ROUTING  # noqa: F401
from src.routes.chat_helpers import _to_thread  # noqa: F401
from src.services.model_transformations import transform_model_id  # noqa: F401
from src.services.pricing import calculate_cost_async  # noqa: F401
from src.services.prometheus_metrics import (  # noqa: F401
    get_trace_exemplar,
    model_inference_duration,
    model_inference_requests,
    tokens_used,
)
from src.services.provider_failover import (  # noqa: F401
    build_provider_failover_chain,
    enforce_model_failover_rules,
    filter_by_circuit_breaker,
    map_provider_error,
    should_failover,
)
from src.utils.ai_tracing import AIRequestType, AITracer  # noqa: F401
from src.utils.rate_limit_headers import get_rate_limit_headers  # noqa: F401
from src.utils.sentry_context import capture_provider_error  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_TIMEOUT = 30
PROVIDER_TIMEOUTS = {
    "huggingface": 120,
    "near": 120,  # Large models like Qwen3-30B need extended timeout
}


def _maybe_record_402(provider: str, status_code: int) -> None:
    """Record a 402 for provider credit monitoring. Never raises."""
    if status_code == 402:
        try:
            from src.services.provider_credit_monitor import record_provider_402

            record_provider_402(provider)
        except Exception:
            pass


async def dispatch_streaming(
    *,
    is_anonymous,
    provider_chain,
    messages,
    original_model,
    optional,
    api_key,
    api_key_id,
    background_tasks,
    request,
    request_id,
    rl_pre,
    tracker,
    user,
    trial,
    environment_tag,
    session_id,
    rate_limit_mgr,
    client_ip,
) -> StreamingResponse:
    """Streaming path of chat_completions Step 3. Returns an SSE StreamingResponse."""
    from src.routes import chat as _chat  # resolve patched provider fns / stream_generator

    # Streaming path
    # Use unified handler for authenticated streaming requests
    if not is_anonymous:
        # Authenticated streaming with provider failover.
        # We wrap consumption inside an async generator so that errors during
        # iteration (not just setup) can be caught and the next provider tried,
        # but only before the first content chunk has been sent to the client.
        async def _auth_stream_with_failover():
            _adapter = OpenAIChatAdapter()
            last_exc = None

            for _idx, _attempt_provider in enumerate(provider_chain):
                _is_last = _idx == len(provider_chain) - 1
                _content_started = False
                try:
                    _internal_req = _adapter.to_internal_request(
                        {
                            "messages": messages,
                            "model": original_model,
                            "stream": True,
                            **optional,
                        }
                    )
                    # Set the provider for this attempt
                    _internal_req.provider = _attempt_provider

                    _handler = ChatInferenceHandler(api_key, background_tasks, request=request)
                    _internal_stream = _handler.process_stream(_internal_req)
                    _sse_stream = _adapter.from_internal_stream(_internal_stream)

                    async for _chunk in _sse_stream:
                        _content_started = True
                        yield _chunk

                    return  # Stream completed successfully

                except HTTPException as _http_exc:
                    if not _content_started and not _is_last and should_failover(_http_exc):
                        logger.warning(
                            f"[Unified Handler] Provider '{_attempt_provider}' failed "
                            f"(HTTP {_http_exc.status_code}) for model {original_model}, "
                            f"failing over ({_idx + 1}/{len(provider_chain)})"
                        )
                        last_exc = _http_exc
                        continue
                    raise

                except Exception as _exc:
                    if not _content_started and not _is_last:
                        logger.warning(
                            f"[Unified Handler] Provider '{_attempt_provider}' error "
                            f"({type(_exc).__name__}: {_exc}) for model {original_model}, "
                            f"failing over ({_idx + 1}/{len(provider_chain)})"
                        )
                        last_exc = map_provider_error(_attempt_provider, original_model, _exc)
                        continue
                    if isinstance(_exc, HTTPException):
                        raise
                    raise map_provider_error(_attempt_provider, original_model, _exc) from _exc

            # All providers exhausted
            if last_exc:
                raise last_exc

        # Prepare response headers
        stream_headers = {}
        if rl_pre is not None:
            stream_headers.update(get_rate_limit_headers(rl_pre))
        if tracker:
            prep_time_ms = tracker.get_total_duration() * 1000
            stream_headers["X-Prep-Time-Ms"] = f"{prep_time_ms:.1f}"
        stream_headers["X-Provider"] = "unified"
        stream_headers["X-Model"] = original_model
        stream_headers["X-Requested-Model"] = original_model
        # SSE streaming headers to prevent buffering by proxies/nginx
        stream_headers["X-Accel-Buffering"] = "no"
        stream_headers["Cache-Control"] = "no-cache, no-transform"
        stream_headers["Connection"] = "keep-alive"

        logger.info(
            f"[Unified Handler] Returning SSE streaming response for model {original_model} "
            f"(provider_chain={provider_chain})"
        )

        return StreamingResponse(
            _auth_stream_with_failover(),
            media_type="text/event-stream",
            headers=stream_headers,
        )
    else:
        # Anonymous users: keep existing provider routing logic
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
                # Registry-based provider dispatch (replaces ~400 lines of if-elif chains)
                # Note: Streaming tracing is handled in _chat.stream_generator to capture final token counts
                if attempt_provider == "fal":
                    # FAL models are for image/video generation, not chat completions
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {
                                "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                "and is not available through the chat completions endpoint. "
                                "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                "type": "invalid_request_error",
                                "code": "model_not_supported_for_chat",
                            }
                        },
                    )
                elif attempt_provider in PROVIDER_ROUTING:
                    # Use registry for all registered providers
                    stream_func = PROVIDER_ROUTING[attempt_provider]["stream"]
                    stream = await _to_thread(stream_func, messages, request_model, **optional)
                else:
                    # Default to OpenRouter with async streaming for performance
                    try:
                        stream = await _chat.make_openrouter_request_openai_stream_async(
                            messages, request_model, **optional
                        )
                        is_async_stream = True
                        logger.debug(f"Using async streaming for OpenRouter model {request_model}")
                    except Exception as async_err:
                        # Fallback to sync streaming if async fails
                        logger.warning(f"Async streaming failed, falling back to sync: {async_err}")
                        stream = await _to_thread(
                            _chat.make_openrouter_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )
                        is_async_stream = False

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
                    _chat.stream_generator(
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
                        request_id=request_id,
                        api_key_id=api_key_id,
                        client_ip=client_ip if is_anonymous else None,
                        request=request,
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
                        request_id=request_id,
                    )
                elif isinstance(exc, httpx.RequestError):
                    logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                    # Capture network error to Sentry
                    capture_provider_error(
                        exc,
                        provider=attempt_provider,
                        model=request_model,
                        endpoint="/v1/chat/completions",
                        request_id=request_id,
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
                            request_id=request_id,
                        )
                else:
                    logger.error("Unexpected upstream error (%s): %s", attempt_provider, exc)
                    # Capture unexpected errors to Sentry
                    capture_provider_error(
                        exc,
                        provider=attempt_provider,
                        model=request_model,
                        endpoint="/v1/chat/completions",
                        request_id=request_id,
                    )
                http_exc = map_provider_error(attempt_provider, request_model, exc)

                last_http_exc = http_exc

                # Record 402 for provider credit monitoring BEFORE failover
                # (must run before should_failover/continue skips past it)
                _maybe_record_402(attempt_provider, http_exc.status_code)

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

                # If this is a 402 (Payment Required) error and we've exhausted the chain,
                # rebuild the chain allowing payment failover to try alternative providers
                if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                    extended_chain = build_provider_failover_chain(provider)
                    extended_chain = enforce_model_failover_rules(
                        original_model, extended_chain, allow_payment_failover=True
                    )
                    extended_chain = filter_by_circuit_breaker(original_model, extended_chain)
                    # Find providers we haven't tried yet
                    new_providers = [p for p in extended_chain if p not in provider_chain]
                    if new_providers:
                        logger.warning(
                            "Provider '%s' returned 402 (Payment Required). "
                            "Extending failover chain with: %s",
                            attempt_provider,
                            new_providers,
                        )
                        provider_chain.extend(new_providers)
                        continue

                raise http_exc

        raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")


async def dispatch_non_streaming(
    *,
    is_anonymous,
    provider_chain,
    messages,
    original_model,
    optional,
    model,
    provider,
    api_key,
    background_tasks,
    request,
    user,
    trial,
):
    """Non-streaming path of chat_completions Step 3.

    Returns ``(processed, provider, model)``. Raises on upstream failure.
    """
    from src.routes import chat as _chat  # resolve patched provider fns

    processed = None
    last_http_exc = None

    # Use unified handler for authenticated non-streaming requests
    if not is_anonymous:
        try:
            logger.info(
                f"[Unified Handler] Processing authenticated non-streaming request for model {original_model}"
            )

            # Convert external OpenAI format to internal format
            adapter = OpenAIChatAdapter()
            internal_request = adapter.to_internal_request(
                {"messages": messages, "model": original_model, "stream": False, **optional}
            )
            # Pass the detected provider so the handler doesn't have to re-detect
            internal_request.provider = provider

            # Create unified handler with user context (pass request for disconnect detection)
            handler = ChatInferenceHandler(api_key, background_tasks, request=request)

            # Wrap with AITracer for gen_ai.* telemetry + track duration for Prometheus
            inference_start = time.time()
            async with AITracer.trace_inference(
                provider="openrouter",  # Will be updated after response
                model=original_model,
                request_type=AIRequestType.CHAT_COMPLETION,
                operation_name=f"unified_handler/{original_model}",
            ) as trace_ctx:
                # Process request through unified pipeline
                internal_response = await handler.process(internal_request)

                # Convert internal response back to OpenAI format
                processed = adapter.from_internal_response(internal_response)

                # Extract values for postprocessing
                provider = internal_response.provider_used or "openrouter"
                model = internal_response.model or original_model

                # Set trace attributes with actual values from response
                usage = processed.get("usage", {}) or {}
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                trace_ctx.set_token_usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=usage.get("total_tokens", 0),
                )
                trace_ctx.set_response_model(
                    response_model=model,
                    finish_reason=(
                        processed.get("choices", [{}])[0].get("finish_reason")
                        if processed.get("choices")
                        else None
                    ),
                    response_id=processed.get("id"),
                )
                if optional:
                    trace_ctx.set_model_parameters(
                        temperature=optional.get("temperature"),
                        max_tokens=optional.get("max_tokens"),
                        top_p=optional.get("top_p"),
                        frequency_penalty=optional.get("frequency_penalty"),
                        presence_penalty=optional.get("presence_penalty"),
                    )
                if user:
                    trace_ctx.set_user_info(
                        user_id=str(user.get("id")),
                        tier="trial" if trial.get("is_trial") else "paid",
                    )

            # Record Prometheus metrics for model popularity tracking
            inference_duration = time.time() - inference_start
            exemplar = get_trace_exemplar()
            model_inference_requests.labels(provider=provider, model=model, status="success").inc(
                1, exemplar=exemplar
            )
            model_inference_duration.labels(provider=provider, model=model).observe(
                inference_duration, exemplar=exemplar
            )
            if input_tokens > 0:
                tokens_used.labels(provider=provider, model=model, token_type="input").inc(
                    input_tokens, exemplar=exemplar
                )
            if output_tokens > 0:
                tokens_used.labels(provider=provider, model=model, token_type="output").inc(
                    output_tokens, exemplar=exemplar
                )

            logger.info(
                f"[Unified Handler] Successfully processed request: provider={provider}, model={model}"
            )

        except Exception as exc:
            # Record error metric for model popularity tracking
            error_provider = provider if "provider" in locals() else "openrouter"
            error_model = model if "model" in locals() else original_model
            model_inference_requests.labels(
                provider=error_provider, model=error_model, status="error"
            ).inc(1, exemplar=get_trace_exemplar())

            # Map any errors to HTTPException
            logger.error(f"[Unified Handler] Error: {type(exc).__name__}: {exc}", exc_info=True)
            if isinstance(exc, HTTPException):
                raise
            # Map provider-specific errors (map_provider_error imported at module level)
            http_exc = map_provider_error(error_provider, error_model, exc)
            raise http_exc
    else:
        # Anonymous users: keep existing provider routing logic
        for idx, attempt_provider in enumerate(provider_chain):
            attempt_model = transform_model_id(original_model, attempt_provider)
            if attempt_model != original_model:
                logger.info(
                    f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                )

            request_model = attempt_model
            request_timeout = PROVIDER_TIMEOUTS.get(attempt_provider, DEFAULT_PROVIDER_TIMEOUT)
            if request_timeout != DEFAULT_PROVIDER_TIMEOUT:
                logger.debug(
                    "Using extended timeout %ss for provider %s",
                    request_timeout,
                    attempt_provider,
                )

            try:
                # Registry-based provider dispatch (replaces ~400 lines of if-elif chains)
                # Wrap provider calls with distributed tracing for Tempo
                async with AITracer.trace_inference(
                    provider=attempt_provider,
                    model=request_model,
                    request_type=AIRequestType.CHAT_COMPLETION,
                ) as trace_ctx:
                    if attempt_provider == "fal":
                        # FAL models are for image/video generation, not chat completions
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                    "and is not available through the chat completions endpoint. "
                                    "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                    "type": "invalid_request_error",
                                    "code": "model_not_supported_for_chat",
                                }
                            },
                        )
                    elif attempt_provider in PROVIDER_ROUTING:
                        # Use registry for all registered providers
                        request_func = PROVIDER_ROUTING[attempt_provider]["request"]
                        process_func = PROVIDER_ROUTING[attempt_provider]["process"]
                        resp_raw = await asyncio.wait_for(
                            _to_thread(request_func, messages, request_model, **optional),
                            timeout=request_timeout,
                        )
                        processed = await _to_thread(process_func, resp_raw)
                    else:
                        # Default to OpenRouter
                        resp_raw = await asyncio.wait_for(
                            _to_thread(
                                _chat.make_openrouter_request_openai,
                                messages,
                                request_model,
                                **optional,
                            ),
                            timeout=request_timeout,
                        )
                        processed = await _to_thread(
                            _chat.process_openrouter_response,
                            resp_raw,
                        )

                    # Extract token usage from response for tracing
                    usage = processed.get("usage", {}) or {}
                    trace_prompt_tokens = usage.get("prompt_tokens", 0)
                    trace_completion_tokens = usage.get("completion_tokens", 0)
                    trace_total_tokens = usage.get("total_tokens", 0)

                    # Calculate cost for tracing
                    trace_cost = await calculate_cost_async(
                        request_model, trace_prompt_tokens, trace_completion_tokens
                    )

                    # Set token usage and cost on trace span
                    trace_ctx.set_token_usage(
                        input_tokens=trace_prompt_tokens,
                        output_tokens=trace_completion_tokens,
                        total_tokens=trace_total_tokens,
                    )
                    trace_ctx.set_cost(trace_cost)

                    # Set actual response model (may differ from requested model)
                    response_model = processed.get("model", request_model)
                    # Extract finish reason from first choice if available
                    choices = processed.get("choices", [])
                    finish_reason = choices[0].get("finish_reason") if choices else None
                    response_id = processed.get("id")
                    trace_ctx.set_response_model(
                        response_model=response_model,
                        finish_reason=finish_reason,
                        response_id=response_id,
                    )

                    # Set model parameters if available
                    if optional:
                        trace_ctx.set_model_parameters(
                            temperature=optional.get("temperature"),
                            max_tokens=optional.get("max_tokens"),
                            top_p=optional.get("top_p"),
                            frequency_penalty=optional.get("frequency_penalty"),
                            presence_penalty=optional.get("presence_penalty"),
                        )

                    # Set user info if authenticated
                    if not is_anonymous and user:
                        trace_ctx.set_user_info(
                            user_id=str(user.get("id")),
                            tier="trial" if trial.get("is_trial") else "paid",
                        )

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
                        "Upstream HTTP error (%s): %s",
                        attempt_provider,
                        exc.response.status_code,
                    )
                else:
                    logger.error("Unexpected upstream error (%s): %s", attempt_provider, exc)
                http_exc = map_provider_error(attempt_provider, request_model, exc)

                last_http_exc = http_exc

                # Record 402 for provider credit monitoring BEFORE failover
                _maybe_record_402(attempt_provider, http_exc.status_code)

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

                # If this is a 402 (Payment Required) error and we've exhausted the chain,
                # rebuild the chain allowing payment failover to try alternative providers
                if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                    extended_chain = build_provider_failover_chain(provider)
                    extended_chain = enforce_model_failover_rules(
                        original_model, extended_chain, allow_payment_failover=True
                    )
                    extended_chain = filter_by_circuit_breaker(original_model, extended_chain)
                    # Find providers we haven't tried yet
                    new_providers = [p for p in extended_chain if p not in provider_chain]
                    if new_providers:
                        logger.warning(
                            "Provider '%s' returned 402 (Payment Required). "
                            "Extending failover chain with: %s",
                            attempt_provider,
                            new_providers,
                        )
                        provider_chain.extend(new_providers)
                        continue

                raise http_exc

    if processed is None:
        raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

    return processed, provider, model
