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
from src.security.identity import get_request_identity
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


# Gateway/stream error types -> Anthropic SSE error types
# (https://docs.anthropic.com/en/api/errors). Anything unmapped is an api_error.
_STREAM_ERROR_TYPE_MAP = {
    "rate_limit_error": "rate_limit_error",
    "plan_limit_exceeded": "rate_limit_error",
    "auth_error": "authentication_error",
    "not_found_error": "not_found_error",
    "capacity_error": "overloaded_error",
}

# Error-shaped chunks that are advisories emitted AFTER content was fully
# delivered (chat_streaming continues to [DONE] after them). Terminating on
# these would discard a delivered-and-billed response.
_NON_FATAL_STREAM_ERROR_TYPES = frozenset({"stream_normalization_warning"})


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

        # A provider failure travels through the inner stream as an error chunk
        # (see stream_normalizer.create_error_sse_chunk). Before this check it
        # fell into the no-choices `continue` below and the stream ended with a
        # clean zero-usage message_stop — a fabricated success (issue #2236).
        # Surface it as Anthropic's own SSE error event and stop.
        if chunk.get("error"):
            err = chunk["error"] if isinstance(chunk["error"], dict) else {}
            if err.get("type") in _NON_FATAL_STREAM_ERROR_TYPES:
                continue
            yield _sse(
                "error",
                {
                    "type": "error",
                    "error": {
                        "type": _STREAM_ERROR_TYPE_MAP.get(err.get("type"), "api_error"),
                        # Never fall back to repr-ing the raw error object —
                        # it may carry internal fields (provider, request_id).
                        "message": err.get("message") or "upstream provider error",
                    },
                },
            )
            # Close the inner generator so its finally blocks (provider
            # connection release, cleanup) run now, not at GC time.
            if hasattr(openai_stream, "aclose"):
                try:
                    await openai_stream.aclose()
                except Exception:
                    pass
            return

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


def _unwrap_chat_result(result):
    """Normalize chat_completions' return value to the response dict.

    chat_completions returns a plain dict in some paths and a JSONResponse
    (rendered BYTES in .body, carrying rate-limit headers) in others — the
    latter is what production returns today. Anything else is a genuine
    unexpected shape.
    """
    if isinstance(result, dict):
        return result
    body = getattr(result, "body", None)
    if isinstance(body, (bytes, bytearray)):
        try:
            decoded = json.loads(body.decode())
        except (ValueError, UnicodeDecodeError):
            raise _anthropic_error(500, "api_error", "unexpected upstream response shape")
        if isinstance(decoded, dict):
            return decoded
    elif isinstance(body, dict):
        return body
    raise _anthropic_error(500, "api_error", "unexpected upstream response shape")


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
        # chat_completions() takes `identity` via Depends(get_request_identity);
        # calling it directly bypasses FastAPI's injection, so resolve the
        # identity here from the SAME key we resolved above (which may have
        # come from Anthropic-style `x-api-key`, invisible to the Bearer-only
        # dependency). Regression: #2274 added the parameter and this call
        # site was missed, 500ing every /v1/messages request.
        identity = await get_request_identity(request, api_key=resolved_key)
        result = await chat_completions(
            req=proxy_request,
            background_tasks=background_tasks,
            api_key=resolved_key,
            session_id=None,
            request=request,
            identity=identity,
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
            # Provider capacity/budget exhaustion (e.g. unfunded upstream
            # account) surfaces as 503 — Anthropic SDKs retry overloaded_error.
            503: "overloaded_error",
        }.get(exc.status_code, "api_error")
        # A 503 for missing pricing config is deterministic, not transient —
        # don't invite SDK retries on it.
        if error_type == "overloaded_error" and "pricing_not_configured" in message:
            error_type = "api_error"
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

    openai_response = _unwrap_chat_result(result)

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
