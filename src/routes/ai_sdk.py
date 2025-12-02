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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.services.ai_sdk_client import (
    make_ai_sdk_request_openai,
    make_ai_sdk_request_openai_stream,
    process_ai_sdk_response,
    validate_ai_sdk_api_key,
)
from src.services.openrouter_client import (
    get_openrouter_client,
    make_openrouter_request_openai,
    make_openrouter_request_openai_stream,
    process_openrouter_response,
)

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
async def ai_sdk_chat_completion(request: AISDKChatRequest):
    """
    Vercel AI SDK compatible chat completion endpoint.

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
        HTTPException: If AI_SDK_API_KEY is not configured or request fails

    **Returns:**
        AISDKChatResponse: Chat completion response with choices and usage
    """
    try:
        # Build kwargs for API request
        kwargs = _build_request_kwargs(request)

        # Convert messages to dict format
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

        # Check if this is an openrouter/* model - route directly through OpenRouter
        if _is_openrouter_model(request.model):
            logger.info(f"Routing '{request.model}' directly through OpenRouter")

            # Handle streaming requests via OpenRouter
            if request.stream:
                return await _handle_openrouter_stream(request, messages, kwargs)

            # Make request directly to OpenRouter
            response = await asyncio.to_thread(
                make_openrouter_request_openai, messages, request.model, **kwargs
            )

            # Process and return response
            processed = await asyncio.to_thread(process_openrouter_response, response)
            return processed

        # Default path: Use Vercel AI Gateway
        # Validate API key is configured
        validate_ai_sdk_api_key()

        # Handle streaming requests via AI SDK
        if request.stream:
            return await _handle_ai_sdk_stream(request, request.model)

        # Make request to AI SDK endpoint
        response = await asyncio.to_thread(
            make_ai_sdk_request_openai, messages, request.model, **kwargs
        )

        # Process and return response
        processed = await asyncio.to_thread(process_ai_sdk_response, response)
        return processed

    except HTTPException:
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
        # Capture configuration errors to Sentry (503 errors)
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=503, detail=detail)
    except Exception as e:
        logger.error(f"AI SDK chat completion error: {e}", exc_info=True)
        # Capture all other errors to Sentry (500 errors)
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=500, detail=f"Failed to process AI SDK request: {str(e)}"
        )


async def _handle_openrouter_stream(request: AISDKChatRequest, messages: list, kwargs: dict):
    """Handle streaming responses routed directly through OpenRouter.

    Args:
        request: AISDKChatRequest with stream=True
        messages: Pre-converted messages list
        kwargs: Pre-built kwargs dictionary

    Returns:
        StreamingResponse with server-sent events

    Raises:
        HTTPException: If OpenRouter API key is not configured (503)
    """
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
        try:
            # Make streaming request directly to OpenRouter
            stream = await asyncio.to_thread(
                make_openrouter_request_openai_stream, messages, request.model, **kwargs
            )

            # Stream response chunks
            for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = getattr(chunk.choices[0], "delta", None)
                    if delta and hasattr(delta, "content") and delta.content:
                        # Format as SSE (Server-Sent Events)
                        data = {
                            "choices": [{"delta": {"role": "assistant", "content": delta.content}}]
                        }
                        yield f"data: {json.dumps(data)}\n\n"

            # Send completion signal
            completion_data = {"choices": [{"finish_reason": "stop"}]}
            yield f"data: {json.dumps(completion_data)}\n\n"
            yield "data: [DONE]\n\n"

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

    return StreamingResponse(stream_response(), media_type="text/event-stream")


async def _handle_ai_sdk_stream(request: AISDKChatRequest, model: str):
    """Handle streaming responses for AI SDK endpoint.

    Args:
        request: AISDKChatRequest with stream=True
        model: The transformed model ID to use

    Returns:
        StreamingResponse with server-sent events
    """

    async def stream_response():
        try:
            # Validate API key is configured
            validate_ai_sdk_api_key()

            # Build kwargs for API request
            kwargs = _build_request_kwargs(request)

            # Convert messages to dict format
            messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

            # Make streaming request
            stream = await asyncio.to_thread(
                make_ai_sdk_request_openai_stream, messages, model, **kwargs
            )

            # Stream response chunks
            for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = getattr(chunk.choices[0], "delta", None)
                    if delta and hasattr(delta, "content") and delta.content:
                        # Format as SSE (Server-Sent Events)
                        data = {
                            "choices": [{"delta": {"role": "assistant", "content": delta.content}}]
                        }
                        yield f"data: {json.dumps(data)}\n\n"

            # Send completion signal
            completion_data = {"choices": [{"finish_reason": "stop"}]}
            yield f"data: {json.dumps(completion_data)}\n\n"
            yield "data: [DONE]\n\n"

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

    return StreamingResponse(stream_response(), media_type="text/event-stream")
