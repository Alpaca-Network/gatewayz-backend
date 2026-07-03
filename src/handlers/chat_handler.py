"""
Chat Inference Handler - Unified handler for all chat endpoints.

This handler provides a single implementation of chat completion logic
that is used by all chat endpoints (OpenAI, Anthropic, AI SDK, etc.).
It handles provider routing, cost calculation, credit deduction, and logging.
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import Request

from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
from src.db.users import deduct_credits, get_user, record_usage
from src.schemas.internal.chat import (
    InternalChatRequest,
    InternalChatResponse,
    InternalStreamChunk,
    InternalUsage,
)
from src.services.circuit_breaker import CircuitBreakerError
from src.services.credit_precheck import estimate_and_check_credits

# Providers with native async streaming use direct imports (currently OpenRouter).
# All other providers are dispatched via PROVIDER_ROUTING (lazy-imported to avoid
# circular deps with chat.py which imports this module).
from src.services.providers.openrouter_client import (
    make_openrouter_request_openai,
    make_openrouter_request_openai_stream_async,
)
from src.services.pricing import calculate_cost, calculate_cost_split, get_model_pricing
from src.services.provider_selector import get_selector

logger = logging.getLogger(__name__)


def _rfield(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a provider response that may be an OpenAI-SDK object OR a plain dict.

    Most provider clients return OpenAI-SDK objects (attribute access), but some —
    notably Google Vertex — return OpenAI-*shaped* dicts (``{"choices": [...]}``).
    This handler's extraction assumed attribute access only, so dict-returning
    providers raised ``"Provider response missing choices"`` on the authenticated path.
    Reading through this accessor makes extraction work for both shapes.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# FREEZE FIX: Hard ceiling for streaming responses — prevents hung provider connections
# from monopolizing the event loop indefinitely. Configured via MAX_STREAM_DURATION_SECONDS env var.
# Mirrors the same constant in src/routes/chat.py (anonymous user path).
_MAX_STREAM_DURATION = int(os.getenv("MAX_STREAM_DURATION_SECONDS", "300"))


def _loss_proof_cost_split(
    requested_model: str,
    provider_model_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> tuple[float, float, float]:
    """Cost split that never bills below the provider that actually served the request.

    The gateway is cost-plus (PRICING_MARKUP is applied on top of upstream cost).
    Pricing is resolved per canonical ``model_id`` via a ``.limit(1)`` lookup that can
    match an arbitrary provider row for a multi-provider model. When the provider
    selector / failover routes the request to a *different, pricier* provider than the
    one whose price was matched, billing by ``requested_model`` alone can charge below
    what we actually pay upstream — a per-request loss.

    This computes the requested-model cost and, only when the SERVED provider model has
    real (non-default, non-zero) pricing, takes the higher of the two. Effect:
      • never bill below the served provider's actual cost  → no gateway loss
      • never bill below the requested model's advertised rate → no customer under-bill
      • identical to the old behaviour whenever served == requested or the served
        provider has no distinct real price (falls back to requested-model cost).

    NOTE (billing behaviour): during a failover to a pricier provider this charges the
    higher served rate (cost-plus). If the product contract is a flat per-model price
    with the gateway absorbing failover variance, swap this back to a plain
    ``calculate_cost_split(requested_model, ...)`` and instead enforce
    catalog_price >= max(provider cost) in the pricing data.
    """
    base = calculate_cost_split(requested_model, prompt_tokens, completion_tokens)
    if not provider_model_id or provider_model_id == requested_model:
        return base
    try:
        served_pricing = get_model_pricing(provider_model_id)
        has_real_price = served_pricing.get("source", "default") != "default" and (
            float(served_pricing.get("prompt", 0) or 0) > 0
            or float(served_pricing.get("completion", 0) or 0) > 0
        )
        if has_real_price:
            served = calculate_cost_split(provider_model_id, prompt_tokens, completion_tokens)
            if served[0] > base[0]:
                logger.info(
                    "[Pricing] Failover re-price: served provider model %r cost $%.6f "
                    "exceeds requested %r cost $%.6f — billing the higher (cost-plus, no loss).",
                    provider_model_id,
                    served[0],
                    requested_model,
                    base[0],
                )
                return served
    except Exception as e:
        # Any failure (high-value guard, unresolved id, etc.) → keep the safe base cost.
        logger.warning(
            "[Pricing] Failover re-price check failed for %r (kept requested-model cost): %s",
            provider_model_id,
            e,
        )
    return base


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

    def __init__(
        self,
        api_key: str | None,
        background_tasks: Any | None = None,
        request: Request | None = None,
    ):
        """
        Initialize the handler with user context.

        Args:
            api_key: User's API key for authentication and billing, or None for anonymous requests
            background_tasks: FastAPI BackgroundTasks for async operations
            request: FastAPI Request object for disconnect detection
        """
        self.api_key = api_key
        self.is_anonymous = api_key is None
        self.background_tasks = background_tasks
        self.request = request
        self.user: dict[str, Any] | None = None
        self.trial: dict[str, Any] | None = None
        self.request_id = str(uuid.uuid4())
        self.start_time = time.monotonic()

        logger.debug(
            f"[ChatHandler] Initialized with request_id={self.request_id}, anonymous={self.is_anonymous}"
        )

    async def _initialize_user_context(self) -> None:
        """
        Load user and trial data with detailed error handling.

        For anonymous requests, sets user=None and a synthetic trial dict
        so that downstream code can proceed without auth.

        Raises:
            HTTPException: With detailed error response if authentication or authorization fails
        """
        if self.is_anonymous:
            self.user = None
            self.trial = {"is_valid": True, "is_trial": False, "is_anonymous": True}
            logger.debug(f"[ChatHandler] Anonymous request, request_id={self.request_id}")
            return

        from fastapi import HTTPException

        from src.utils.error_factory import DetailedErrorFactory

        # Get user
        try:
            self.user = await asyncio.to_thread(get_user, self.api_key)
            if not self.user:
                # Invalid API key - return detailed error
                error_response = DetailedErrorFactory.invalid_api_key(request_id=self.request_id)
                raise HTTPException(
                    status_code=error_response.error.status,
                    detail=error_response.dict(exclude_none=True),
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
                detail=error_response.dict(exclude_none=True),
            )

        self.trial = {"is_valid": True, "is_trial": False}

        logger.debug(f"[ChatHandler] User context loaded: user_id={self.user.get('id')}")

    async def _check_credit_sufficiency(
        self,
        model_id: str,
        messages: list[dict],
        max_tokens: int | None,
    ) -> int | None:
        """
        Pre-flight credit check with automatic max_tokens capping.

        If the user cannot afford the full ``max_tokens`` but CAN afford
        some output, the method returns a reduced ``max_tokens`` value that
        the caller must use instead.

        Returns:
            Effective max_tokens to use (may be lower than the requested
            value).  ``None`` only when the check is skipped entirely
            (anonymous / trial).

        Raises:
            HTTPException: 402 Payment Required if the user cannot afford
                any output at all.
        """

        # Anonymous requests don't require credits
        if self.is_anonymous:
            logger.debug("[ChatHandler] Skipping credit check for anonymous request")
            return max_tokens

        # Get user's current spendable balance (subscription allowance + purchased credits)
        user_credits = float(self.user.get("subscription_allowance") or 0) + float(
            self.user.get("purchased_credits") or 0
        )

        # Free models cost $0 — always allow regardless of balance
        if model_id and model_id.endswith(":free"):
            logger.debug("[ChatHandler] Skipping credit check for free model %s", model_id)
            return max_tokens

        # Perform pre-flight check (now includes affordability capping)
        check_result = estimate_and_check_credits(
            model_id=model_id,
            messages=messages,
            user_credits=user_credits,
            max_tokens=max_tokens,
            is_trial=False,
        )

        if not check_result["allowed"]:
            # Cannot afford any output — reject before provider call
            from src.utils.exceptions import APIExceptions

            max_cost = check_result["max_cost"]
            max_output_tokens = check_result["max_output_tokens"]
            input_tokens = check_result.get("input_tokens", 0)

            logger.warning(
                f"[ChatHandler] Insufficient credits for user {self.user.get('id')}: "
                f"need ${max_cost:.4f}, have ${user_credits:.4f}"
            )

            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=user_credits,
                max_cost=max_cost,
                model_id=model_id,
                max_tokens=max_output_tokens,
                input_tokens=input_tokens,
                request_id=self.request_id,
            )

        # Check if max_tokens was capped to an affordable limit
        capped = check_result.get("capped_max_tokens")
        if capped is not None:
            logger.warning(
                "[ChatHandler] max_tokens capped for user %s: "
                "original=%s → capped=%d (credits=%.4f, model=%s)",
                self.user.get("id"),
                check_result.get("original_max_tokens"),
                capped,
                user_credits,
                model_id,
            )
            return capped

        # Full budget is affordable — no cap needed
        logger.info(
            f"[ChatHandler] Credit pre-check passed: max_cost=${check_result['max_cost']:.4f}, "
            f"available=${user_credits:.4f}"
        )
        return max_tokens

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

        from src.utils.profiling import tag_wrapper
        from src.utils.error_factory import DetailedErrorFactory

        logger.info(f"[ChatHandler] Calling provider={provider_name}, model={model_id}")

        with tag_wrapper({"provider": provider_name, "model": model_id}):
            try:
                # Route to appropriate provider
                # OpenRouter has native async — check via DB flag with fallback
                _is_openrouter_async = False
                try:
                    from src.services.gateway_registry import get_gateway_registry

                    _reg = get_gateway_registry()
                    _is_openrouter_async = provider_name == "openrouter" and _reg.get(
                        provider_name, {}
                    ).get("async_streaming", False)
                except Exception:
                    _is_openrouter_async = provider_name == "openrouter"

                if _is_openrouter_async:
                    return make_openrouter_request_openai(messages, model_id, **kwargs)

                # Registry-based dispatch for all other providers
                from src.handlers.provider_registry import PROVIDER_ROUTING

                routing = PROVIDER_ROUTING.get(provider_name)
                if routing and routing.get("request"):
                    return routing["request"](messages, model_id, **kwargs)

                # Fallback to OpenRouter for unknown providers
                logger.warning(
                    f"[ChatHandler] Provider '{provider_name}' not in PROVIDER_ROUTING, "
                    f"falling back to OpenRouter"
                )
                return make_openrouter_request_openai(messages, model_id, **kwargs)
            except HTTPException:
                # Re-raise HTTP exceptions (already formatted)
                raise
            except CircuitBreakerError as e:
                # Circuit breaker is open - provider temporarily unavailable
                logger.warning(
                    f"[ChatHandler] Circuit breaker open: provider={e.provider}, "
                    f"state={e.state.value}, model={model_id}"
                )

                # Get circuit breaker state for retry_after calculation
                from src.services.circuit_breaker import get_circuit_breaker

                breaker = get_circuit_breaker(e.provider)
                state_info = breaker.get_state()
                retry_after = state_info.get("seconds_until_retry", 60)

                # Create detailed circuit breaker error
                error_response = DetailedErrorFactory.provider_unavailable(
                    provider=e.provider,
                    model=model_id,
                    retry_after=retry_after,
                    circuit_breaker_state=e.state.value,
                    request_id=self.request_id,
                )

                raise HTTPException(
                    status_code=error_response.error.status,
                    detail=error_response.dict(exclude_none=True),
                )
            except Exception as e:
                # Convert provider exceptions to detailed errors
                logger.error(
                    f"[ChatHandler] Provider error: provider={provider_name}, model={model_id}, error={e}",
                    exc_info=True,
                )

                # Surface upstream rate limits (e.g. OpenRouter free-tier 429) as a
                # retryable 429 with Retry-After instead of a generic 502 — otherwise
                # rate-limited models look "broken" in the model selector.
                from src.services.provider_failover import map_provider_error

                mapped = map_provider_error(provider_name, model_id, e)
                if mapped.status_code == 429:
                    retry_after = None
                    if mapped.headers and "Retry-After" in mapped.headers:
                        try:
                            retry_after = int(mapped.headers["Retry-After"])
                        except (TypeError, ValueError):
                            retry_after = None
                    error_response = DetailedErrorFactory.rate_limit_exceeded(
                        limit_type="provider_rate_limit",
                        retry_after=retry_after,
                        request_id=self.request_id,
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=error_response.dict(exclude_none=True),
                        headers=mapped.headers,
                    ) from e

                # Provider account/key budget exhausted (e.g. an OpenRouter key hitting its
                # weekly spend limit -> upstream 402). This is a gateway-side capacity issue,
                # NOT the user's — show a friendly message, never leak the upstream key/URL,
                # and fire a distinct Sentry alert so the team is notified to top up.
                from src.utils.error_messages import (
                    PROVIDER_CAPACITY_MESSAGE,
                    is_provider_budget_error,
                    sanitize_provider_error_for_user,
                )

                if mapped.status_code == 402 or is_provider_budget_error(str(e)):
                    try:
                        from src.utils.sentry_context import capture_provider_error

                        capture_provider_error(
                            e,
                            provider=provider_name,
                            model=model_id,
                            request_id=self.request_id,
                            endpoint="/v1/chat/completions",
                            extra_context={
                                "error_category": "provider_budget_exhausted",
                                "alert": True,
                            },
                        )
                    except Exception:
                        logger.warning("Failed to capture provider-budget alert", exc_info=True)

                    error_response = DetailedErrorFactory.provider_error(
                        provider=provider_name,
                        model=model_id,
                        provider_message=PROVIDER_CAPACITY_MESSAGE,
                        status_code=503,
                        request_id=self.request_id,
                    )
                    raise HTTPException(
                        status_code=error_response.error.status,
                        detail=error_response.dict(exclude_none=True),
                        headers={"Retry-After": "30"},
                    ) from e

                # Other provider errors keep the existing 502 provider-error contract,
                # sanitized so we never leak upstream URLs/key ids to end users.
                error_response = DetailedErrorFactory.provider_error(
                    provider=provider_name,
                    model=model_id,
                    provider_message=sanitize_provider_error_for_user(str(e)),
                    status_code=502,
                    request_id=self.request_id,
                )

                raise HTTPException(
                    status_code=error_response.error.status,
                    detail=error_response.dict(exclude_none=True),
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
        logger.info(f"[ChatHandler] Calling provider={provider_name} (streaming), model={model_id}")

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

        # Route to appropriate provider with circuit breaker error handling
        from fastapi import HTTPException

        from src.utils.profiling import tag_wrapper
        from src.utils.error_factory import DetailedErrorFactory

        try:
            with tag_wrapper({"provider": provider_name, "model": model_id}):
                # OpenRouter has native async streaming — check via DB flag
                _is_openrouter_async_stream = False
                try:
                    from src.services.gateway_registry import get_gateway_registry

                    _reg = get_gateway_registry()
                    _is_openrouter_async_stream = provider_name == "openrouter" and _reg.get(
                        provider_name, {}
                    ).get("async_streaming", False)
                except Exception:
                    _is_openrouter_async_stream = provider_name == "openrouter"

                if _is_openrouter_async_stream:
                    # OpenRouter supports native async streaming
                    stream = await make_openrouter_request_openai_stream_async(
                        messages, model_id, **kwargs
                    )
                    async for chunk in stream:
                        yield chunk
                else:
                    # Registry-based dispatch for all other providers
                    from src.handlers.provider_registry import PROVIDER_ROUTING

                    routing = PROVIDER_ROUTING.get(provider_name)
                    if routing and routing.get("stream"):
                        # All non-OpenRouter providers use sync streaming clients
                        stream = routing["stream"](messages, model_id, **kwargs)
                        async for chunk in _iterate_sync_stream(stream):
                            yield chunk
                    else:
                        # Fallback to OpenRouter for unknown providers
                        logger.warning(
                            f"[ChatHandler] Provider '{provider_name}' not in PROVIDER_ROUTING, "
                            f"falling back to OpenRouter (streaming)"
                        )
                        stream = await make_openrouter_request_openai_stream_async(
                            messages, model_id, **kwargs
                        )
                        async for chunk in stream:
                            yield chunk
        except CircuitBreakerError as e:
            # Circuit breaker is open - provider temporarily unavailable
            logger.warning(
                f"[ChatHandler] Circuit breaker open (streaming): provider={e.provider}, "
                f"state={e.state.value}, model={model_id}"
            )

            # Get circuit breaker state for retry_after calculation
            from src.services.circuit_breaker import get_circuit_breaker

            breaker = get_circuit_breaker(e.provider)
            state_info = breaker.get_state()
            retry_after = state_info.get("seconds_until_retry", 60)

            # Create detailed circuit breaker error
            error_response = DetailedErrorFactory.provider_unavailable(
                provider=e.provider,
                model=model_id,
                retry_after=retry_after,
                circuit_breaker_state=e.state.value,
                request_id=self.request_id,
            )

            raise HTTPException(
                status_code=error_response.error.status,
                detail=error_response.dict(exclude_none=True),
            )

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
        # Anonymous requests are not charged
        if self.is_anonymous:
            logger.debug("[ChatHandler] Skipping charge for anonymous request")
            return

        total_tokens = prompt_tokens + completion_tokens

        # Deduct credits
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
                    "request_id": self.request_id,
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
        error_message: str | None = None,
        cost_usd: float = 0.0,
        input_cost_usd: float = 0.0,
        output_cost_usd: float = 0.0,
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

        # pricing_source enum (per migration 20260115000001):
        # 'calculated' = computed from model_pricing on a successful billable call
        # 'free'       = legitimate zero-cost call (e.g. OpenRouter :free models)
        # Failed/errored requests inherit the DB default ('calculated') with cost=0.
        if status == "completed":
            pricing_source = "calculated" if cost_usd > 0 else "free"
        else:
            pricing_source = "calculated"  # DB default; tokens/cost will be 0

        save_kwargs = dict(
            request_id=self.request_id,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            processing_time_ms=elapsed_ms,
            cost_usd=cost_usd,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            pricing_source=pricing_source,
            status=status,
            error_message=error_message,
            user_id=self.user.get("id") if self.user else None,
            provider_name=provider_name,
            model_id=None,
            api_key_id=self.user.get("key_id") if self.user else None,
            is_anonymous=self.is_anonymous,
        )

        if self.background_tasks:
            self.background_tasks.add_task(save_chat_completion_request_with_cost, **save_kwargs)
        else:
            save_chat_completion_request_with_cost(**save_kwargs)

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
                f"messages={len(request.messages)}, user_id={self.user.get('id') if self.user else 'anonymous'}"
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

            # Step 1.6: Pre-flight credit check (may cap max_tokens to affordable limit)
            effective_max_tokens = await self._check_credit_sufficiency(
                model_id=request.model,
                messages=messages,
                max_tokens=request.max_tokens,
            )

            # Step 2: Build provider kwargs
            kwargs = {
                "temperature": request.temperature,
                "max_tokens": effective_max_tokens,
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
                # Use provider hint from chat.py (already detected from model ID + catalog)
                # Falls back to detect_provider_from_model_id if no hint
                from src.services.model_transformations import (
                    detect_provider_from_model_id,
                    transform_model_id,
                )

                provider_used = (
                    request.provider or detect_provider_from_model_id(request.model) or "openrouter"
                )
                provider_model_id = transform_model_id(request.model, provider_used)
                logger.info(
                    f"[ChatHandler] Model {request.model} not in registry, "
                    f"using provider='{provider_used}', model_id='{provider_model_id}'"
                )
                provider_response = self._call_provider(
                    provider_used, provider_model_id, messages, **kwargs
                )

            logger.info(
                f"[ChatHandler] Provider call successful: provider={provider_used}, "
                f"model={provider_model_id}"
            )

            # Step 4: Extract token usage from response
            # (_rfield handles both OpenAI-SDK objects and dict-shaped responses, e.g. Vertex.)
            usage = _rfield(provider_response, "usage")
            if usage:
                prompt_tokens = _rfield(usage, "prompt_tokens", 0) or 0
                completion_tokens = _rfield(usage, "completion_tokens", 0) or 0
            else:
                prompt_tokens = 0
                completion_tokens = 0

            # Extract response content
            choices = _rfield(provider_response, "choices")
            if choices:
                choice = choices[0]
                message = _rfield(choice, "message")
                if not message:
                    raise ValueError("Provider response missing message")

                content = _rfield(message, "content", "")
                finish_reason = _rfield(choice, "finish_reason", "stop")
                tool_calls = _rfield(message, "tool_calls")
            else:
                raise ValueError("Provider response missing choices")

            # Token estimation fallback if provider didn't return usage data
            if prompt_tokens == 0 and completion_tokens == 0:
                completion_tokens = max(1, len(content or "") // 4)
                prompt_chars = sum(
                    len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                    for m in messages
                )
                prompt_tokens = max(1, prompt_chars // 4)
                logger.info(
                    f"[ChatHandler] No usage data from provider {provider_used}, estimated "
                    f"{prompt_tokens} prompt + {completion_tokens} completion tokens"
                )

            total_tokens = prompt_tokens + completion_tokens

            # Step 5: Calculate cost (loss-proof: never bill below the served provider)
            cost, input_cost, output_cost = await asyncio.to_thread(
                _loss_proof_cost_split,
                request.model,
                provider_model_id,
                prompt_tokens,
                completion_tokens,
            )

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
                cost_usd=cost,
                input_cost_usd=input_cost,
                output_cost_usd=output_cost,
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
        provider_used = None

        try:
            # Step 1: Initialize user context
            await self._initialize_user_context()

            logger.info(
                f"[ChatHandler] Processing streaming request: model={request.model}, "
                f"messages={len(request.messages)}, user_id={self.user.get('id') if self.user else 'anonymous'}"
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

            # Step 2.5: Pre-flight credit check (streaming, may cap max_tokens)
            effective_max_tokens = await self._check_credit_sufficiency(
                model_id=request.model,
                messages=messages,
                max_tokens=request.max_tokens,
            )

            kwargs = {
                "temperature": request.temperature,
                "max_tokens": effective_max_tokens,
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
                # Use provider hint from chat.py (already detected from model ID + catalog)
                from src.services.model_transformations import (
                    detect_provider_from_model_id,
                    transform_model_id,
                )

                provider_used = (
                    request.provider or detect_provider_from_model_id(request.model) or "openrouter"
                )
                provider_model_id = transform_model_id(request.model, provider_used)
                logger.info(
                    f"[ChatHandler] Model {request.model} not in registry, "
                    f"using provider='{provider_used}', model_id='{provider_model_id}' (streaming)"
                )

            logger.info(
                f"[ChatHandler] Streaming from provider={provider_used}, model={provider_model_id}"
            )

            # Step 3: Stream from provider
            stream = self._call_provider_stream(
                provider_used, provider_model_id, messages, **kwargs
            )

            # Validate stream before iteration
            if stream is None:
                raise ValueError(
                    f"Provider {provider_used} returned None instead of stream for model {provider_model_id}"
                )

            # Step 5: Yield normalized chunks
            accumulated_content = ""  # For token estimation fallback
            chunk_count = 0

            # FREEZE FIX: Set wall-clock deadline before entering the streaming loop.
            # A hung provider that sends headers then goes silent will hold this coroutine
            # (and therefore the entire event loop) indefinitely without this guard.
            _stream_deadline = time.monotonic() + _MAX_STREAM_DURATION

            try:
                async for provider_chunk in stream:
                    # CRITICAL: Check for client disconnect to prevent zombie requests (499)
                    if self.request and await self.request.is_disconnected():
                        logger.warning(
                            f"[ChatHandler] Client disconnected during stream (request_id={self.request_id})"
                        )
                        break

                    # FREEZE FIX: Wall-clock deadline — abort if provider stream exceeds limit.
                    if time.monotonic() > _stream_deadline:
                        _elapsed = time.monotonic() - (_stream_deadline - _MAX_STREAM_DURATION)
                        logger.error(
                            f"[STREAM WATCHDOG] ChatInferenceHandler stream exceeded "
                            f"{_MAX_STREAM_DURATION}s ({_elapsed:.1f}s elapsed) for "
                            f"provider={provider_used}, model={provider_model_id}. Terminating."
                        )
                        break

                    chunk_count += 1

                    # Extract delta content
                    # (_rfield handles both OpenAI-SDK objects and dict-shaped chunks, e.g. Vertex.)
                    choices = _rfield(provider_chunk, "choices")
                    if choices:
                        choice = choices[0]
                        delta = _rfield(choice, "delta")
                        chunk_finish_reason = _rfield(choice, "finish_reason")

                        # Emit when the chunk carries a delta or a terminating finish_reason.
                        # A finish-only chunk has an empty delta ({}) — must not be dropped.
                        if delta is not None or chunk_finish_reason is not None:
                            content = _rfield(delta, "content")
                            role = _rfield(delta, "role")
                            tool_calls = _rfield(delta, "tool_calls")

                            if content:
                                accumulated_content += content

                            # Extract usage from final chunk if available
                            chunk_usage = _rfield(provider_chunk, "usage")
                            if chunk_usage:
                                prompt_tokens = _rfield(chunk_usage, "prompt_tokens", 0) or 0
                                completion_tokens = _rfield(chunk_usage, "completion_tokens", 0) or 0

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

            finally:
                # FREEZE FIX: Always release the underlying provider connection back to the pool.
                # Without this, an aborted/timed-out stream holds the httpx connection open,
                # exhausting the connection pool and eventually freezing new requests.
                try:
                    if hasattr(stream, "aclose"):
                        await stream.aclose()
                    elif hasattr(stream, "close"):
                        stream.close()
                except Exception:
                    pass  # Never let cleanup block the generator teardown

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
            # (loss-proof: never bill below the provider that actually served)
            cost, input_cost, output_cost = await asyncio.to_thread(
                _loss_proof_cost_split,
                request.model,
                provider_model_id,
                prompt_tokens,
                completion_tokens,
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
                cost_usd=cost,
                input_cost_usd=input_cost,
                output_cost_usd=output_cost,
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
