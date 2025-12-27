"""
Unified chat endpoint - single endpoint for all chat operations.

Replaces:
- /v1/chat/completions (OpenAI format)
- /v1/messages (Anthropic format)
- /v1/responses (OpenAI Responses API)
- /api/chat/ai-sdk (Vercel AI SDK)

Features:
- Auto-detects request format
- Returns response in matching format
- Supports all providers
- Handles streaming and non-streaming
- Anonymous and authenticated requests
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from src.schemas.unified_chat import UnifiedChatRequest
from src.services.format_detection import detect_request_format, validate_format_compatibility
from src.services.unified_chat_handler import UnifiedChatHandler
from src.services.response_formatters import ResponseFormatter
from src.security.deps import get_optional_api_key
from src.routes.helpers.chat import (
    validate_user_and_auth,
    validate_trial,
    check_plan_limits
)
from src.utils.token_estimator import estimate_message_tokens
from src.utils.logging_utils import mask_key

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize unified handler (singleton)
chat_handler = UnifiedChatHandler()


async def _to_thread(func, *args, **kwargs):
    """Helper for thread pool execution"""
    return await asyncio.to_thread(func, *args, **kwargs)


@router.post("/chat", tags=["chat"])
async def unified_chat_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: str | None = Depends(get_optional_api_key),
    session_id: int | None = Query(None, description="Chat session ID"),
):
    """
    🎯 UNIFIED CHAT ENDPOINT

    Single endpoint for ALL chat operations. Automatically detects and supports:
    - OpenAI format (chat completions)
    - Anthropic format (messages API)
    - OpenAI Responses API
    - Custom formats

    ## Request Format Auto-Detection

    The endpoint automatically detects which format you're using based on field presence.

    ### OpenAI Format (Default)
    ```json
    {
      "model": "gpt-4",
      "messages": [{"role": "user", "content": "Hello"}],
      "temperature": 0.7
    }
    ```

    ### Anthropic Format
    ```json
    {
      "model": "claude-3-opus",
      "system": "You are a helpful assistant",
      "messages": [{"role": "user", "content": "Hello"}],
      "max_tokens": 1024
    }
    ```

    ### Responses API Format
    ```json
    {
      "model": "gpt-4",
      "input": [{"role": "user", "content": "Hello"}],
      "response_format": {"type": "json_object"}
    }
    ```

    ### Explicit Format (Optional)
    ```json
    {
      "format": "openai",  // or "anthropic", "responses"
      "model": "gpt-4",
      "messages": [{"role": "user", "content": "Hello"}]
    }
    ```

    ## Response Format

    The response format automatically matches your request format:
    - OpenAI request → OpenAI response
    - Anthropic request → Anthropic response
    - Responses request → Responses response

    ## Features

    - ✅ All 15+ providers supported
    - ✅ Automatic provider failover
    - ✅ Streaming and non-streaming
    - ✅ Function calling (tools)
    - ✅ Chat history
    - ✅ Anonymous requests (no API key required)
    - ✅ Trial and paid accounts
    - ✅ Rate limiting
    - ✅ Usage analytics

    ## Examples

    See full documentation at /docs
    """

    try:
        # 1. Parse raw request body
        raw_body = await request.json()

        # 2. Detect format
        detected_format = detect_request_format(raw_body)
        logger.info(f"Detected request format: {detected_format}")

        # Validate format compatibility
        validate_format_compatibility(raw_body, detected_format)

        # 3. Parse into unified schema
        try:
            unified_request = UnifiedChatRequest(**raw_body)
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid request format: {str(e)}"
            )

        # 4. Authenticate and validate user
        user, is_anonymous = await validate_user_and_auth(
            api_key,
            _to_thread,
            request_id="unified-chat"
        )

        logger.info(
            f"unified_chat: model={unified_request.model}, "
            f"format={detected_format}, stream={unified_request.stream}, "
            f"anonymous={is_anonymous}, api_key={mask_key(api_key) if api_key else 'none'}"
        )

        # 5. Validate trial and get environment
        if not is_anonymous:
            trial = await validate_trial(api_key, _to_thread)
            environment_tag = user.get("environment_tag", "live")

            # Credit check for paid users
            if not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Insufficient credits"
                )
        else:
            trial = {"is_valid": True, "is_trial": False}
            environment_tag = "live"

        # 6. Get normalized messages
        messages = unified_request.get_normalized_messages()

        # 7. Plan limit pre-check (authenticated users only)
        if not is_anonymous:
            estimated_tokens = estimate_message_tokens(
                messages,
                unified_request.max_tokens
            )
            await check_plan_limits(
                user["id"],
                environment_tag,
                estimated_tokens,
                _to_thread
            )

        # 8. Process through unified handler
        response = await chat_handler.process_chat(
            messages=messages,
            model=unified_request.model,
            user=user,
            api_key=api_key,
            provider=unified_request.provider,
            stream=unified_request.stream,
            session_id=session_id,
            is_trial=trial.get("is_trial", False),
            is_anonymous=is_anonymous,
            environment_tag=environment_tag,
            # Pass all optional parameters
            **unified_request.get_optional_params()
        )

        # 9. Handle streaming response
        if unified_request.stream:
            return StreamingResponse(
                response,
                media_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Request-Format": detected_format,
                    "X-Response-Format": detected_format,
                }
            )

        # 10. Format response based on detected format
        formatted_response = ResponseFormatter.format_response(
            response,
            detected_format
        )

        # 11. Return formatted response
        return JSONResponse(
            content=formatted_response,
            headers={
                "X-Request-Format": detected_format,
                "X-Response-Format": detected_format,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unified chat endpoint error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
