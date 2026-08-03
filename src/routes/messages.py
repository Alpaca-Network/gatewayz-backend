"""Anthropic Messages API compatibility endpoint (``POST /v1/messages``).

Why this exists
---------------
Claude Code -- the single largest coding-agent audience -- speaks the Anthropic
Messages API and is pointed at a gateway via ``ANTHROPIC_BASE_URL``. It has no
OpenAI-compatible mode. Without this route the only way to run Claude Code on
Gatewayz is to install a third-party translation proxy (Claude Code Router),
which adds an install step to the funnel and puts a dependency we do not
control on the critical path.

Implementation
--------------
The request is translated to the gateway's internal OpenAI shape and handed to
the existing ``chat_completions`` pipeline, so billing, rate limiting, provider
failover, credit checks and analytics all behave identically to the OpenAI
route -- there is exactly one inference path in this service, and this endpoint
does not fork it. The response is translated back to Anthropic shape.

``cache_control`` markers survive the round trip (see ``anthropic_transformer``
and ``anthropic_native_client``), which is what makes Claude Code's large
static prefix cheap to replay.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.schemas.proxy import ProxyRequest
from src.security.deps import get_optional_api_key
from src.services.providers.anthropic_transformer import (
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["messages"])


class AnthropicMessagesRequest(BaseModel):
    """Anthropic Messages API request.

    Mirrors https://docs.anthropic.com/en/api/messages. Extra fields are
    allowed so that newer Anthropic parameters pass through rather than 422-ing
    a client we do not control.
    """

    model: str = Field(..., description="Model identifier")
    messages: list[dict[str, Any]] = Field(..., description="Conversation turns")
    max_tokens: int = Field(..., ge=1, description="Maximum tokens to generate (required)")
    system: str | list[dict[str, Any]] | None = Field(None, description="System prompt")
    temperature: float | None = Field(None, ge=0.0, le=1.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    top_k: int | None = Field(None, ge=0)
    stop_sequences: list[str] | None = None
    stream: bool | None = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | str | None = None
    metadata: dict[str, Any] | None = None

    class Config:
        extra = "allow"


def _resolve_api_key(request: Request, api_key: str | None) -> str | None:
    """Accept Anthropic-style ``x-api-key`` as well as ``Authorization: Bearer``.

    Claude Code sends ``x-api-key``; the rest of the gateway expects a Bearer
    token. Without this the endpoint would 401 for exactly the client it was
    built for.
    """
    if api_key:
        return api_key
    if request is None:
        return None
    header_key = request.headers.get("x-api-key")
    return header_key.strip() if header_key else None


def _anthropic_error(status_code: int, error_type: str, message: str) -> HTTPException:
    """Errors in Anthropic's envelope, so Anthropic SDKs parse them correctly."""
    return HTTPException(
        status_code=status_code,
        detail={"type": "error", "error": {"type": error_type, "message": message}},
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_anthropic_events(
    openai_stream,
    model: str,
    message_id: str,
):
    """Re-emit an OpenAI SSE stream as Anthropic Messages stream events.

    Anthropic's event sequence is:
        message_start -> content_block_start -> content_block_delta*
        -> content_block_stop -> message_delta -> message_stop

    Text and tool-call deltas map onto separate content blocks; the block index
    advances when the stream switches between them.
    """
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )

    block_index = -1
    text_block_open = False
    # Maps an OpenAI tool_call index to the Anthropic content-block index.
    tool_blocks: dict[int, int] = {}
    stop_reason = "end_turn"
    usage: dict[str, Any] = {}

    finish_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "refusal",
    }

    async for raw in openai_stream:
        # The OpenAI adapter yields fully-formed SSE lines.
        if not raw or not raw.startswith("data: "):
            continue
        payload = raw[len("data: ") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if chunk.get("usage"):
            usage = chunk["usage"]

        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        if choice.get("finish_reason"):
            stop_reason = finish_map.get(choice["finish_reason"], "end_turn")

        content = delta.get("content")
        if content:
            if not text_block_open:
                block_index += 1
                text_block_open = True
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": content},
                },
            )

        for tool_call in delta.get("tool_calls") or []:
            tc_index = tool_call.get("index", 0)
            fn = tool_call.get("function") or {}

            if tc_index not in tool_blocks:
                # A tool call starting means any open text block is finished.
                if text_block_open:
                    yield _sse(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block_index},
                    )
                    text_block_open = False
                block_index += 1
                tool_blocks[tc_index] = block_index
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_call.get("id", f"toolu_{uuid.uuid4().hex[:16]}"),
                            "name": fn.get("name", ""),
                            "input": {},
                        },
                    },
                )

            arguments = fn.get("arguments")
            if arguments:
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": tool_blocks[tc_index],
                        "delta": {"type": "input_json_delta", "partial_json": arguments},
                    },
                )

    # Close whichever block is still open.
    if text_block_open or tool_blocks:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_index})

    anthropic_usage = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }
    if usage.get("cache_read_input_tokens"):
        anthropic_usage["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
    if usage.get("cache_creation_input_tokens"):
        anthropic_usage["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]

    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": anthropic_usage,
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


@router.post("/messages", tags=["messages"])
async def create_message(
    req: AnthropicMessagesRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    api_key: str | None = Depends(get_optional_api_key),
):
    """Anthropic Messages API endpoint.

    Point Claude Code at the gateway with:
        ANTHROPIC_BASE_URL=https://api.gatewayz.ai
        ANTHROPIC_AUTH_TOKEN=<gatewayz key>
    """
    resolved_key = _resolve_api_key(request, api_key)

    if not req.messages:
        raise _anthropic_error(400, "invalid_request_error", "messages must not be empty")

    # Anthropic -> OpenAI (internal shape). cache_control markers are preserved
    # by the transformer.
    openai_messages, openai_params = transform_anthropic_to_openai(
        messages=req.messages,
        system=req.system,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        stop_sequences=req.stop_sequences,
        tools=req.tools,
        tool_choice=req.tool_choice,
    )

    proxy_request = ProxyRequest(
        model=req.model,
        messages=openai_messages,
        stream=bool(req.stream),
        **openai_params,
    )

    # Reuse the single inference pipeline rather than forking it: billing,
    # rate limiting, failover and analytics all come along unchanged.
    from src.routes.chat import chat_completions

    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    try:
        result = await chat_completions(
            req=proxy_request,
            background_tasks=background_tasks,
            api_key=resolved_key,
            session_id=None,
            request=request,
        )
    except HTTPException as exc:
        # Re-wrap gateway errors in Anthropic's envelope so Anthropic SDKs
        # surface a useful message instead of "unknown error shape".
        detail = exc.detail
        message = (
            detail
            if isinstance(detail, str)
            else json.dumps(detail) if detail else "request failed"
        )
        error_type = {
            400: "invalid_request_error",
            401: "authentication_error",
            403: "permission_error",
            404: "not_found_error",
            429: "rate_limit_error",
        }.get(exc.status_code, "api_error")
        raise _anthropic_error(exc.status_code, error_type, message) from exc

    if req.stream:
        if not isinstance(result, StreamingResponse):
            raise _anthropic_error(
                500, "api_error", "upstream did not return a stream for a streaming request"
            )
        return StreamingResponse(
            _stream_anthropic_events(result.body_iterator, req.model, message_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    openai_response = result if isinstance(result, dict) else getattr(result, "body", result)
    if not isinstance(openai_response, dict):
        raise _anthropic_error(500, "api_error", "unexpected upstream response shape")

    anthropic_response = transform_openai_to_anthropic(
        openai_response,
        req.model,
        stop_sequences=req.stop_sequences,
    )
    anthropic_response["id"] = message_id
    return anthropic_response


@router.get("/messages/health", tags=["messages"])
async def messages_health():
    """Liveness probe for the Anthropic-compatibility surface."""
    return {
        "status": "ok",
        "endpoint": "/v1/messages",
        "api": "anthropic-messages",
        "supports": ["streaming", "tools", "prompt_caching", "vision"],
        "timestamp": int(time.time()),
    }
