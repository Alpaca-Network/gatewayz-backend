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
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.schemas.proxy import ProxyRequest
from src.security.deps import get_optional_api_key_strict
from src.security.identity import get_request_identity
from src.security.security import ApiKeyLookupUnavailable, validate_api_key_security
from src.services.auth_rate_limiting import get_client_ip
from src.services.job_usage import JOB_BUSY, job_lease_of
from src.services.job_usage_v2 import CONTENT_HASH_HEADER
from src.services.providers.anthropic_transformer import (
    anthropic_input_tokens,
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)

logger = logging.getLogger(__name__)


def _trusted_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    ip = get_client_ip(request)
    return None if not ip or ip == "unknown" else ip


# Job keys: the usage line's content hash, emitted by chat_dispatch before [DONE].
_CONTENT_HASH_COMMENT = f": {CONTENT_HASH_HEADER.lower()}: "

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


def _detail_message(exc: HTTPException) -> str:
    """The human-readable string out of an HTTPException detail.

    Our gates raise `detail={"error": {"message": ..., "code": ...}}`. Passing
    that dict through `json.dumps` put a JSON blob inside the Anthropic
    envelope's own `message` field — unreadable in an SDK traceback.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if detail.get("message"):
            return str(detail["message"])
        return json.dumps(detail)
    return str(detail) if detail else "request failed"


# Any URL except our own: a top-up or docs link in a gateway message is kept.
_URL_RE = re.compile(r"https?://(?![\w.-]*gatewayz\.ai)\S+")
_GENERIC_UPSTREAM_ERROR = "The model provider returned an error."
_GENERIC_GATEWAY_ERROR = "The gateway could not process the provider's response."


def _safe_error_message(message: Any) -> str:
    """An error message with internal details removed.

    Provider and library errors reach this route as raw exception text: the
    upstream URL from an HTTP client error, or a validation dump naming our
    internal classes. Neither helps the caller and both describe our internals.
    """
    text = str(message or "").strip()
    if not text:
        return _GENERIC_UPSTREAM_ERROR
    if "validation error" in text and " for " in text:
        return _GENERIC_GATEWAY_ERROR
    lines = [
        line
        for line in text.splitlines()
        if not line.strip().lower().startswith("for more information")
    ]
    text = _URL_RE.sub("", " ".join(lines))
    text = re.sub(r"\s+for url\s*'?'?", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" :'")
    if not text:
        return _GENERIC_UPSTREAM_ERROR
    return text[:500]


_PLAN_LIMIT_PREFIX = "Plan limit exceeded:"


def _is_plan_period_limit(status_code: int, message: str) -> bool:
    """True for a spent daily or monthly plan allowance.

    These arrive as 429, which Anthropic clients retry with backoff. Retrying
    cannot help before the period resets, so the caller is told once instead of
    waiting through minutes of retries. Per-minute limits stay 429.
    """
    if status_code != 429 or not message.startswith(_PLAN_LIMIT_PREFIX):
        return False
    lowered = message.lower()
    return "daily" in lowered or "monthly" in lowered


def _plan_limit_message(message: str) -> str:
    reason = message[len(_PLAN_LIMIT_PREFIX) :].strip()
    return (
        f"Gatewayz plan limit reached: {reason}. Retrying will not help until the "
        "limit resets or the plan is raised."
    )


def _detail_code(exc: HTTPException) -> str | None:
    """The gateway error code (`model_not_found`, ...) when the detail carries one."""
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict):
            return inner.get("code")
    return None


def _anthropic_error_type(status_code: int, code: str | None) -> str:
    """Map our status + error code onto an Anthropic error type."""
    base = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        429: "rate_limit_error",
        # Provider budget exhaustion is 402 and terminal: an SDK must not
        # retry it, and an operator must be told to top up rather than wait.
        402: "invalid_request_error",
        # 503 stays overloaded_error for GENUINE upstream capacity, which is
        # the one case where retrying is the right advice.
        503: "overloaded_error",
    }.get(status_code, "api_error")
    # A 503 for missing pricing config is deterministic, not transient —
    # don't invite SDK retries on it.
    if base == "overloaded_error" and code == "pricing_not_configured":
        return "api_error"
    return base


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Gateway/stream error types -> Anthropic SSE error types
# (https://docs.anthropic.com/en/api/errors). Anything unmapped is an api_error.
_STREAM_ERROR_TYPE_MAP = {
    "rate_limit_error": "rate_limit_error",
    "plan_limit_exceeded": "rate_limit_error",
    "auth_error": "authentication_error",
    "not_found_error": "not_found_error",
    # NOT overloaded_error. `capacity_error` is emitted from exactly one place --
    # chat_streaming's is_provider_budget_error() branch -- so it always means a
    # spending limit on OUR provider account, never genuine upstream capacity.
    # overloaded_error is the one Anthropic type whose documented advice is to
    # back off and retry, which is the advice this condition can never satisfy.
    #
    # This is #2355's defect coming through the streaming door: that PR moved the
    # non-stream path from 503 to a terminal 402, and the mid-stream event for the
    # identical cause kept telling the SDK to retry.
    "capacity_error": "invalid_request_error",
}

# Error-shaped chunks that are advisories emitted AFTER content was fully
# delivered (chat_streaming continues to [DONE] after them). Terminating on
# these would discard a delivered-and-billed response.
#
# plan_limit_exceeded is the post-usage check in chat_streaming: this response
# crossed the plan allowance, and it was already delivered and billed. The NEXT
# request is refused before it starts; this one must end normally.
_NON_FATAL_STREAM_ERROR_TYPES = frozenset({"stream_normalization_warning", "plan_limit_exceeded"})


async def _aclose_stream(openai_stream) -> None:
    """Close the inner generator so its finally blocks (provider connection
    release, cleanup) run now, not at GC time."""
    if hasattr(openai_stream, "aclose"):
        try:
            await openai_stream.aclose()
        except Exception:
            pass


async def _stream_anthropic_events(
    openai_stream,
    model: str,
    message_id: str,
):
    """Re-emit an OpenAI SSE stream as Anthropic events, closing the inner
    stream on every exit path.

    The inner stream is the chat pipeline's generator, which bills after it
    finishes -- and, on a client abort, in its own cleanup. A client
    disconnect (CancelledError/GeneratorExit at our ``yield``) never reaches
    the inner generator on its own, so without this close it would sit
    suspended and the tokens already generated upstream would go uncharged
    until garbage collection (or forever).
    """
    events = _stream_anthropic_events_impl(openai_stream, model, message_id)
    try:
        async for event in events:
            yield event
    finally:
        # Close the inner stream first: that is what triggers its billing.
        try:
            await _aclose_stream(openai_stream)
        except BaseException:  # noqa: BLE001 - never mask the cancellation
            pass
        try:
            await events.aclose()
        except BaseException:  # noqa: BLE001
            pass


async def _stream_anthropic_events_impl(
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
    # Index of the block that is currently open (text or tool_use), or None.
    # Anthropic clients finish a block only on its content_block_stop, so every
    # block is closed before the next one opens. Leaving earlier tool blocks
    # open made clients keep one of several parallel tool calls.
    open_block: int | None = None
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

    try:
        async for raw in openai_stream:
            # The OpenAI adapter yields fully-formed SSE lines.
            if raw and raw.startswith(_CONTENT_HASH_COMMENT):
                yield raw  # job keys: the usage line's content hash, as an SSE comment
                continue
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
                            "message": _safe_error_message(err.get("message")),
                        },
                    },
                )
                await _aclose_stream(openai_stream)
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
                    if open_block is not None:
                        yield _sse(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": open_block},
                        )
                    block_index += 1
                    open_block = block_index
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
                    # A tool call starting means the open block is finished,
                    # whether that is text or an earlier tool call.
                    if open_block is not None:
                        yield _sse(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": open_block},
                        )
                    text_block_open = False
                    block_index += 1
                    open_block = block_index
                    tool_blocks[tc_index] = block_index
                    yield _sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
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
    except Exception:
        # An upstream failure reaches us either as an error-shaped chunk
        # (handled above) or as a raised exception on the inner iterator —
        # a reset provider connection, a read timeout, a transport error.
        # StreamingResponse has already flushed 200 and possibly content, so
        # an escaping exception cannot become an HTTP error: the client just
        # sees the stream stop, which is indistinguishable from a short
        # successful answer. Same fabricated success as #2236, other door.
        # CancelledError/GeneratorExit are BaseException and deliberately
        # excluded — those mean the CLIENT went away, not the provider.
        logger.exception("Upstream stream raised mid-flight for model %s", model)
        yield _sse(
            "error",
            {
                "type": "error",
                # Never surface the exception text — it can carry DSNs,
                # provider hostnames and request ids.
                "error": {
                    "type": "api_error",
                    "message": "The upstream provider connection failed mid-stream. The response is incomplete.",
                },
            },
        )
        await _aclose_stream(openai_stream)
        return

    # Close whichever block is still open.
    if open_block is not None:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": open_block})
    if tool_blocks:
        # A turn that made tool calls ends with tool_use, whatever finish_reason
        # the provider reported.
        stop_reason = "tool_use"

    anthropic_usage = {
        "input_tokens": anthropic_input_tokens(usage),
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


def _resolve_checked_api_key(request: Request, api_key: str | None) -> str | None:
    """The caller's key, with the key security checks applied to ``x-api-key``."""
    resolved_key = _resolve_api_key(request, api_key)
    if resolved_key and not api_key:
        # x-api-key bypassed the Bearer dependency; apply the same key security
        # checks (IP allowlist, referrer domains, max_requests).
        lease_sink = [] if request is not None and not job_lease_of(request) else None
        try:
            validate_api_key_security(
                resolved_key,
                # Not request.client.host: that is the caller-written first
                # X-Forwarded-For hop and would let a forged header pass the allowlist.
                client_ip=_trusted_ip(request),
                referer=request.headers.get("referer") if request else None,
                method=request.method if request else None,
                path=request.url.path if request else None,
                lease_sink=lease_sink,
            )
        except ApiKeyLookupUnavailable as e:
            raise _anthropic_error(
                503, "api_error", "Authentication temporarily unavailable"
            ) from e
        except ValueError as e:
            if JOB_BUSY in str(e):
                raise _anthropic_error(429, "rate_limit_error", str(e)) from e
            raise _anthropic_error(401, "authentication_error", str(e)) from e
        finally:
            # Released by JobLeaseMiddleware when the response finishes.
            if lease_sink:
                request.state.job_lease = lease_sink[0]
    return resolved_key


@router.post("/messages", tags=["messages"])
async def create_message(
    req: AnthropicMessagesRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    api_key: str | None = Depends(get_optional_api_key_strict),
):
    """Anthropic Messages API endpoint.

    Point Claude Code at the gateway with:
        ANTHROPIC_BASE_URL=https://api.gatewayz.ai
        ANTHROPIC_AUTH_TOKEN=<gatewayz key>
    """
    resolved_key = _resolve_checked_api_key(request, api_key)

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
        # Only Anthropic understands a cache breakpoint on a tool result.
        keep_tool_result_cache_control="claude" in req.model.lower(),
    )

    proxy_request = ProxyRequest(
        model=req.model,
        messages=openai_messages,
        stream=bool(req.stream),
        # Anthropic streams always end with usage. The chat pipeline sends a
        # usage chunk only to callers that ask, so without this every
        # OpenAI-shape provider reported 0 input and 0 output tokens here.
        **({"stream_options": {"include_usage": True}} if req.stream else {}),
        # Never on this route. Auto web search rewrites the prompt with an
        # injected system message and sends the query to a search vendor; an
        # agent's context is source code and tool output, and it brings its own
        # tools when it wants to search.
        auto_web_search=False,
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
        message = _detail_message(exc)
        if _is_plan_period_limit(exc.status_code, message):
            # 402 like the other "pay or wait" conditions: not retried by SDKs.
            raise _anthropic_error(
                402, "invalid_request_error", _plan_limit_message(message)
            ) from exc
        raise _anthropic_error(
            exc.status_code,
            _anthropic_error_type(exc.status_code, _detail_code(exc)),
            _safe_error_message(message),
        ) from exc

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
    # Job keys only: the content fingerprint of this request's usage line.
    content_hash = getattr(getattr(request, "state", None), "gatewayz_content_hash", None)
    if content_hash:
        return JSONResponse(content=anthropic_response, headers={CONTENT_HASH_HEADER: content_hash})
    return anthropic_response


class AnthropicCountTokensRequest(BaseModel):
    """Body of ``POST /v1/messages/count_tokens``. Same shape as a message request,
    without ``max_tokens``."""

    model: str = Field(..., description="Model identifier")
    messages: list[dict[str, Any]] = Field(..., description="Conversation turns")
    system: str | list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None

    class Config:
        extra = "allow"


def _count_block_tokens(value: Any) -> int:
    """Tokens in one piece of Anthropic content: text, or any JSON-shaped block."""
    from src.utils.token_estimator import count_tokens_text

    if value is None:
        return 0
    if isinstance(value, str):
        return count_tokens_text(value)
    if isinstance(value, list):
        return sum(_count_block_tokens(item) for item in value)
    if isinstance(value, dict):
        block_type = value.get("type")
        if block_type == "text":
            return count_tokens_text(value.get("text", ""))
        if block_type in ("image", "document"):
            # Binary payloads are not text. A flat allowance keeps a base64 blob
            # from being counted as hundreds of thousands of tokens.
            return 1500
        if block_type == "tool_result":
            return 4 + _count_block_tokens(value.get("content"))
        if block_type in ("thinking", "redacted_thinking"):
            return count_tokens_text(value.get("thinking", ""))
        return count_tokens_text(json.dumps(value, separators=(",", ":")))
    return count_tokens_text(str(value))


def estimate_input_tokens(body: AnthropicCountTokensRequest) -> int:
    """Estimated input tokens for a message request.

    An estimate from the gateway's tokenizer, not the provider's count: close
    enough for a client's context meter, and it costs no upstream call.
    """
    total = _count_block_tokens(body.system)
    for message in body.messages:
        total += 4 + _count_block_tokens(message.get("content"))
    for tool in body.tools or []:
        total += _count_block_tokens(tool)
    return max(total, 1)


@router.post("/messages/count_tokens", tags=["messages"])
async def count_message_tokens(
    body: AnthropicCountTokensRequest,
    request: Request = None,
    api_key: str | None = Depends(get_optional_api_key_strict),
):
    """Anthropic ``count_tokens``: how many input tokens a request would use.

    Agent clients call this to keep their context meter accurate. Not billed.
    Requires a key, so it cannot be used as a free anonymous service.
    """
    resolved_key = _resolve_checked_api_key(request, api_key)
    if not resolved_key:
        raise _anthropic_error(401, "authentication_error", "An API key is required")
    return {"input_tokens": estimate_input_tokens(body)}


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
