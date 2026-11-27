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
BILLED_SERVER_TOOL_PREFIXES = ("web_search", "web_fetch", "code_execution", "mcp")

# Request fields forwarded to Anthropic. Everything else in the caller's body is
# dropped, because the request runs on Gatewayz's Anthropic account: fields
# like `mcp_servers` (Anthropic connects to a URL the caller chooses), `container`
# (code-execution containers) or `service_tier` / `speed` (priced higher) would
# act on that account. New Anthropic fields are added here deliberately.
FORWARDED_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "system",
        "stream",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "tools",
        "tool_choice",
        "metadata",
        "thinking",
        "output_config",
        "context_management",
        # Claude Code auto mode's safety classifier; Anthropic runs it and
        # returns safeguard_results (see SAFEGUARDS_FIELD in routes/messages.py).
        "safeguards",
    }
)

# anthropic-beta values forwarded to Anthropic; all others are dropped. An
# allowlist, because a beta can act on Gatewayz's Anthropic account (files,
# remote MCP, code execution, skills) or change its price (1M-token context is
# billed at a premium Gatewayz does not charge). Add new ones deliberately.
ALLOWED_BETA_PREFIXES = (
    "claude-code-",
    "interleaved-thinking-",
    "fine-grained-tool-streaming-",
    "context-management-",
    "dangerous-tool-use-",  # Claude Code auto mode (safeguards)
    "token-efficient-tools-",
    "prompt-caching-",
    "output-128k-",
    "effort-",
    "structured-outputs-",
    "redact-thinking-",
)


# Items buffered for one streamed response before the caller is considered
# gone. The queue holds about two items per streamed token (Anthropic's event
# and the pipeline's line); Anthropic streams on the order of 100 tokens a
# second, so this is minutes behind: a caller that has stopped reading.
MAX_QUEUED_EVENTS = 50_000


class RelayBacklog(RuntimeError):
    """The caller stopped reading a passed-through stream."""


def has_file_reference(value: Any) -> bool:
    """True if any content block refers to a stored file (``source.type == "file"``).

    Such a block makes Anthropic look up a file by id in the account that sends
    the request, which for Gatewayz is a shared account.
    """
    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, dict) and source.get("type") == "file":
            return True
        return any(has_file_reference(v) for v in value.values())
    if isinstance(value, list):
        return any(has_file_reference(v) for v in value)
    return False


# Sentinel placed on the queue when the pipeline's stream has ended.
STREAM_DONE = object()


OPT_IN_HEADER = "x-gatewayz-passthrough"
_TRUE = {"1", "true", "yes", "on"}


def passthrough_enabled(opt_in: str | None = None) -> bool:
    """Whether this request may be passed through.

    ``MESSAGES_PASSTHROUGH_ENABLED``:

    - unset / ``false``: never;
    - ``header``: only requests that send ``x-gatewayz-passthrough: true``, so
      it can be tried on chosen sessions in production before anyone else
      is affected;
    - ``true``: every Claude request on /v1/messages.
    """
    mode = os.getenv("MESSAGES_PASSTHROUGH_ENABLED", "false").strip().lower()
    if mode in _TRUE:
        return True
    if mode == "header":
        return (opt_in or "").strip().lower() in _TRUE
    return False


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
    # Context-variable token from bind(); the route passes it to unbind().
    binding: Any = None

    def upstream_body(self, model: str, *, stream: bool, max_tokens: int | None) -> dict[str, Any]:
        """The body to send to Anthropic: the caller's, with the provider's model id.

        ``max_tokens`` is the pipeline's value, which the credit check may have
        lowered to what the caller can afford. It only ever lowers the caller's.
        """
        body = {k: copy.deepcopy(v) for k, v in self.body.items() if k in FORWARDED_FIELDS}
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
        """Provider headers plus the caller's ``anthropic-version`` and allowed betas.

        Nothing else from the caller is forwarded; the provider key is ours.
        Only betas in ALLOWED_BETA_PREFIXES are kept.
        """
        out = dict(headers)
        if self.version:
            out["anthropic-version"] = self.version
        betas = [
            b.strip()
            for b in (self.beta or "").split(",")
            if b.strip().startswith(ALLOWED_BETA_PREFIXES)
        ]
        if betas:
            out["anthropic-beta"] = ",".join(betas)
        return out

    def push_event(self, event: str | None, data: str) -> None:
        """Hand one raw SSE event to the route. Safe to call from a worker thread.

        Raises RelayBacklog when the caller has stopped reading and the backlog
        passes MAX_QUEUED_EVENTS: the upstream stream is then abandoned (and
        billed for what was produced) instead of buffering in memory.
        """
        if self.loop is None or self.queue is None:
            return
        if self.queue.qsize() > MAX_QUEUED_EVENTS:
            raise RelayBacklog("caller is not reading the stream")
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
