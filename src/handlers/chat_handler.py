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
from src.services.model_transformations import apply_transformations
from src.services.pricing import calculate_cost
from src.services.provider_selector import get_selector
from src.services.trial_validation import track_trial_usage, validate_trial_access

# Provider client imports
from src.services.openrouter_client import (
    make_openrouter_request_openai,
    make_openrouter_request_openai_stream_async,
)
from src.services.cerebras_client import (
    make_cerebras_request,
    make_cerebras_request_stream_async,
)
from src.services.groq_client import (
    make_groq_request,
    make_groq_request_stream_async,
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
        Load user and trial data.

        Raises:
            ValueError: If API key is invalid or trial access is denied
        """
        # Get user
        self.user = await asyncio.to_thread(get_user, self.api_key)
        if not self.user:
            raise ValueError("Invalid API key")

        # Validate trial access
        self.trial = await asyncio.to_thread(validate_trial_access, self.api_key)
        if not self.trial.get("is_valid", False):
            raise ValueError(self.trial.get("error", "Access denied"))

        logger.debug(
            f"[ChatHandler] User context loaded: user_id={self.user.get('id')}, "
            f"is_trial={self.trial.get('is_trial')}"
        )

    def _call_provider(
        self,
        provider_name: str,
        model_id: str,
        messages: list,
        **kwargs,
    ) -> Any:
        """
        Route request to the appropriate provider client.

        Args:
            provider_name: Provider to use (e.g., "openrouter", "cerebras", "groq")
            model_id: Model identifier to use
            messages: Chat messages in OpenAI format
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Provider response object

        Raises:
            ValueError: If provider is not supported
            Exception: Provider-specific errors
        """
        logger.info(f"[ChatHandler] Calling provider={provider_name}, model={model_id}")

        # Route to appropriate provider
        if provider_name == "openrouter":
            return make_openrouter_request_openai(messages, model_id, **kwargs)
        elif provider_name == "cerebras":
            return make_cerebras_request(messages, model_id, **kwargs)
        elif provider_name == "groq":
            return make_groq_request(messages, model_id, **kwargs)
        else:
            # Fallback to OpenRouter for unknown providers
            logger.warning(
                f"[ChatHandler] Unknown provider {provider_name}, falling back to OpenRouter"
            )
            return make_openrouter_request_openai(messages, model_id, **kwargs)

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
            provider_name: Provider to use (e.g., "openrouter", "cerebras", "groq")
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

        # Route to appropriate provider
        if provider_name == "openrouter":
            stream = await make_openrouter_request_openai_stream_async(
                messages, model_id, **kwargs
            )
            async for chunk in stream:
                yield chunk
        elif provider_name == "cerebras":
            stream = await make_cerebras_request_stream_async(messages, model_id, **kwargs)
            async for chunk in stream:
                yield chunk
        elif provider_name == "groq":
            stream = await make_groq_request_stream_async(messages, model_id, **kwargs)
            async for chunk in stream:
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

    # TODO: Implement process() for non-streaming requests (Task 7)
    # TODO: Implement process_stream() for streaming requests (Task 8)
