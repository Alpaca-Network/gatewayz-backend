"""
Unified chat handler - single source of truth for ALL chat operations.

This module consolidates logic from:
- /v1/chat/completions (OpenAI format)
- /v1/messages (Anthropic format)
- /v1/responses (Responses API format)

Into a single, well-tested implementation.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import HTTPException

from src.services.provider_registry import get_provider_registry
from src.services.model_transformations import transform_model_id, detect_provider_from_model_id
from src.services.provider_failover import (
    build_provider_failover_chain,
    enforce_model_failover_rules,
    filter_by_circuit_breaker,
    map_provider_error,
    should_failover
)
from src.services.pricing import calculate_cost
from src.db.users import deduct_credits, log_api_usage_transaction, record_usage
from src.db.api_keys import increment_api_key_usage
from src.db.activity import log_activity, get_provider_from_model
from src.db.chat_history import save_chat_message, get_chat_session
from src.db.rate_limits import update_rate_limit_usage
from src.services.trial_validation import track_trial_usage
from src.utils.token_estimator import estimate_message_tokens

logger = logging.getLogger(__name__)


async def _to_thread(func, *args, **kwargs):
    """Helper to run blocking functions in thread pool"""
    return await asyncio.to_thread(func, *args, **kwargs)


class UnifiedChatHandler:
    """
    Single source of truth for ALL chat operations.

    Consolidates logic from multiple endpoints into one implementation,
    supporting all API formats (OpenAI, Anthropic, Responses API).

    Features:
    - Provider selection and automatic failover
    - Model ID transformation per provider
    - Streaming and non-streaming support
    - Billing and credit management
    - Rate limiting
    - Chat history tracking
    - Activity logging
    - Prometheus metrics
    - Trial account handling
    - Anonymous request support
    """

    def __init__(self):
        self.logger = logger

    async def process_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        user: dict[str, Any] | None = None,
        api_key: str | None = None,
        provider: str | None = None,
        stream: bool = False,
        session_id: int | None = None,
        is_trial: bool = False,
        is_anonymous: bool = False,
        environment_tag: str = "live",
        request_id: str | None = None,
        **optional_params
    ) -> dict[str, Any] | AsyncGenerator:
        """
        Process a chat request through the unified pipeline.

        Args:
            messages: List of message dicts (normalized to OpenAI format)
            model: Model ID to use
            user: User dict (None for anonymous)
            api_key: API key (None for anonymous)
            provider: Specific provider to use (None for auto-detect)
            stream: Whether to stream response
            session_id: Chat session ID for history
            is_trial: Whether this is a trial request
            is_anonymous: Whether this is an anonymous request
            environment_tag: Environment (live, staging, development)
            request_id: Request correlation ID
            **optional_params: Additional parameters (max_tokens, temperature, etc.)

        Returns:
            Unified response dict (if not streaming) or AsyncGenerator (if streaming)

        Raises:
            HTTPException: On validation or provider errors
        """

        # Generate request ID if not provided
        if not request_id:
            request_id = str(uuid.uuid4())

        start_time = time.monotonic()

        self.logger.info(
            f"[{request_id}] Unified handler: model={model}, provider={provider}, "
            f"stream={stream}, anonymous={is_anonymous}, trial={is_trial}"
        )

        # 1. Provider Selection
        if not provider:
            provider = detect_provider_from_model_id(model)
            if provider:
                self.logger.info(f"[{request_id}] Auto-detected provider: {provider}")
            else:
                provider = "openrouter"  # Default
                self.logger.info(f"[{request_id}] Using default provider: {provider}")

        # Normalize provider name
        from src.config.providers import normalize_provider_name
        provider = normalize_provider_name(provider)

        # 2. Build Provider Failover Chain
        provider_chain = build_provider_failover_chain(provider)
        provider_chain = enforce_model_failover_rules(model, provider_chain)
        provider_chain = filter_by_circuit_breaker(model, provider_chain)

        self.logger.info(f"[{request_id}] Provider chain: {provider_chain}")

        # 3. Try Providers in Order
        last_error = None

        for idx, attempt_provider in enumerate(provider_chain):
            try:
                # Transform model ID for this provider
                transformed_model = transform_model_id(model, attempt_provider)

                if transformed_model != model:
                    self.logger.info(
                        f"[{request_id}] Model transformation: {model} → {transformed_model} "
                        f"for provider {attempt_provider}"
                    )

                # Get provider configuration
                provider_config = get_provider_registry().get(attempt_provider)
                if not provider_config:
                    raise ValueError(f"Unknown provider: {attempt_provider}")

                # Make request (streaming or non-streaming)
                if stream:
                    response = await self._handle_streaming_request(
                        provider_config=provider_config,
                        provider_name=attempt_provider,
                        messages=messages,
                        model=transformed_model,
                        original_model=model,
                        user=user,
                        api_key=api_key,
                        is_trial=is_trial,
                        is_anonymous=is_anonymous,
                        environment_tag=environment_tag,
                        session_id=session_id,
                        request_id=request_id,
                        **optional_params
                    )
                else:
                    response = await self._handle_non_streaming_request(
                        provider_config=provider_config,
                        provider_name=attempt_provider,
                        messages=messages,
                        model=transformed_model,
                        original_model=model,
                        user=user,
                        api_key=api_key,
                        is_trial=is_trial,
                        is_anonymous=is_anonymous,
                        environment_tag=environment_tag,
                        session_id=session_id,
                        request_id=request_id,
                        start_time=start_time,
                        **optional_params
                    )

                # Success!
                self.logger.info(
                    f"[{request_id}] Success with provider {attempt_provider}"
                )
                return response

            except Exception as exc:
                self.logger.warning(
                    f"[{request_id}] Provider {attempt_provider} failed: {exc}",
                    exc_info=True
                )

                # Map to HTTP exception
                http_exc = map_provider_error(attempt_provider, transformed_model, exc)
                last_error = http_exc

                # Try next provider if available and error is retryable
                if idx < len(provider_chain) - 1 and should_failover(http_exc):
                    next_provider = provider_chain[idx + 1]
                    self.logger.info(
                        f"[{request_id}] Failing over: {attempt_provider} → {next_provider}"
                    )
                    continue

                # No more providers or non-retryable error
                raise http_exc

        # All providers failed
        raise last_error or HTTPException(
            status_code=502,
            detail="All providers failed"
        )

    async def _handle_non_streaming_request(
        self,
        provider_config,
        provider_name: str,
        messages: list[dict[str, Any]],
        model: str,
        original_model: str,
        user: dict[str, Any] | None,
        api_key: str | None,
        is_trial: bool,
        is_anonymous: bool,
        environment_tag: str,
        session_id: int | None,
        request_id: str,
        start_time: float,
        **optional_params
    ) -> dict[str, Any]:
        """
        Handle non-streaming request.

        Returns:
            Unified response dict (not formatted to specific API format yet)
        """

        # Get provider timeout
        timeout = get_provider_registry().get_timeout(provider_name)

        # Make request to provider
        self.logger.debug(
            f"[{request_id}] Calling provider {provider_name} with timeout {timeout}s"
        )

        try:
            raw_response = await asyncio.wait_for(
                _to_thread(
                    provider_config.make_request,
                    messages,
                    model,
                    **optional_params
                ),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"Provider {provider_name} timeout after {timeout}s"
            )

        # Process provider response to normalized format
        processed = await _to_thread(
            provider_config.process_response,
            raw_response
        )

        elapsed = time.monotonic() - start_time

        # Extract usage information
        usage = processed.get("usage", {}) or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        # If usage not provided, estimate it
        if total_tokens == 0:
            estimated_prompt = estimate_message_tokens(messages, model)
            prompt_tokens = estimated_prompt
            # Estimate completion tokens from content length
            choices = processed.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                completion_tokens = len(content.split()) * 1.3  # Rough estimate
            total_tokens = prompt_tokens + completion_tokens

        # Calculate cost
        cost = calculate_cost(original_model, prompt_tokens, completion_tokens)

        # Extract content from response
        choices = processed.get("choices", [])
        if not choices:
            content = ""
            finish_reason = "error"
            tool_calls = None
        else:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            finish_reason = choices[0].get("finish_reason", "stop")
            tool_calls = message.get("tool_calls")

        # Post-processing (billing, logging, history)
        if not is_anonymous and user:
            await self._handle_post_processing(
                user=user,
                api_key=api_key,
                model=original_model,
                provider=provider_name,
                messages=messages,
                content=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=cost,
                elapsed=elapsed,
                is_trial=is_trial,
                environment_tag=environment_tag,
                session_id=session_id,
                request_id=request_id,
                finish_reason=finish_reason
            )

        # Return unified response format
        return {
            "id": processed.get("id", f"chatcmpl-{request_id[:8]}"),
            "created": processed.get("created", int(time.time())),
            "model": original_model,
            "content": content,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens)
            },
            "gateway_usage": {
                "tokens_charged": int(total_tokens),
                "cost_usd": cost if not is_trial else 0.0,
                "backend_processing_ms": int(elapsed * 1000),
                "provider": provider_name,
                "request_id": request_id
            },
            "tool_calls": tool_calls
        }

    async def _handle_streaming_request(
        self,
        provider_config,
        provider_name: str,
        messages: list[dict[str, Any]],
        model: str,
        original_model: str,
        user: dict[str, Any] | None,
        api_key: str | None,
        is_trial: bool,
        is_anonymous: bool,
        environment_tag: str,
        session_id: int | None,
        request_id: str,
        **optional_params
    ) -> AsyncGenerator:
        """
        Handle streaming request.

        Returns:
            AsyncGenerator that yields SSE chunks
        """

        # Check if provider supports async streaming
        is_async_stream = False

        if hasattr(provider_config, 'supports_async_streaming') and provider_config.supports_async_streaming:
            if hasattr(provider_config, 'make_request_stream_async'):
                try:
                    stream = await provider_config.make_request_stream_async(
                        messages,
                        model,
                        **optional_params
                    )
                    is_async_stream = True
                    self.logger.debug(f"[{request_id}] Using async streaming")
                except Exception as async_err:
                    self.logger.warning(
                        f"[{request_id}] Async streaming failed, falling back to sync: {async_err}"
                    )
                    stream = await _to_thread(
                        provider_config.make_request_stream,
                        messages,
                        model,
                        **optional_params
                    )
            else:
                stream = await _to_thread(
                    provider_config.make_request_stream,
                    messages,
                    model,
                    **optional_params
                )
        else:
            # Use sync streaming
            stream = await _to_thread(
                provider_config.make_request_stream,
                messages,
                model,
                **optional_params
            )

        # Return streaming generator
        return self._stream_generator(
            stream=stream,
            is_async_stream=is_async_stream,
            provider=provider_name,
            model=original_model,
            user=user,
            api_key=api_key,
            messages=messages,
            is_trial=is_trial,
            is_anonymous=is_anonymous,
            environment_tag=environment_tag,
            session_id=session_id,
            request_id=request_id
        )

    async def _stream_generator(
        self,
        stream,
        is_async_stream: bool,
        provider: str,
        model: str,
        user: dict[str, Any] | None,
        api_key: str | None,
        messages: list[dict[str, Any]],
        is_trial: bool,
        is_anonymous: bool,
        environment_tag: str,
        session_id: int | None,
        request_id: str
    ) -> AsyncGenerator:
        """
        Generate SSE stream from provider stream.

        Handles:
        - Chunk normalization
        - Token counting
        - Background post-processing
        """

        accumulated_content = ""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        start_time = time.monotonic()

        try:
            # Iterate stream (async or sync)
            async def iterate_stream():
                """Helper to support both sync and async iteration"""
                if is_async_stream:
                    async for chunk in stream:
                        yield chunk
                else:
                    # Sentinel for StopIteration
                    _STREAM_EXHAUSTED = object()

                    def _safe_next(iterator):
                        try:
                            return next(iterator)
                        except StopIteration:
                            return _STREAM_EXHAUSTED

                    iterator = iter(stream)
                    while True:
                        chunk = await asyncio.to_thread(_safe_next, iterator)
                        if chunk is _STREAM_EXHAUSTED:
                            break
                        yield chunk

            async for chunk in iterate_stream():
                # Convert chunk to SSE format
                chunk_str = self._format_chunk_to_sse(chunk, model)
                if chunk_str:
                    # Accumulate content
                    if hasattr(chunk, 'choices') and chunk.choices:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            accumulated_content += delta.content

                    # Extract usage if present
                    if hasattr(chunk, 'usage') and chunk.usage:
                        prompt_tokens = getattr(chunk.usage, 'prompt_tokens', 0)
                        completion_tokens = getattr(chunk.usage, 'completion_tokens', 0)
                        total_tokens = getattr(chunk.usage, 'total_tokens', 0)

                    yield chunk_str

            # Send [DONE]
            yield "data: [DONE]\n\n"

            # Estimate tokens if not provided
            if total_tokens == 0:
                estimated_prompt = estimate_message_tokens(messages, model)
                prompt_tokens = estimated_prompt
                completion_tokens = len(accumulated_content.split()) * 1.3  # Rough estimate
                total_tokens = int(prompt_tokens + completion_tokens)

            elapsed = time.monotonic() - start_time
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

            # Schedule background post-processing
            if not is_anonymous and user:
                asyncio.create_task(
                    self._handle_post_processing(
                        user=user,
                        api_key=api_key,
                        model=model,
                        provider=provider,
                        messages=messages,
                        content=accumulated_content,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        cost=cost,
                        elapsed=elapsed,
                        is_trial=is_trial,
                        environment_tag=environment_tag,
                        session_id=session_id,
                        request_id=request_id,
                        finish_reason="stop"
                    )
                )

        except Exception as e:
            self.logger.error(f"[{request_id}] Streaming error: {e}", exc_info=True)
            error_chunk = {
                "error": {
                    "message": str(e),
                    "type": "stream_error"
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    def _format_chunk_to_sse(self, chunk, model: str) -> str:
        """Format streaming chunk to SSE format"""
        try:
            # Convert chunk to dict
            if hasattr(chunk, 'model_dump'):
                chunk_dict = chunk.model_dump()
            elif hasattr(chunk, 'dict'):
                chunk_dict = chunk.dict()
            elif isinstance(chunk, dict):
                chunk_dict = chunk
            else:
                chunk_dict = {"content": str(chunk)}

            # Format as SSE
            return f"data: {json.dumps(chunk_dict)}\n\n"
        except Exception as e:
            self.logger.warning(f"Failed to format chunk: {e}")
            return ""

    async def _handle_post_processing(
        self,
        user: dict[str, Any],
        api_key: str,
        model: str,
        provider: str,
        messages: list[dict[str, Any]],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost: float,
        elapsed: float,
        is_trial: bool,
        environment_tag: str,
        session_id: int | None,
        request_id: str,
        finish_reason: str
    ):
        """
        Handle post-request processing: billing, logging, history.

        This runs for authenticated users after successful response.
        """

        # 1. Billing
        if is_trial:
            # Trial: Log transaction but don't deduct credits
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
                    True
                )
            except Exception as e:
                self.logger.error(f"[{request_id}] Failed to log trial transaction: {e}")

            # Track trial usage
            try:
                await _to_thread(track_trial_usage, api_key, total_tokens, 1)
            except Exception as e:
                self.logger.warning(f"[{request_id}] Failed to track trial usage: {e}")

        else:
            # Paid: Deduct credits
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
                    }
                )

                # Record usage
                await _to_thread(
                    record_usage,
                    user["id"],
                    api_key,
                    model,
                    total_tokens,
                    cost,
                    int(elapsed * 1000)
                )

                # Update rate limit usage
                await _to_thread(update_rate_limit_usage, api_key, total_tokens)

            except Exception as e:
                self.logger.error(f"[{request_id}] Billing error: {e}", exc_info=True)

        # 2. Increment API key usage counter
        try:
            await _to_thread(increment_api_key_usage, api_key)
        except Exception as e:
            self.logger.warning(f"[{request_id}] Failed to increment API key usage: {e}")

        # 3. Activity Logging
        try:
            provider_name = get_provider_from_model(model)
            speed = total_tokens / elapsed if elapsed > 0 else 0

            await _to_thread(
                log_activity,
                user_id=user["id"],
                model=model,
                provider=provider_name,
                tokens=total_tokens,
                cost=cost if not is_trial else 0.0,
                speed=speed,
                finish_reason=finish_reason,
                app="API",
                metadata={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": "/v1/chat",
                    "session_id": session_id,
                    "gateway": provider,
                    "request_id": request_id
                }
            )
        except Exception as e:
            self.logger.error(f"[{request_id}] Failed to log activity: {e}")

        # 4. Chat History
        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session:
                    # Save user message
                    last_user_msg = None
                    for m in reversed(messages):
                        if m.get("role") == "user":
                            last_user_msg = m
                            break

                    if last_user_msg:
                        user_content = last_user_msg.get("content", "")
                        if isinstance(user_content, list):
                            # Multimodal content
                            text_parts = [
                                item.get("text", "")
                                for item in user_content
                                if isinstance(item, dict) and item.get("type") == "text"
                            ]
                            user_content = " ".join(text_parts) if text_parts else "[multimodal]"

                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "user",
                            user_content,
                            model,
                            0,
                            user["id"]
                        )

                    # Save assistant message
                    if content:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "assistant",
                            content,
                            model,
                            total_tokens,
                            user["id"]
                        )

            except Exception as e:
                self.logger.error(f"[{request_id}] Failed to save chat history: {e}")
