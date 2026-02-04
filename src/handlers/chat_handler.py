"""
Chat Inference Handler - Unified handler for all chat endpoints.

This handler provides a single implementation of chat completion logic
that is used by all chat endpoints (OpenAI, Anthropic, AI SDK, etc.).
It handles provider routing, cost calculation, credit deduction, and logging.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from src.db.chat_completion_requests import save_chat_completion_request
from src.db.users import deduct_credits, get_user, record_usage
from src.schemas.internal.chat import (
    InternalChatRequest,
    InternalChatResponse,
    InternalStreamChunk,
    InternalUsage,
)
from src.services.pricing import calculate_cost
from src.services.provider_selector import get_selector
from src.services.trial_validation import track_trial_usage, validate_trial_access
from src.services.credit_precheck import estimate_and_check_credits

# Provider client imports
from src.services.openrouter_client import (
    make_openrouter_request_openai,
    make_openrouter_request_openai_stream_async,
)
from src.services.cerebras_client import (
    make_cerebras_request_openai,
    make_cerebras_request_openai_stream,
)
from src.services.groq_client import (
    make_groq_request_openai,
    make_groq_request_openai_stream,
)
from src.services.onerouter_client import (
    make_onerouter_request_openai_stream,
)

logger = logging.getLogger(__name__)


class ChatInferenceHandler:
    """
    Unified handler for chat inference across all endpoints.

    This handler implements the complete chat inference pipeline:
    1. Model transformation and provider selection
    2. Provider API calls (with failover)
    3. Token usage extraction
    4. Cost calculation
    5. Credit deduction (with trial support)
    6. Transaction logging
    7. Request metadata persistence

    All chat endpoints (OpenAI, Anthropic, AI SDK) use this same handler.
    """

    def __init__(self, api_key: str, background_tasks: Optional[Any] = None):
        """
        Initialize the handler with user context.

        Args:
            api_key: User's API key for authentication and billing
            background_tasks: FastAPI BackgroundTasks for async operations
        """
        self.api_key = api_key
        self.background_tasks = background_tasks
        self.user: Optional[Dict[str, Any]] = None
        self.trial: Optional[Dict[str, Any]] = None
        self.request_id = str(uuid.uuid4())
        self.start_time = time.monotonic()

        logger.debug(f"[ChatHandler] Initialized with request_id={self.request_id}")

    async def _initialize_user_context(self) -> None:
        """
        Load user and trial data with detailed error handling.

        Raises:
            HTTPException: With detailed error response if authentication or authorization fails
        """
        from fastapi import HTTPException
        from src.utils.error_factory import DetailedErrorFactory

        # Get user
        try:
            self.user = await asyncio.to_thread(get_user, self.api_key)
            if not self.user:
                # Invalid API key - return detailed error
                error_response = DetailedErrorFactory.invalid_api_key(
                    request_id=self.request_id
                )
                raise HTTPException(
                    status_code=error_response.error.status,
                    detail=error_response.dict(exclude_none=True)
                )
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            # Unexpected error during user lookup
            logger.error(f"[ChatHandler] Error fetching user: {e}", exc_info=True)
            error_response = DetailedErrorFactory.internal_error(
                operation="user_lookup",
                error=e,
                request_id=self.request_id,
            )
            raise HTTPException(
                status_code=error_response.error.status,
                detail=error_response.dict(exclude_none=True)
            )

        # Validate trial access
        try:
            self.trial = await asyncio.to_thread(validate_trial_access, self.api_key)
            if not self.trial.get("is_valid", False):
                # Trial validation failed - determine error type
                trial_error = self.trial.get("error", "Access denied")

                # Check if it's a trial expired error
                if "trial" in trial_error.lower() and "expired" in trial_error.lower():
                    error_response = DetailedErrorFactory.trial_expired(
                        request_id=self.request_id
                    )
                else:
                    # Generic authorization error
                    error_response = DetailedErrorFactory.invalid_api_key(
                        reason=trial_error,
                        request_id=self.request_id,
                    )

                raise HTTPException(
                    status_code=error_response.error.status,
                    detail=error_response.dict(exclude_none=True)
                )
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            # Unexpected error during trial validation
            logger.error(f"[ChatHandler] Error validating trial: {e}", exc_info=True)
            error_response = DetailedErrorFactory.internal_error(
                operation="trial_validation",
                error=e,
                request_id=self.request_id,
            )
            raise HTTPException(
                status_code=error_response.error.status,
                detail=error_response.dict(exclude_none=True)
            )

        logger.debug(
            f"[ChatHandler] User context loaded: user_id={self.user.get('id')}, "
            f"is_trial={self.trial.get('is_trial')}"
        )

    async def _check_credit_sufficiency(
        self,
        model_id: str,
        messages: list[dict],
        max_tokens: Optional[int],
    ) -> None:
        """
        Pre-flight credit check: verify user has sufficient credits for maximum possible cost.

        This follows OpenAI's model:
        1. Estimate input tokens from messages
        2. Use max_tokens for maximum output
        3. Calculate maximum possible cost
        4. Verify user has sufficient credits

        Args:
            model_id: Model to be used
            messages: Chat messages
            max_tokens: Maximum output tokens (from request)

        Raises:
            HTTPException: 402 Payment Required if insufficient credits
        """
        from fastapi import HTTPException

        # Trial users don't need credit checks
        if self.trial.get("is_trial", False):
            logger.debug("[ChatHandler] Skipping credit check for trial user")
            return

        # Get user's current credits
        user_credits = self.user.get("credits", 0.0)

        # Perform pre-flight check
        check_result = estimate_and_check_credits(
            model_id=model_id,
            messages=messages,
            user_credits=user_credits,
            max_tokens=max_tokens,
            is_trial=False,
        )

        if not check_result["allowed"]:
            # Insufficient credits - reject before provider call
            from src.utils.exceptions import APIExceptions

            max_cost = check_result["max_cost"]
            max_output_tokens = check_result["max_output_tokens"]
            input_tokens = check_result.get("input_tokens", 0)

            logger.warning(
                f"[ChatHandler] Insufficient credits for user {self.user.get('id')}: "
                f"need ${max_cost:.4f}, have ${user_credits:.4f}"
            )

            # Use detailed error with actionable suggestions
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=user_credits,
                max_cost=max_cost,
                model_id=model_id,
                max_tokens=max_output_tokens,
                input_tokens=input_tokens,
                request_id=self.request_id,
            )

        # Log successful check
        logger.info(
            f"[ChatHandler] Credit pre-check passed: max_cost=${check_result['max_cost']:.4f}, "
            f"available=${user_credits:.4f}"
        )

    def _call_provider(
        self,
        provider_name: str,
        model_id: str,
        messages: list,
        **kwargs,
    ) -> Any:
        """
        Route request to the appropriate provider client with detailed error handling.

        Args:
            provider_name: Provider to use (e.g., "openrouter", "cerebras", "groq")
            model_id: Model identifier to use
            messages: Chat messages in OpenAI format
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Provider response object

        Raises:
            HTTPException: With detailed error response if provider call fails
        """
        from fastapi import HTTPException
        from src.utils.error_factory import DetailedErrorFactory

        logger.info(f"[ChatHandler] Calling provider={provider_name}, model={model_id}")

        try:
            # Route to appropriate provider
            if provider_name == "openrouter":
                return make_openrouter_request_openai(messages, model_id, **kwargs)
            elif provider_name == "cerebras":
                return make_cerebras_request_openai(messages, model_id, **kwargs)
            elif provider_name == "groq":
                return make_groq_request_openai(messages, model_id, **kwargs)
            else:
                # Fallback to OpenRouter for unknown providers
                logger.warning(
                    f"[ChatHandler] Unknown provider {provider_name}, falling back to OpenRouter"
                )
                return make_openrouter_request_openai(messages, model_id, **kwargs)
        except HTTPException:
            # Re-raise HTTP exceptions (already formatted)
            raise
        except Exception as e:
            # Convert provider exceptions to detailed errors
            logger.error(
                f"[ChatHandler] Provider error: provider={provider_name}, model={model_id}, error={e}",
                exc_info=True
            )

            # Create detailed provider error
            error_response = DetailedErrorFactory.provider_error(
                provider=provider_name,
                model=model_id,
                provider_message=str(e),
                status_code=502,
                request_id=self.request_id,
            )

            raise HTTPException(
                status_code=error_response.error.status,
                detail=error_response.dict(exclude_none=True)
            )

    async def _call_provider_stream(
        self,
        provider_name: str,
        model_id: str,
        messages: list,
        **kwargs,
    ) -> AsyncIterator[Any]:
        """
        Route streaming request to the appropriate provider client.

        Args:
            provider_name: Provider to use (e.g., "openrouter", "cerebras", "groq", "onerouter")
            model_id: Model identifier to use
            messages: Chat messages in OpenAI format
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Yields:
            Provider stream chunks

        Raises:
            ValueError: If provider is not supported
            Exception: Provider-specific errors
        """
        logger.info(
            f"[ChatHandler] Calling provider={provider_name} (streaming), model={model_id}"
        )

        # Sentinel value to signal iterator exhaustion (PEP 479 compliance)
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

        async def _iterate_sync_stream(sync_stream):
            """Non-blocking iteration over sync streams using asyncio.to_thread.

            This prevents blocking the event loop while waiting for chunks from
            providers that use synchronous HTTP clients.
            """
            iterator = iter(sync_stream)
            while True:
                chunk = await asyncio.to_thread(_safe_next, iterator)
                if chunk is _STREAM_EXHAUSTED:
                    break
                yield chunk

        # Route to appropriate provider
        if provider_name == "openrouter":
            stream = await make_openrouter_request_openai_stream_async(
                messages, model_id, **kwargs
            )
            async for chunk in stream:
                yield chunk
        elif provider_name == "onerouter":
            # OneRouter/Infron.ai uses sync client - use non-blocking iteration
            stream = make_onerouter_request_openai_stream(messages, model_id, **kwargs)
            async for chunk in _iterate_sync_stream(stream):
                yield chunk
        elif provider_name == "cerebras":
            # Cerebras uses sync client - use non-blocking iteration
            stream = make_cerebras_request_openai_stream(messages, model_id, **kwargs)
            async for chunk in _iterate_sync_stream(stream):
                yield chunk
        elif provider_name == "groq":
            # Groq uses sync client - use non-blocking iteration
            stream = make_groq_request_openai_stream(messages, model_id, **kwargs)
            async for chunk in _iterate_sync_stream(stream):
                yield chunk
        else:
            # Fallback to OpenRouter
            logger.warning(
                f"[ChatHandler] Unknown provider {provider_name}, falling back to OpenRouter"
            )
            stream = await make_openrouter_request_openai_stream_async(
                messages, model_id, **kwargs
            )
            async for chunk in stream:
                yield chunk

    async def _charge_user(
        self,
        cost: float,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """
        Charge user credits or track trial usage.

        Args:
            cost: Cost in USD to charge
            model_name: Model used
            prompt_tokens: Input tokens
            completion_tokens: Output tokens

        Raises:
            Exception: If credit deduction fails
        """
        total_tokens = prompt_tokens + completion_tokens
        is_trial = self.trial.get("is_trial", False)

        # Override trial status if user has active subscription (defense-in-depth)
        if is_trial and self.user:
            has_active_subscription = (
                self.user.get("stripe_subscription_id") is not None
                and self.user.get("subscription_status") == "active"
            ) or self.user.get("tier") in ("pro", "max", "admin")

            if has_active_subscription:
                logger.warning(
                    f"[ChatHandler] User {self.user.get('id')} has is_trial=TRUE "
                    f"but has active subscription. Forcing paid path."
                )
                is_trial = False

        # Track trial usage
        if is_trial and not self.trial.get("is_expired"):
            try:
                await asyncio.to_thread(
                    track_trial_usage,
                    self.api_key,
                    total_tokens,
                    1,  # request count
                    model_id=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                logger.debug(f"[ChatHandler] Tracked trial usage: {total_tokens} tokens")
            except Exception as e:
                logger.warning(f"[ChatHandler] Failed to track trial usage: {e}")

        # Deduct credits for non-trial users
        if not is_trial:
            try:
                await asyncio.to_thread(
                    deduct_credits,
                    self.api_key,
                    cost,
                    f"Chat completion - {model_name}",
                    {
                        "model": model_name,
                        "total_tokens": total_tokens,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_usd": cost,
                    },
                )

                # Record usage for analytics
                elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
                await asyncio.to_thread(
                    record_usage,
                    self.user["id"],
                    self.api_key,
                    model_name,
                    total_tokens,
                    cost,
                    elapsed_ms,
                )

                logger.debug(
                    f"[ChatHandler] Charged ${cost:.6f} for {total_tokens} tokens "
                    f"(user_id={self.user.get('id')})"
                )
            except Exception as e:
                logger.error(f"[ChatHandler] Credit deduction failed: {e}")
                raise

    def _save_request_record(
        self,
        model_name: str,
        provider_name: str,
        input_tokens: int,
        output_tokens: int,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> None:
        """
        Log request metadata to chat_completion_requests table.

        Args:
            model_name: Model used
            provider_name: Provider used
            input_tokens: Prompt tokens
            output_tokens: Completion tokens
            status: Request status (completed, failed, partial)
            error_message: Error message if failed
        """
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)

        # Save as background task if available
        if self.background_tasks:
            self.background_tasks.add_task(
                save_chat_completion_request,
                request_id=self.request_id,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                processing_time_ms=elapsed_ms,
                status=status,
                error_message=error_message,
                user_id=self.user.get("id") if self.user else None,
                provider_name=provider_name,
                model_id=None,  # Will be looked up in save function
                api_key_id=self.user.get("key_id") if self.user else None,
                is_anonymous=False,
            )
        else:
            # Synchronous save if no background tasks
            save_chat_completion_request(
                request_id=self.request_id,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                processing_time_ms=elapsed_ms,
                status=status,
                error_message=error_message,
                user_id=self.user.get("id") if self.user else None,
                provider_name=provider_name,
                model_id=None,
                api_key_id=self.user.get("key_id") if self.user else None,
                is_anonymous=False,
            )

        logger.debug(
            f"[ChatHandler] Saved request record: request_id={self.request_id}, "
            f"status={status}, tokens={input_tokens}+{output_tokens}"
        )

    async def process(self, request: InternalChatRequest) -> InternalChatResponse:
        """
        Process a non-streaming chat completion request.

        This is the SINGLE implementation used by ALL chat endpoints for
        non-streaming requests. It handles the complete pipeline:
        1. User context initialization
        2. Model transformation
        3. Provider selection and API call
        4. Token usage extraction
        5. Cost calculation
        6. Credit deduction
        7. Request logging
        8. Response formatting

        Args:
            request: Internal chat request (already converted from external format)

        Returns:
            InternalChatResponse with all metadata populated

        Raises:
            ValueError: If user is invalid or has insufficient credits
            Exception: Provider or system errors
        """
        try:
            # Step 1: Initialize user context
            await self._initialize_user_context()

            logger.info(
                f"[ChatHandler] Processing request: model={request.model}, "
                f"messages={len(request.messages)}, user_id={self.user.get('id')}"
            )

            # Step 1.5: Convert internal messages to OpenAI format for provider clients
            messages = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    **({"name": msg.name} if msg.name else {}),
                    **({"tool_call_id": msg.tool_call_id} if msg.tool_call_id else {}),
                    **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
                }
                for msg in request.messages
            ]

            # Step 1.6: Pre-flight credit check (verify user has enough credits for max possible cost)
            await self._check_credit_sufficiency(
                model_id=request.model,
                messages=messages,
                max_tokens=request.max_tokens,
            )

            # Step 2: Build provider kwargs
            kwargs = {
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "top_p": request.top_p,
                "frequency_penalty": request.frequency_penalty,
                "presence_penalty": request.presence_penalty,
                "stop": request.stop,
                "tools": request.tools,
                "tool_choice": request.tool_choice,
                "response_format": request.response_format,
                "user": request.user,
            }
            # Remove None values
            kwargs = {k: v for k, v in kwargs.items() if v is not None}

            # Try using provider selector for multi-provider models
            selector = get_selector()

            # Check if model is in multi-provider registry
            model_in_registry = selector.registry.get_model(request.model) is not None

            if model_in_registry:
                # Use intelligent routing with failover for multi-provider models
                result = await asyncio.to_thread(
                    selector.execute_with_failover,
                    model_id=request.model,
                    execute_fn=lambda provider_name, provider_model_id: self._call_provider(
                        provider_name, provider_model_id, messages, **kwargs
                    ),
                )

                if not result["success"]:
                    error_msg = result.get("error", "All providers failed")
                    logger.error(f"[ChatHandler] All providers failed: {error_msg}")
                    # Save failed request
                    self._save_request_record(
                        model_name=request.model,
                        provider_name="unknown",
                        input_tokens=0,
                        output_tokens=0,
                        status="failed",
                        error_message=error_msg,
                    )
                    raise Exception(error_msg)

                # Extract provider response
                provider_response = result["response"]
                provider_used = result["provider"]
                provider_model_id = result.get("provider_model_id", request.model)
            else:
                # Fallback to OpenRouter for models not in multi-provider registry
                logger.info(
                    f"[ChatHandler] Model {request.model} not in registry, using OpenRouter fallback"
                )
                provider_used = "openrouter"
                provider_model_id = request.model
                provider_response = self._call_provider(
                    provider_used, provider_model_id, messages, **kwargs
                )

            logger.info(
                f"[ChatHandler] Provider call successful: provider={provider_used}, "
                f"model={provider_model_id}"
            )

            # Step 4: Extract token usage from response
            usage = getattr(provider_response, "usage", None)
            if not usage:
                raise ValueError("Provider response missing usage data")

            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            total_tokens = prompt_tokens + completion_tokens

            # Extract response content
            if hasattr(provider_response, "choices") and provider_response.choices:
                choice = provider_response.choices[0]
                message = getattr(choice, "message", None)
                if not message:
                    raise ValueError("Provider response missing message")

                content = getattr(message, "content", "")
                finish_reason = getattr(choice, "finish_reason", "stop")
                tool_calls = getattr(message, "tool_calls", None)
            else:
                raise ValueError("Provider response missing choices")

            # Step 5: Calculate cost
            cost = await asyncio.to_thread(
                calculate_cost, request.model, prompt_tokens, completion_tokens
            )
            input_cost = await asyncio.to_thread(calculate_cost, request.model, prompt_tokens, 0)
            output_cost = await asyncio.to_thread(calculate_cost, request.model, 0, completion_tokens)

            logger.debug(
                f"[ChatHandler] Cost calculation: total=${cost:.6f}, "
                f"input=${input_cost:.6f}, output=${output_cost:.6f}"
            )

            # Step 6: Charge user
            await self._charge_user(cost, request.model, prompt_tokens, completion_tokens)

            # Step 7: Save request record
            self._save_request_record(
                model_name=request.model,
                provider_name=provider_used,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                status="completed",
            )

            # Step 8: Return InternalChatResponse with all metadata
            elapsed_ms = int((time.monotonic() - self.start_time) * 1000)

            response = InternalChatResponse(
                id=self.request_id,
                model=request.model,
                content=content or "",
                finish_reason=finish_reason,
                usage=InternalUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                ),
                cost_usd=cost,
                input_cost_usd=input_cost,
                output_cost_usd=output_cost,
                provider_used=provider_used,
                processing_time_ms=elapsed_ms,
                tool_calls=tool_calls,
            )

            logger.info(
                f"[ChatHandler] Request completed successfully: "
                f"tokens={total_tokens}, cost=${cost:.6f}, time={elapsed_ms}ms"
            )

            return response

        except Exception as e:
            # Log error and save failed request
            logger.error(f"[ChatHandler] Request failed: {e}", exc_info=True)

            # Save failed request metadata
            self._save_request_record(
                model_name=request.model,
                provider_name="unknown",
                input_tokens=0,
                output_tokens=0,
                status="failed",
                error_message=str(e),
            )

            raise

    async def process_stream(
        self, request: InternalChatRequest
    ) -> AsyncIterator[InternalStreamChunk]:
        """
        Process a streaming chat completion request.

        This is the SINGLE implementation used by ALL chat endpoints for
        streaming requests. It handles:
        1. User context initialization
        2. Model transformation and provider selection
        3. Streaming from provider
        4. Yielding normalized internal chunks
        5. Token tracking during stream
        6. Post-stream cost calculation and charging
        7. Request logging

        Args:
            request: Internal chat request with stream=True

        Yields:
            InternalStreamChunk objects as they arrive from provider

        Raises:
            ValueError: If user is invalid or has insufficient credits
            Exception: Provider or system errors
        """
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = None
        provider_used = None

        try:
            # Step 1: Initialize user context
            await self._initialize_user_context()

            logger.info(
                f"[ChatHandler] Processing streaming request: model={request.model}, "
                f"messages={len(request.messages)}, user_id={self.user.get('id')}"
            )

            # Step 2: Prepare messages and kwargs (provider selector handles model transformation)
            messages = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    **({"name": msg.name} if msg.name else {}),
                    **({"tool_call_id": msg.tool_call_id} if msg.tool_call_id else {}),
                    **({"tool_calls": msg.tool_calls} if msg.tool_calls else {}),
                }
                for msg in request.messages
            ]

            # Step 2.5: Pre-flight credit check (streaming)
            await self._check_credit_sufficiency(
                model_id=request.model,
                messages=messages,
                max_tokens=request.max_tokens,
            )

            kwargs = {
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "top_p": request.top_p,
                "frequency_penalty": request.frequency_penalty,
                "presence_penalty": request.presence_penalty,
                "stop": request.stop,
                "tools": request.tools,
                "tool_choice": request.tool_choice,
                "response_format": request.response_format,
                "user": request.user,
            }
            kwargs = {k: v for k, v in kwargs.items() if v is not None}

            # Check if model is in multi-provider registry
            selector = get_selector()
            model_in_registry = selector.registry.get_model(request.model) is not None

            if model_in_registry:
                # Use multi-provider routing for models in registry
                primary_provider = await asyncio.to_thread(
                    selector.registry.select_provider, request.model
                )

                if not primary_provider:
                    raise ValueError(f"No provider found for model {request.model}")

                provider_used = primary_provider.name
                provider_model_id = primary_provider.model_id
            else:
                # Fallback to OpenRouter for models not in registry
                logger.info(
                    f"[ChatHandler] Model {request.model} not in registry, using OpenRouter fallback (streaming)"
                )
                provider_used = "openrouter"
                provider_model_id = request.model

            logger.info(
                f"[ChatHandler] Streaming from provider={provider_used}, model={provider_model_id}"
            )

            # Step 3: Stream from provider
            stream = self._call_provider_stream(
                provider_used, provider_model_id, messages, **kwargs
            )

            # Step 5: Yield normalized chunks
            accumulated_content = ""  # For token estimation fallback
            chunk_count = 0

            async for provider_chunk in stream:
                chunk_count += 1

                # Extract delta content
                if hasattr(provider_chunk, "choices") and provider_chunk.choices:
                    choice = provider_chunk.choices[0]
                    delta = getattr(choice, "delta", None)

                    if delta:
                        content = getattr(delta, "content", None)
                        role = getattr(delta, "role", None)
                        tool_calls = getattr(delta, "tool_calls", None)
                        chunk_finish_reason = getattr(choice, "finish_reason", None)

                        if content:
                            accumulated_content += content

                        if chunk_finish_reason:
                            finish_reason = chunk_finish_reason

                        # Extract usage from final chunk if available
                        if hasattr(provider_chunk, "usage") and provider_chunk.usage:
                            prompt_tokens = getattr(provider_chunk.usage, "prompt_tokens", 0)
                            completion_tokens = getattr(
                                provider_chunk.usage, "completion_tokens", 0
                            )

                        # Yield internal chunk
                        internal_chunk = InternalStreamChunk(
                            id=self.request_id,
                            model=request.model,
                            created=int(time.time()),
                            content=content,
                            role=role,
                            finish_reason=chunk_finish_reason,
                            tool_calls=tool_calls,
                            usage=(
                                InternalUsage(
                                    prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens,
                                    total_tokens=prompt_tokens + completion_tokens,
                                )
                                if prompt_tokens > 0 or completion_tokens > 0
                                else None
                            ),
                        )

                        yield internal_chunk

            logger.debug(f"[ChatHandler] Streamed {chunk_count} chunks")

            # Step 6: Token estimation fallback if provider didn't provide usage
            if prompt_tokens == 0 and completion_tokens == 0:
                # Estimate tokens from content length (1 token ≈ 4 characters)
                completion_tokens = max(1, len(accumulated_content) // 4)
                prompt_chars = sum(
                    len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                    for m in messages
                )
                prompt_tokens = max(1, prompt_chars // 4)

                logger.info(
                    f"[ChatHandler] No usage data from provider, estimated "
                    f"{prompt_tokens} prompt + {completion_tokens} completion tokens"
                )

            # Step 7: Calculate cost and charge user after stream completes
            cost = await asyncio.to_thread(
                calculate_cost, request.model, prompt_tokens, completion_tokens
            )

            logger.debug(f"[ChatHandler] Streaming cost: ${cost:.6f}")

            await self._charge_user(cost, request.model, prompt_tokens, completion_tokens)

            # Step 8: Save request record
            self._save_request_record(
                model_name=request.model,
                provider_name=provider_used,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                status="completed",
            )

            logger.info(
                f"[ChatHandler] Streaming request completed: "
                f"tokens={prompt_tokens + completion_tokens}, cost=${cost:.6f}"
            )

        except Exception as e:
            # Log error and save failed request
            logger.error(f"[ChatHandler] Streaming request failed: {e}", exc_info=True)

            # Save failed request metadata
            self._save_request_record(
                model_name=request.model,
                provider_name=provider_used or "unknown",
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                status="failed",
                error_message=str(e),
            )

            raise
