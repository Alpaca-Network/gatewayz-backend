"""Anthropic Messages API compatibility endpoint (``POST /v1/messages``).

Why this exists
---------------
Claude Code -- the single largest coding-agent audience -- speaks the Anthropic
Messages API and is pointed at a gateway via ``ANTHROPIC_BASE_URL``. It has no
OpenAI-compatible mode. Without this route the only way to run Claude Code on
Gatewayz is to install a third-party translation proxy (Claude Code Router),
which adds an install step to the funnel and puts a dependency we do not
control on the critical path.

How a request flows
-------------------
1. The Anthropic request is translated to the gateway's internal OpenAI shape
   (``anthropic_transformer``) and handed to the one ``chat_completions``
   pipeline, so auth, credit checks, rate limits, failover and billing behave
   exactly as on the OpenAI route.
2. Claude models are additionally *passed through*: the native Anthropic client
   sends the caller's original body and the caller gets Anthropic's own events
   back (``anthropic_passthrough``), so thinking, cache breakpoints and beta
   features survive. Other models get the pipeline's output translated back.
3. Errors leave in Anthropic's envelope (``anthropic_messages.errors``);
   streams are produced by ``anthropic_messages.stream``.

Helpers live in ``src/services/anthropic_messages/``; this module only wires
them to the HTTP endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from src.services.anthropic_messages.errors import anthropic_error, to_anthropic_error
from src.services.anthropic_messages.stream import relay_passthrough_stream, translate_stream
from src.services.anthropic_messages.tokens import (
    AnthropicCountTokensRequest,
    estimate_input_tokens,
)
from src.services.auth_rate_limiting import get_client_ip
from src.services.billing.credit_precheck import INPUT_TOKENS_FLOOR_STATE
from src.services.claude_code_models import strip_claude_code_prefix
from src.services.job_usage import JOB_BUSY, job_lease_of
from src.services.job_usage_v2 import CONTENT_HASH_HEADER
from src.services.providers import anthropic_passthrough
from src.services.providers.anthropic_transformer import (
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)
from src.utils.token_estimator import (
    estimate_request_input_tokens,
    request_uses_one_hour_cache,
)

logger = logging.getLogger(__name__)


def _trusted_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    ip = get_client_ip(request)
    return None if not ip or ip == "unknown" else ip


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


# Claude Code in auto mode adds a top-level ``safeguards`` field (and an
# ``anthropic-beta: dangerous-tool-use-*`` value) asking the upstream to run
# auto mode's safety check, and reads ``safeguard_results`` from the reply.
# Pass-through forwards all three untouched. Every other path would drop the
# field silently, so the client would pay for the check as extra billed model
# calls without being told why. Claude Code's gateway contract for that case:
# answer 400 invalid_request_error naming ``safeguards``; the client then
# retries without it and runs the check itself.
SAFEGUARDS_FIELD = "safeguards"


def _safeguards_unsupported() -> HTTPException:
    return anthropic_error(
        400,
        "invalid_request_error",
        f"`{SAFEGUARDS_FIELD}` is not supported for this model on this gateway; "
        "retry without it.",
    )


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
            raise anthropic_error(500, "api_error", "unexpected upstream response shape")
        if isinstance(decoded, dict):
            return decoded
    elif isinstance(body, dict):
        return body
    raise anthropic_error(500, "api_error", "unexpected upstream response shape")


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
            raise anthropic_error(503, "api_error", "Authentication temporarily unavailable") from e
        except ValueError as e:
            if JOB_BUSY in str(e):
                raise anthropic_error(429, "rate_limit_error", str(e)) from e
            raise anthropic_error(401, "authentication_error", str(e)) from e
        finally:
            # Released by JobLeaseMiddleware when the response finishes.
            if lease_sink:
                request.state.job_lease = lease_sink[0]
    return resolved_key


def _start_passthrough(
    req: AnthropicMessagesRequest, request: Request | None
) -> anthropic_passthrough.Passthrough | None:
    """Set up pass-through for a Claude request, or return None.

    The caller's original body goes to the native Anthropic client instead of
    the one rebuilt from the OpenAI shape; the pipeline still runs on the
    translated request (credit check, limits, billing).
    """
    opt_in = request.headers.get(anthropic_passthrough.OPT_IN_HEADER) if request else None
    if not (
        anthropic_passthrough.is_claude_model(req.model)
        and anthropic_passthrough.passthrough_enabled(opt_in)
    ):
        return None
    original = req.model_dump(exclude_unset=True)
    passthrough = anthropic_passthrough.Passthrough(
        body=original,
        requested_model=req.model,
        beta=request.headers.get("anthropic-beta") if request else None,
        version=request.headers.get("anthropic-version") if request else None,
        loop=asyncio.get_running_loop(),
        queue=asyncio.Queue() if req.stream else None,
        dropped_tools=anthropic_passthrough.strip_billed_server_tools(original),
    )
    passthrough.binding = anthropic_passthrough.bind(passthrough)
    return passthrough


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
    # Ids from Claude Code's model picker: claude-code/<catalog id>.
    req.model = strip_claude_code_prefix(req.model)

    if not req.messages:
        raise anthropic_error(400, "invalid_request_error", "messages must not be empty")

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

    # The credit pre-checks run on the translated request, where documents
    # become short placeholders: they also count every input of the client's
    # own body (documents, images, tool definitions and results), and the
    # larger estimate is used. A one-hour cache marker is noted for billing.
    state = getattr(request, "state", None) if request is not None else None
    if state is not None:
        setattr(
            state,
            INPUT_TOKENS_FLOOR_STATE,
            estimate_request_input_tokens(
                req.messages, req.tools, req.tool_choice, system=req.system
            ),
        )
        state.gatewayz_cache_ttl_1h = request_uses_one_hour_cache(
            req.messages, req.tools, req.system
        )

    passthrough = _start_passthrough(req, request)
    if passthrough is None and SAFEGUARDS_FIELD in (req.model_extra or {}):
        # Refused before the pipeline runs, so nothing is billed for it.
        raise _safeguards_unsupported()
    extra_headers = (
        {"x-gatewayz-dropped": "server_tools"} if passthrough and passthrough.dropped_tools else {}
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
        raise to_anthropic_error(exc) from exc
    finally:
        # A streaming response may reach the provider only once its body is
        # iterated, so the binding stays for the life of this request's context.
        if passthrough is not None and not req.stream:
            anthropic_passthrough.unbind(passthrough.binding)

    if req.stream:
        if not isinstance(result, StreamingResponse):
            raise anthropic_error(
                500, "api_error", "upstream did not return a stream for a streaming request"
            )
        events = (
            relay_passthrough_stream(result.body_iterator, req.model, message_id, passthrough)
            if passthrough is not None
            else translate_stream(result.body_iterator, req.model, message_id)
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                **extra_headers,
            },
        )

    content_hash = getattr(getattr(request, "state", None), "gatewayz_content_hash", None)
    if content_hash:
        extra_headers = {**extra_headers, CONTENT_HASH_HEADER: content_hash}

    if passthrough is not None and passthrough.used and passthrough.response_json is not None:
        # Anthropic's own response, as received; only the model id is the
        # caller's. Billing already ran in the pipeline on the same counts.
        native_response = {**passthrough.response_json, "model": req.model}
        return JSONResponse(content=native_response, headers=extra_headers)

    openai_response = _unwrap_chat_result(result)

    anthropic_response = transform_openai_to_anthropic(
        openai_response,
        req.model,
        stop_sequences=req.stop_sequences,
    )
    anthropic_response["id"] = message_id
    # Job keys: the content fingerprint of this request's usage line.
    if extra_headers:
        return JSONResponse(content=anthropic_response, headers=extra_headers)
    return anthropic_response


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
        raise anthropic_error(401, "authentication_error", "An API key is required")
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
