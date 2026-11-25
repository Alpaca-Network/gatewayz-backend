"""Pass-through of native Anthropic requests arriving on ``/v1/messages``.

Why this exists
---------------
A request on ``/v1/messages`` is translated to the gateway's OpenAI shape, run
through the one inference pipeline, and translated back. For a Claude model
that is a round trip through a format that cannot carry everything Anthropic's
own format does: thinking blocks and their signatures, cache breakpoints on
every block type, beta features, new event types. Each of those was a defect
found one at a time, and each Anthropic release adds more.

With pass-through, the caller's original body is what the native Anthropic
client sends, and Anthropic's event stream is what the caller receives. The
request still runs through the pipeline -- authentication, credit check, rate
limits, usage recording and billing are unchanged, and the native client still
yields the OpenAI-shaped chunks the pipeline bills from. Only the bytes on the
wire to and from Anthropic stop being re-derived.

How the pieces meet
-------------------
``messages.py`` creates a :class:`Passthrough` for the request and binds it to
a context variable. The provider call happens several layers down, sometimes
on a worker thread; ``asyncio.to_thread`` copies the context, so the native
client finds the same object with :func:`current`. The client marks it
``used`` once Anthropic has accepted the request, and pushes every raw event
into it. If the request never reaches the native client (another provider
served the model, or the native call failed and the pipeline failed over),
``used`` stays false and the route translates the pipeline's output as before.
"""

from __future__ import annotations

import asyncio
import copy
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Anthropic-hosted tools that Anthropic bills per use, on top of tokens. The
# gateway has no price for them, so they are removed rather than run at a loss.
BILLED_SERVER_TOOL_PREFIXES = ("web_search", "web_fetch", "code_execution")

# Sentinel placed on the queue when the pipeline's stream has ended.
STREAM_DONE = object()


def passthrough_enabled() -> bool:
    return os.getenv("MESSAGES_PASSTHROUGH_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_claude_model(model: str | None) -> bool:
    return "claude" in (model or "").lower()


def strip_billed_server_tools(body: dict[str, Any]) -> list[str]:
    """Remove separately billed server tools from ``body`` in place.

    Returns the ``type`` of each tool removed. A ``tool_choice`` that named a
    removed tool is dropped with it.
    """
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    kept: list[Any] = []
    dropped: list[str] = []
    dropped_names: set[str] = set()
    for tool in tools:
        tool_type = tool.get("type") if isinstance(tool, dict) else None
        if isinstance(tool_type, str) and tool_type.startswith(BILLED_SERVER_TOOL_PREFIXES):
            dropped.append(tool_type)
            if tool.get("name"):
                dropped_names.add(tool["name"])
        else:
            kept.append(tool)
    if not dropped:
        return []
    if kept:
        body["tools"] = kept
    else:
        body.pop("tools", None)
        body.pop("tool_choice", None)
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("name") in dropped_names:
        body.pop("tool_choice", None)
    return dropped


@dataclass
class Passthrough:
    """One request's original Anthropic body and the raw response to relay."""

    body: dict[str, Any]
    requested_model: str
    beta: str | None = None
    version: str | None = None
    loop: asyncio.AbstractEventLoop | None = None
    queue: asyncio.Queue | None = None
    # Set by the native client once Anthropic has accepted the request.
    used: bool = False
    # Non-streaming: Anthropic's response body, as received.
    response_json: dict[str, Any] | None = None
    dropped_tools: list[str] = field(default_factory=list)

    def upstream_body(self, model: str, *, stream: bool, max_tokens: int | None) -> dict[str, Any]:
        """The body to send to Anthropic: the caller's, with the provider's model id.

        ``max_tokens`` is the pipeline's value, which the credit check may have
        lowered to what the caller can afford. It only ever lowers the caller's.
        """
        body = copy.deepcopy(self.body)
        body["model"] = model
        if stream:
            body["stream"] = True
        else:
            body.pop("stream", None)

        requested = body.get("max_tokens")
        lowered = (
            isinstance(max_tokens, int)
            and isinstance(requested, int)
            and 0 < max_tokens < requested
        )
        if lowered:
            body["max_tokens"] = max_tokens
            thinking = body.get("thinking")
            budget = thinking.get("budget_tokens") if isinstance(thinking, dict) else None
            if isinstance(budget, int) and budget >= max_tokens:
                # Anthropic requires budget_tokens < max_tokens and >= 1024.
                if max_tokens > 1024:
                    thinking["budget_tokens"] = max_tokens - 1
                else:
                    body.pop("thinking", None)
        return body

    def upstream_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Provider headers plus the caller's ``anthropic-beta`` / ``anthropic-version``.

        Nothing else from the caller is forwarded; the provider key is ours.
        """
        out = dict(headers)
        if self.version:
            out["anthropic-version"] = self.version
        if self.beta:
            out["anthropic-beta"] = self.beta
        return out

    def push_event(self, event: str | None, data: str) -> None:
        """Hand one raw SSE event to the route. Safe to call from a worker thread."""
        if self.loop is None or self.queue is None:
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("event", event, data))


_current: ContextVar[Passthrough | None] = ContextVar("anthropic_passthrough", default=None)


def bind(passthrough: Passthrough | None):
    """Bind ``passthrough`` to the current context. Returns the reset token."""
    return _current.set(passthrough)


def unbind(token) -> None:
    try:
        _current.reset(token)
    except ValueError:
        # Reset from a different context (a streaming response finishing on
        # another task). The variable dies with its context either way.
        pass


def current() -> Passthrough | None:
    return _current.get()
