"""Legacy OpenAI completions endpoint (``POST /v1/completions``).

Some older tools and SDK code paths still probe or use the pre-chat completions
API. Rather than maintain a second inference path, this translates the legacy
shape into a chat request, runs it through ``chat_completions``, and translates
the response back — so billing, rate limiting and failover are identical and
there is still exactly one place where inference happens.

The translation is lossy in one direction that matters and is documented on the
endpoint: a text completion has no roles, so the prompt becomes a single user
message. Instruction-tuned models will answer it conversationally rather than
continuing the text. Callers that need true continuation semantics should use a
base model, and the response says so rather than letting them discover it.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.schemas.proxy import ProxyRequest
from src.security.deps import get_optional_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["completions"])


class CompletionsRequest(BaseModel):
    """Legacy OpenAI text-completions request."""

    model: str
    prompt: str | list[str] = Field(..., description="Prompt text (or list of prompts)")
    max_tokens: int | None = Field(None, ge=1)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    n: int | None = Field(None, ge=1)
    stream: bool | None = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    user: str | None = None
    # Accepted and ignored — these have no chat equivalent.
    suffix: str | None = None
    echo: bool | None = None
    logprobs: int | None = None
    best_of: int | None = None

    class Config:
        extra = "allow"


def normalize_prompt(prompt: str | list[str]) -> tuple[str, list[str]]:
    """Collapse the prompt to a single string, reporting what was dropped.

    The legacy API accepts an array of prompts and returns one choice per
    prompt. Chat completions has no equivalent, so only the first is used —
    said out loud rather than silently truncated, because a caller batching 20
    prompts and getting one answer back deserves to know why.
    """
    warnings: list[str] = []
    if isinstance(prompt, list):
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt must not be empty")
        if len(prompt) > 1:
            warnings.append(
                f"{len(prompt)} prompts supplied; only the first was used. "
                "Batched prompts are not supported via the chat pipeline — send "
                "separate requests."
            )
        return prompt[0], warnings
    return prompt, warnings


def unsupported_params(req: CompletionsRequest) -> list[str]:
    """Legacy params with no chat-completions equivalent."""
    dropped = []
    if req.suffix is not None:
        dropped.append("suffix")
    if req.echo:
        dropped.append("echo")
    if req.logprobs is not None:
        dropped.append("logprobs (integer form)")
    if req.best_of is not None and req.best_of > 1:
        dropped.append("best_of")
    return dropped


def chat_response_to_completion(
    chat_response: dict[str, Any], model: str, completion_id: str
) -> dict[str, Any]:
    """Translate a chat completion response into the legacy shape."""
    choices = []
    for choice in chat_response.get("choices", []) or []:
        message = choice.get("message") or {}
        choices.append(
            {
                "index": choice.get("index", 0),
                "text": message.get("content") or "",
                "logprobs": None,
                "finish_reason": choice.get("finish_reason", "stop"),
            }
        )

    return {
        "id": completion_id,
        "object": "text_completion",
        "created": int(time.time()),
        "model": chat_response.get("model", model),
        "choices": choices,
        "usage": chat_response.get("usage", {}),
    }


@router.post("/completions", tags=["completions"])
async def create_completion(
    req: CompletionsRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    api_key: str | None = Depends(get_optional_api_key),
):
    """Legacy text completions.

    Translated to a chat completion internally. Prefer ``/v1/chat/completions``
    for anything new — this exists for tools that have not migrated.
    """
    prompt_text, warnings = normalize_prompt(req.prompt)

    dropped = unsupported_params(req)
    if dropped:
        warnings.append(
            "Parameters with no chat-completions equivalent were ignored: "
            + ", ".join(dropped)
        )

    if req.stream:
        # The legacy streaming chunk shape differs from chat's, and no target
        # tool needs it. Refusing is better than emitting chat-shaped chunks
        # that a legacy client will silently mis-parse into empty output.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "streaming_not_supported",
                "message": (
                    "Streaming is not supported on the legacy /v1/completions "
                    "endpoint. Use /v1/chat/completions for streaming."
                ),
            },
        )

    proxy_request = ProxyRequest(
        model=req.model,
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        stop=req.stop,
        seed=req.seed,
        user=req.user,
        stream=False,
        **(
            {"presence_penalty": req.presence_penalty}
            if req.presence_penalty is not None
            else {}
        ),
        **(
            {"frequency_penalty": req.frequency_penalty}
            if req.frequency_penalty is not None
            else {}
        ),
    )

    from src.routes.chat import chat_completions

    result = await chat_completions(
        req=proxy_request,
        background_tasks=background_tasks,
        api_key=api_key,
        session_id=None,
        request=request,
    )

    if isinstance(result, StreamingResponse):
        raise HTTPException(status_code=500, detail="unexpected streaming response")
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="unexpected upstream response shape")

    completion = chat_response_to_completion(
        result, req.model, f"cmpl-{uuid.uuid4().hex[:24]}"
    )

    if warnings:
        # Non-standard field, but silently doing something different from what
        # was asked is worse than an extra key a legacy client will ignore.
        completion["gatewayz_warnings"] = warnings

    return completion
