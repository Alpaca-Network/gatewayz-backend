"""
Vercel AI SDK compatibility endpoint.

This route provides a dedicated endpoint for Vercel AI SDK requests.
The endpoint is compatible with the AI SDK client interface and routes
requests through the Vercel AI Gateway for actual model execution.

Endpoints:
- POST /api/chat/ai-sdk
- POST /api/chat/ai-sdk-completions (alias)
"""

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import src.db.chat_completion_requests as chat_completion_requests_module
from src.adapters.chat import AISDKChatAdapter
from src.db.users import deduct_credits, get_user, record_usage

# Unified chat handler and adapters for chat unification
from src.handlers.chat_handler import ChatInferenceHandler
from src.security.deps import get_api_key
from src.services.ai_sdk_client import (
    make_ai_sdk_request_openai_stream_async,
    process_ai_sdk_response,
    validate_ai_sdk_api_key,
)
from src.services.openrouter_client import (
    get_openrouter_client,
    make_openrouter_request_openai_stream_async,
)
from src.services.pricing import calculate_cost
from src.services.trial_validation import track_trial_usage, validate_trial_access
from src.utils.sentry_context import capture_payment_error

# Initialize logging
logger = logging.getLogger(__name__)

# Try to import sentry_sdk for error tracking
try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

# Create router
router = APIRouter()


def _extract_delta_content(delta) -> tuple[dict, bool]:
    """Extract content and reasoning from a streaming delta object.

    This helper function extracts both regular text content and reasoning/thinking
    content from a streaming delta, handling various attribute naming conventions
    used by different providers.

    Args:
        delta: A streaming delta object from OpenAI-compatible API response

    Returns:
        Tuple of (delta_response dict, has_content bool) where:
        - delta_response contains 'content' and/or 'reasoning_content' if present
        - has_content indicates whether any meaningful content was extracted
    """
    delta_response = {}
    has_content = False

    # Handle regular text content
    content = getattr(delta, "content", None)
    if content:
        delta_response["content"] = content
        has_content = True

    # Handle reasoning/thinking content (Claude extended thinking, etc.)
    # Check for reasoning_content first, then fall back to reasoning attribute
    # Use `is None` check to properly handle explicit empty string values
    reasoning = getattr(delta, "reasoning_content", None)
    if reasoning is None:
        reasoning = getattr(delta, "reasoning", None)
    if reasoning:
        delta_response["reasoning_content"] = reasoning
        has_content = True

    return delta_response, has_content


# Request/Response schemas for AI SDK endpoint
class Message(BaseModel):
    """Message object for chat completions"""

    role: str = Field(..., description="Role of the message author (user, assistant, system)")
    content: str = Field(..., description="Content of the message")


class AISDKChatRequest(BaseModel):
    """AI SDK chat completion request"""

    model: str = Field(..., description="Model to use for completion")
    messages: list[Message] = Field(..., description="List of messages in the conversation")
    max_tokens: int | None = Field(None, description="Maximum tokens to generate")
    temperature: float | None = Field(None, description="Sampling temperature (0.0 to 2.0)")
    top_p: float | None = Field(None, description="Top-p sampling parameter")
    frequency_penalty: float | None = Field(None, description="Frequency penalty")
    presence_penalty: float | None = Field(None, description="Presence penalty")
    stream: bool | None = Field(False, description="Whether to stream the response")


class Choice(BaseModel):
    """Choice in completion response"""

    message: dict
    finish_reason: str | None = None


class Usage(BaseModel):
    """Token usage information"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AISDKChatResponse(BaseModel):
    """AI SDK chat completion response"""

    choices: list[Choice]
    usage: Usage


def _build_request_kwargs(request: AISDKChatRequest) -> dict:
    """Build kwargs dictionary for AI SDK request.

    Args:
        request: The incoming AI SDK chat request

    Returns:
        dict: Filtered kwargs with None values removed
    """
    kwargs = {
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "frequency_penalty": request.frequency_penalty,
        "presence_penalty": request.presence_penalty,
    }
    # Remove None values
    return {k: v for k, v in kwargs.items() if v is not None}


def _check_trial_override(trial: dict, user: dict | None) -> bool:
    """Check if trial status should be overridden for users with active subscriptions.

    Defense-in-depth: Override is_trial flag if user has active subscription.
    This protects against webhook delays or failures that leave is_trial=TRUE
    for paid users.

    Args:
        trial: Trial status dictionary containing 'is_trial' flag
        user: User dictionary with subscription details

    Returns:
        bool: True if user should be treated as trial user, False if they should be billed
    """
    is_trial = trial.get("is_trial", False)

    # If not a trial user, no override needed
    if not is_trial:
        return False

    # Check for active subscription that should override trial status
    if user:
        has_active_subscription = (
            user.get("stripe_subscription_id") is not None
            and user.get("subscription_status") == "active"
        ) or user.get("tier") in ("pro", "max", "admin")

        if has_active_subscription:
            logger.warning(
                "BILLING_OVERRIDE: User %s has is_trial=TRUE but has active subscription "
                "(tier=%s, sub_status=%s, stripe_sub_id=%s). Forcing paid path.",
                user.get("id"),
                user.get("tier"),
                user.get("subscription_status"),
                user.get("stripe_subscription_id"),
            )
            return False  # Override: user should be billed

    return True  # User is a legitimate trial user


def _is_openrouter_model(model: str) -> bool:
    """Check if the model should be routed through OpenRouter.

    Models with the 'openrouter/' prefix are OpenRouter-specific and should be
    routed directly through OpenRouter instead of the Vercel AI Gateway.

    Examples:
        - openrouter/auto -> True (OpenRouter's automatic model selection)
        - openrouter/quasar-alpha -> True
        - openai/gpt-4o -> False (use Vercel AI Gateway)
        - anthropic/claude-3 -> False (use Vercel AI Gateway)

    Args:
        model: The requested model ID

    Returns:
        bool: True if the model should be routed through OpenRouter
    """
    if not model:
        return False
    return model.lower().startswith("openrouter/")


@router.post("/api/chat/ai-sdk-completions", tags=["ai-sdk"], response_model=AISDKChatResponse)
@router.post("/api/chat/ai-sdk", tags=["ai-sdk"], response_model=AISDKChatResponse)
async def ai_sdk_chat_completion(
    request: AISDKChatRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    """
    Vercel AI SDK compatible chat completion endpoint.

    **AUTHENTICATION REQUIRED:** This endpoint requires a valid Gatewayz API key.

    This endpoint provides compatibility with the Vercel AI SDK by accepting
    requests in the standard OpenAI chat completion format and routing them
    through the Vercel AI Gateway.

    **Request Format:**
    ```json
    {
        "model": "openai/gpt-5",
        "messages": [
            {"role": "user", "content": "Hello!"}
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
        "stream": false
    }
    ```

    **Response Format:**
    ```json
    {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you?"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 12,
            "total_tokens": 22
        }
    }
    ```

    **Supported Models (Vercel AI Gateway):**
    - OpenAI: openai/gpt-5, openai/gpt-4o, openai/gpt-4-turbo
    - Anthropic: anthropic/claude-sonnet-4.5, anthropic/claude-haiku-4.5
    - Google: google/gemini-2.5-pro, google/gemini-2.5-flash
    - xAI: xai/grok-3, xai/grok-2-latest
    - Meta: meta/llama-3.1-70b, meta/llama-3.1-8b
    - And models from DeepSeek, Mistral, Cohere, Perplexity, and more

    Model format: `provider/model-name` (e.g., `openai/gpt-5`, `anthropic/claude-sonnet-4.5`)

    For complete model list: https://vercel.com/ai-gateway/models

    **Raises:**
        HTTPException: If authentication fails, API_SDK_API_KEY is not configured, or request fails

    **Returns:**
        AISDKChatResponse: Chat completion response with choices and usage
    """
    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())
    start_time = time.monotonic()

    # Get user and validate
    user = await asyncio.to_thread(get_user, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Validate trial access
    trial = await asyncio.to_thread(validate_trial_access, api_key)
    if not trial.get("is_valid", False):
        raise HTTPException(status_code=403, detail=trial.get("error", "Access denied"))

    logger.info(
        f"ai_sdk_chat_completion start (request_id={request_id}, model={request.model}, stream={request.stream})"
    )

    try:
        logger.info(
            f"[Unified Handler] Processing AI SDK request for model {request.model}, stream={request.stream}"
        )

        # Convert AI SDK format request to dict for adapter
        ai_sdk_request = {
            "model": request.model,
            "messages": [{"role": msg.role, "content": msg.content} for msg in request.messages],
            "max_tokens": getattr(request, "max_tokens", None),
            "temperature": getattr(request, "temperature", None),
            "top_p": getattr(request, "top_p", None),
            "stream": request.stream or False,
        }

        # Convert AI SDK format to internal format
        adapter = AISDKChatAdapter()
        internal_request = adapter.to_internal_request(ai_sdk_request)

        # Create unified handler with user context
        handler = ChatInferenceHandler(api_key, background_tasks)

        # Handle streaming requests
        if request.stream:
            logger.info(f"[Unified Handler] Starting AI SDK streaming for model {request.model}")

            # Process stream through unified pipeline
            internal_stream = handler.process_stream(internal_request)

            # Convert internal stream to AI SDK SSE format
            sse_stream = adapter.from_internal_stream(internal_stream)

            return StreamingResponse(
                sse_stream,
                media_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                },
            )

        # Handle non-streaming requests
        # Process request through unified pipeline
        internal_response = await handler.process(internal_request)

        # Convert internal response back to AI SDK format
        processed = adapter.from_internal_response(internal_response)

        logger.info(
            f"[Unified Handler] Successfully processed AI SDK request: model={internal_response.model}"
        )

        return processed
        processed = await asyncio.to_thread(process_ai_sdk_response, response)  # noqa: F821

        # Calculate processing time and extract usage
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        usage = processed.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        # Calculate cost and handle credit deduction
        cost = await asyncio.to_thread(
            calculate_cost, request.model, prompt_tokens, completion_tokens
        )
        # Defense-in-depth: Override trial status if user has active subscription
        is_trial = _check_trial_override(trial, user)

        # Track trial usage
        if is_trial and not trial.get("is_expired"):
            try:
                await asyncio.to_thread(
                    track_trial_usage,
                    api_key,
                    total_tokens,
                    1,
                    model_id=request.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to track trial usage: {e}")

        # Deduct credits for non-trial users
        if not is_trial:
            try:
                await asyncio.to_thread(
                    deduct_credits,
                    api_key,
                    cost,
                    f"AI SDK usage - {request.model}",
                    {
                        "model": request.model,
                        "total_tokens": total_tokens,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_usd": cost,
                    },
                )
                await asyncio.to_thread(
                    record_usage,
                    user["id"],
                    api_key,
                    request.model,
                    total_tokens,
                    cost,
                    elapsed_ms,
                )
            except Exception as e:
                logger.error(f"Credit deduction error: {e}")
                raise

        # Extract provider from model (format: provider/model)
        provider_name = "vercel-ai-gateway"
        if "/" in request.model:
            provider_name = request.model.split("/")[0]

        # Save chat completion request metadata - run as background task
        background_tasks.add_task(
            chat_completion_requests_module.save_chat_completion_request,
            request_id=request_id,
            model_name=request.model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=elapsed_ms,
            status="completed",
            error_message=None,
            user_id=user["id"],
            provider_name=provider_name,
            model_id=None,
            api_key_id=user.get("key_id"),
        )

        return processed

    except HTTPException as http_exc:
        # Save failed request for HTTPException errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = (
                    int((time.monotonic() - start_time) * 1000) if "start_time" in dir() else 0
                )

                # Save failed request to database
                background_tasks.add_task(
                    chat_completion_requests_module.save_chat_completion_request,
                    request_id=request_id,
                    model_name=request.model,
                    input_tokens=0,  # Unknown at error time
                    output_tokens=0,
                    processing_time_ms=error_elapsed,
                    status="failed",
                    error_message=f"HTTP {http_exc.status_code}: {http_exc.detail}",
                    user_id=user.get("id") if "user" in locals() and user else None,
                    provider_name=(
                        "openrouter" if "/" not in request.model else request.model.split("/")[0]
                    ),
                    model_id=None,
                    api_key_id=user.get("key_id") if "user" in locals() and user else None,
                    is_anonymous=False,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")
        # Re-raise HTTPExceptions without modification (e.g., from _handle_openrouter_stream)
        raise
    except ValueError as e:
        error_message = str(e).lower()
        # Determine if this is an OpenRouter or AI SDK configuration error
        if "openrouter" in error_message:
            logger.error(f"OpenRouter configuration error: {e}", exc_info=True)
            detail = "OpenRouter service is not configured. Please contact support."
        else:
            logger.error(f"AI SDK configuration error: {e}", exc_info=True)
            detail = "AI SDK service is not configured. Please contact support."

        # Save failed request
        if request_id:
            try:
                error_elapsed = (
                    int((time.monotonic() - start_time) * 1000) if "start_time" in dir() else 0
                )
                background_tasks.add_task(
                    chat_completion_requests_module.save_chat_completion_request,
                    request_id=request_id,
                    model_name=request.model,
                    input_tokens=0,
                    output_tokens=0,
                    processing_time_ms=error_elapsed,
                    status="failed",
                    error_message=f"ValueError: {str(e)[:500]}",
                    user_id=user.get("id") if "user" in locals() and user else None,
                    provider_name=(
                        "openrouter" if "/" not in request.model else request.model.split("/")[0]
                    ),
                    model_id=None,
                    api_key_id=user.get("key_id") if "user" in locals() and user else None,
                    is_anonymous=False,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")

        # Capture configuration errors to Sentry (503 errors)
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=503, detail=detail)
    except Exception as e:
        logger.error(f"AI SDK chat completion error: {e}", exc_info=True)

        # Save failed request for unexpected errors
        if request_id:
            try:
                error_elapsed = (
                    int((time.monotonic() - start_time) * 1000) if "start_time" in dir() else 0
                )
                background_tasks.add_task(
                    chat_completion_requests_module.save_chat_completion_request,
                    request_id=request_id,
                    model_name=request.model,
                    input_tokens=0,
                    output_tokens=0,
                    processing_time_ms=error_elapsed,
                    status="failed",
                    error_message=f"{type(e).__name__}: {str(e)[:500]}",
                    user_id=user.get("id") if "user" in locals() and user else None,
                    provider_name=(
                        "openrouter" if "/" not in request.model else request.model.split("/")[0]
                    ),
                    model_id=None,
                    api_key_id=user.get("key_id") if "user" in locals() and user else None,
                    is_anonymous=False,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")

        # Capture all other errors to Sentry (500 errors)
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Failed to process AI SDK request: {str(e)}")


async def _handle_openrouter_stream(
    request: AISDKChatRequest,
    messages: list,
    kwargs: dict,
    api_key: str,
    user: dict,
    trial: dict,
):
    """Handle streaming responses routed directly through OpenRouter.

    Args:
        request: AISDKChatRequest with stream=True
        messages: Pre-converted messages list
        kwargs: Pre-built kwargs dictionary
        api_key: User's API key for credit deduction
        user: User object with id and other details
        trial: Trial validation result

    Returns:
        StreamingResponse with server-sent events

    Raises:
        HTTPException: If OpenRouter API key is not configured (503)
    """
    # Track tokens for credit deduction
    total_prompt_tokens = 0
    total_completion_tokens = 0
    accumulated_content = ""  # For token estimation fallback
    start_time_stream = time.monotonic()
    # Validate OpenRouter API key before starting the stream
    # This ensures we return HTTP 503 instead of streaming an error
    try:
        get_openrouter_client()
    except ValueError as e:
        logger.error(f"OpenRouter configuration error: {e}", exc_info=True)
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=503,
            detail="OpenRouter service is not configured. Please contact support.",
        )

    async def stream_response():
        nonlocal total_prompt_tokens, total_completion_tokens, accumulated_content
        try:
            # Make async streaming request directly to OpenRouter
            # PERF: Using async client prevents blocking the event loop while waiting
            # for chunks, ensuring proper real-time streaming to the client
            stream = await make_openrouter_request_openai_stream_async(
                messages, request.model, **kwargs
            )

            # Stream response chunks using async iteration
            # PERF: async for yields control to the event loop between chunks,
            # allowing FastAPI to flush each chunk immediately instead of buffering
            has_any_content = False  # Track if we received any content
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = getattr(chunk.choices[0], "delta", None)
                    if delta:
                        # Extract content and reasoning using shared helper
                        delta_response, has_content = _extract_delta_content(delta)
                        if has_content:
                            has_any_content = True
                            # Accumulate content for token estimation fallback
                            if "content" in delta_response:
                                accumulated_content += delta_response["content"]

                        # Only yield if we have content to send
                        if delta_response:
                            # Format as SSE (Server-Sent Events)
                            data = {"choices": [{"delta": {"role": "assistant", **delta_response}}]}
                            yield f"data: {json.dumps(data)}\n\n"

                # Extract usage if available (OpenRouter provides this in the final chunk)
                if hasattr(chunk, "usage") and chunk.usage:
                    total_prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                    total_completion_tokens = getattr(chunk.usage, "completion_tokens", 0)

            # If no content was streamed, log a warning for debugging
            if not has_any_content:
                logger.warning(
                    f"OpenRouter stream completed with no content for model {request.model}"
                )

            # Send completion signal
            completion_data = {"choices": [{"finish_reason": "stop"}]}
            yield f"data: {json.dumps(completion_data)}\n\n"
            yield "data: [DONE]\n\n"

            # After streaming completes, deduct credits
            # Token estimation fallback: if provider didn't return usage data,
            # estimate tokens based on content length (1 token ≈ 4 characters)
            if total_prompt_tokens == 0 and total_completion_tokens == 0:
                # Estimate completion tokens from accumulated content
                total_completion_tokens = max(1, len(accumulated_content) // 4)
                # Estimate prompt tokens from messages
                prompt_chars = sum(
                    len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                    for m in messages
                )
                total_prompt_tokens = max(1, prompt_chars // 4)
                logger.info(
                    f"OpenRouter stream: No usage data, estimated {total_prompt_tokens} prompt + "
                    f"{total_completion_tokens} completion tokens"
                )

            try:
                total_tokens = total_prompt_tokens + total_completion_tokens
                elapsed_ms = int((time.monotonic() - start_time_stream) * 1000)

                # Calculate cost
                cost = await asyncio.to_thread(
                    calculate_cost, request.model, total_prompt_tokens, total_completion_tokens
                )
                # Defense-in-depth: Override trial status if user has active subscription
                is_trial = _check_trial_override(trial, user)

                # Track trial usage
                if is_trial and not trial.get("is_expired"):
                    await asyncio.to_thread(
                        track_trial_usage,
                        api_key,
                        total_tokens,
                        1,
                        model_id=request.model,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                    )

                # Deduct credits for non-trial users
                if not is_trial:
                    await asyncio.to_thread(
                        deduct_credits,
                        api_key,
                        cost,
                        f"AI SDK streaming - {request.model}",
                        {
                            "model": request.model,
                            "total_tokens": total_tokens,
                            "prompt_tokens": total_prompt_tokens,
                            "completion_tokens": total_completion_tokens,
                            "cost_usd": cost,
                        },
                    )
                    await asyncio.to_thread(
                        record_usage,
                        user["id"],
                        api_key,
                        request.model,
                        total_tokens,
                        cost,
                        elapsed_ms,
                    )

                logger.info(
                    f"OpenRouter stream complete: {total_prompt_tokens} prompt + "
                    f"{total_completion_tokens} completion tokens, cost=${cost:.6f}"
                )
            except Exception as e:
                logger.error(f"Error deducting credits after stream: {e}", exc_info=True)
                # Capture payment errors for monitoring and alerting
                capture_payment_error(
                    e,
                    operation="streaming_credit_deduction",
                    user_id=user.get("id"),
                    amount=0.0,  # Cost unknown at this point
                    details={
                        "model": request.model,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                        "endpoint": "ai_sdk_openrouter_stream",
                    },
                )

        except ValueError as e:
            error_message = str(e).lower()
            # Determine if this is an OpenRouter configuration error
            if "openrouter" in error_message:
                logger.error(f"OpenRouter configuration error: {e}", exc_info=True)
                detail = "OpenRouter service is not configured. Please contact support."
            else:
                logger.error(f"OpenRouter streaming error: {e}", exc_info=True)
                detail = f"Failed to process streaming request: {str(e)}"
            # Capture errors to Sentry
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            error_data = {"error": detail}
            yield f"data: {json.dumps(error_data)}\n\n"
        except Exception as e:
            logger.error(f"OpenRouter streaming error: {e}", exc_info=True)
            # Capture streaming errors to Sentry
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            error_data = {"error": f"Failed to process streaming request: {str(e)}"}
            yield f"data: {json.dumps(error_data)}\n\n"

    # SSE streaming headers to prevent buffering by proxies/nginx
    stream_headers = {
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
    }

    return StreamingResponse(
        stream_response(), media_type="text/event-stream", headers=stream_headers
    )


async def _handle_ai_sdk_stream(
    request: AISDKChatRequest,
    model: str,
    api_key: str,
    user: dict,
    trial: dict,
):
    """Handle streaming responses for AI SDK endpoint.

    Args:
        request: AISDKChatRequest with stream=True
        model: The transformed model ID to use
        api_key: User's API key for credit deduction
        user: User object with id and other details
        trial: Trial validation result

    Returns:
        StreamingResponse with server-sent events
    """
    # Track tokens for credit deduction
    total_prompt_tokens = 0
    total_completion_tokens = 0
    accumulated_content = ""  # For token estimation fallback
    start_time_stream = time.monotonic()

    async def stream_response():
        nonlocal total_prompt_tokens, total_completion_tokens, accumulated_content
        messages = []  # Will be populated inside the try block
        try:
            # Validate API key is configured
            validate_ai_sdk_api_key()

            # Build kwargs for API request
            kwargs = _build_request_kwargs(request)

            # Convert messages to dict format
            messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

            # Make async streaming request
            # PERF: Using async client prevents blocking the event loop while waiting
            # for chunks, ensuring proper real-time streaming to the client
            stream = await make_ai_sdk_request_openai_stream_async(messages, model, **kwargs)

            # Stream response chunks using async iteration
            # PERF: async for yields control to the event loop between chunks,
            # allowing FastAPI to flush each chunk immediately instead of buffering
            has_any_content = False  # Track if we received any content
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = getattr(chunk.choices[0], "delta", None)
                    if delta:
                        # Extract content and reasoning using shared helper
                        delta_response, has_content = _extract_delta_content(delta)
                        if has_content:
                            has_any_content = True
                            # Accumulate content for token estimation fallback
                            if "content" in delta_response:
                                accumulated_content += delta_response["content"]

                        # Only yield if we have content to send
                        if delta_response:
                            # Format as SSE (Server-Sent Events)
                            data = {"choices": [{"delta": {"role": "assistant", **delta_response}}]}
                            yield f"data: {json.dumps(data)}\n\n"

                # Extract usage if available (AI SDK provides this in the final chunk)
                if hasattr(chunk, "usage") and chunk.usage:
                    total_prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                    total_completion_tokens = getattr(chunk.usage, "completion_tokens", 0)

            # If no content was streamed, log a warning for debugging
            if not has_any_content:
                logger.warning(f"AI SDK stream completed with no content for model {model}")

            # Send completion signal
            completion_data = {"choices": [{"finish_reason": "stop"}]}
            yield f"data: {json.dumps(completion_data)}\n\n"
            yield "data: [DONE]\n\n"

            # After streaming completes, deduct credits
            # Token estimation fallback: if provider didn't return usage data,
            # estimate tokens based on content length (1 token ≈ 4 characters)
            if total_prompt_tokens == 0 and total_completion_tokens == 0:
                # Estimate completion tokens from accumulated content
                total_completion_tokens = max(1, len(accumulated_content) // 4)
                # Estimate prompt tokens from messages
                prompt_chars = sum(
                    len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                    for m in messages
                )
                total_prompt_tokens = max(1, prompt_chars // 4)
                logger.info(
                    f"AI SDK stream: No usage data, estimated {total_prompt_tokens} prompt + "
                    f"{total_completion_tokens} completion tokens"
                )

            try:
                total_tokens = total_prompt_tokens + total_completion_tokens
                elapsed_ms = int((time.monotonic() - start_time_stream) * 1000)

                # Calculate cost
                cost = await asyncio.to_thread(
                    calculate_cost, model, total_prompt_tokens, total_completion_tokens
                )
                # Defense-in-depth: Override trial status if user has active subscription
                is_trial = _check_trial_override(trial, user)

                # Track trial usage
                if is_trial and not trial.get("is_expired"):
                    await asyncio.to_thread(
                        track_trial_usage,
                        api_key,
                        total_tokens,
                        1,
                        model_id=model,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                    )

                # Deduct credits for non-trial users
                if not is_trial:
                    await asyncio.to_thread(
                        deduct_credits,
                        api_key,
                        cost,
                        f"AI SDK streaming - {model}",
                        {
                            "model": model,
                            "total_tokens": total_tokens,
                            "prompt_tokens": total_prompt_tokens,
                            "completion_tokens": total_completion_tokens,
                            "cost_usd": cost,
                        },
                    )
                    await asyncio.to_thread(
                        record_usage,
                        user["id"],
                        api_key,
                        model,
                        total_tokens,
                        cost,
                        elapsed_ms,
                    )

                logger.info(
                    f"AI SDK stream complete: {total_prompt_tokens} prompt + "
                    f"{total_completion_tokens} completion tokens, cost=${cost:.6f}"
                )
            except Exception as e:
                logger.error(f"Error deducting credits after stream: {e}", exc_info=True)
                # Capture payment errors for monitoring and alerting
                capture_payment_error(
                    e,
                    operation="streaming_credit_deduction",
                    user_id=user.get("id"),
                    amount=0.0,  # Cost unknown at this point
                    details={
                        "model": model,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                        "endpoint": "ai_sdk_stream",
                    },
                )

        except ValueError as e:
            # This handler only processes AI SDK models (non-OpenRouter)
            logger.error(f"AI SDK configuration error: {e}", exc_info=True)
            # Capture configuration errors to Sentry
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            error_data = {"error": "AI SDK service is not configured. Please contact support."}
            yield f"data: {json.dumps(error_data)}\n\n"
        except Exception as e:
            logger.error(f"AI SDK streaming error: {e}", exc_info=True)
            # Capture streaming errors to Sentry
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            error_data = {"error": f"Failed to process streaming request: {str(e)}"}
            yield f"data: {json.dumps(error_data)}\n\n"

    # SSE streaming headers to prevent buffering by proxies/nginx
    stream_headers = {
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
    }

    return StreamingResponse(
        stream_response(), media_type="text/event-stream", headers=stream_headers
    )
