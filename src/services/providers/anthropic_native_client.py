"""Native Anthropic Messages API transport.

Why this exists
---------------
``anthropic_client.py`` talks to Anthropic through their OpenAI-compatibility
endpoint. That endpoint is convenient but it does **not** support prompt
caching: ``cache_control`` markers are ignored and no cache token counts come
back. For coding agents -- long, mostly-static system prompts and file context
replayed on every turn -- caching is the single biggest cost lever there is, so
routing Claude traffic through the compatibility shim throws away the majority
of the achievable savings.

This module speaks the native Messages API instead, which supports
``cache_control`` and reports ``cache_creation_input_tokens`` /
``cache_read_input_tokens`` in its usage block. Those counts feed the
cache-aware pricing in ``src/services/pricing/pricing.py`` so cache reads are
billed at the cache rate rather than the full input rate.

Shape contract
--------------
Requests arrive in OpenAI shape (that is the gateway's internal lingua franca)
and responses are returned in OpenAI shape, so this client is a drop-in
replacement for ``make_anthropic_request`` from the handler's point of view.
``_rfield`` in ``chat_handler`` accepts dict-shaped responses, so we return
plain dicts rather than synthesising SDK objects.

The reverse direction (native Anthropic requests arriving at ``/v1/messages``)
is handled by ``anthropic_transformer.py``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Iterator

import httpx

from src.config import Config
from src.services.connection_pool import get_http_client
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# Anthropic requires max_tokens on every request; OpenAI treats it as optional.
DEFAULT_MAX_TOKENS = 4096

# Anthropic stop_reason -> OpenAI finish_reason
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "pause_turn": "stop",
    "refusal": "content_filter",
}


def _headers() -> dict[str, str]:
    if not Config.ANTHROPIC_API_KEY:
        raise ValueError("Anthropic API key not configured")
    return {
        "x-api-key": Config.ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


# --------------------------------------------------------------------------
# Request: OpenAI -> Anthropic
# --------------------------------------------------------------------------


def _content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalise OpenAI message content into Anthropic content blocks.

    ``cache_control`` markers are preserved verbatim -- they are the entire
    reason this transport exists.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []

    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue

        part_type = part.get("type")
        # Preserve a cache breakpoint if the caller set one on this block.
        cache_control = part.get("cache_control")

        if part_type == "text":
            block: dict[str, Any] = {"type": "text", "text": part.get("text", "")}
        elif part_type == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                # data:<media_type>;base64,<data>
                try:
                    header, data = url.split(",", 1)
                    media_type = header.split(":", 1)[1].split(";", 1)[0]
                except (ValueError, IndexError):
                    logger.warning("Malformed data URL in message content; skipping block")
                    continue
                block = {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            else:
                block = {"type": "image", "source": {"type": "url", "url": url}}
        else:
            # Pass through anything already in Anthropic shape (text/image/
            # document/tool_use/tool_result blocks a caller supplied directly).
            block = dict(part)

        if cache_control:
            block["cache_control"] = cache_control
        blocks.append(block)

    return blocks


def _openai_tools_to_anthropic(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI function-tool definitions to Anthropic tool definitions."""
    if not tools:
        return None

    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or {}
        name = fn.get("name") or tool.get("name")
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "input_schema": fn.get("parameters") or tool.get("input_schema") or {"type": "object"},
        }
        description = fn.get("description") or tool.get("description")
        if description:
            entry["description"] = description
        # Tool definitions are a common cache breakpoint for coding agents.
        if tool.get("cache_control"):
            entry["cache_control"] = tool["cache_control"]
        converted.append(entry)

    return converted or None


def _openai_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    """Convert OpenAI tool_choice to the Anthropic equivalent."""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return {
            "auto": {"type": "auto"},
            "required": {"type": "any"},
            "none": {"type": "none"},
        }.get(tool_choice)
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        name = fn.get("name") or tool_choice.get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def _assistant_tool_calls_to_blocks(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert an OpenAI assistant tool_calls array to Anthropic tool_use blocks."""
    blocks: list[dict[str, Any]] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            # A malformed arguments blob is the model's fault, not ours. Forward
            # it as an empty object rather than failing the whole turn.
            logger.warning("Could not parse tool_call arguments as JSON; sending empty input")
            parsed = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", f"toolu_{uuid.uuid4().hex[:16]}"),
                "name": fn.get("name", ""),
                "input": parsed,
            }
        )
    return blocks


def build_anthropic_payload(
    messages: list[dict[str, Any]],
    model: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a native Anthropic Messages payload from OpenAI-shaped inputs.

    System messages are hoisted into the top-level ``system`` field (Anthropic
    has no system role), consecutive tool results are folded into user turns,
    and ``cache_control`` markers survive on every block that carried one.
    """
    system_blocks: list[dict[str, Any]] = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role in ("system", "developer"):
            system_blocks.extend(_content_to_anthropic_blocks(content))
            continue

        if role == "tool" or role == "function":
            # Anthropic models tool results as a user turn containing
            # tool_result blocks. Merge into the previous user turn when
            # possible so consecutive results stay in one message.
            block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": (
                    content if isinstance(content, str) else _content_to_anthropic_blocks(content)
                ),
            }
            if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                prev = anthropic_messages[-1]
                if isinstance(prev["content"], list):
                    prev["content"].append(block)
                    continue
            anthropic_messages.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            blocks = _content_to_anthropic_blocks(content)
            if msg.get("tool_calls"):
                blocks.extend(_assistant_tool_calls_to_blocks(msg["tool_calls"]))
            if blocks:
                anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        # user (and any unrecognised role, treated as user)
        blocks = _content_to_anthropic_blocks(content)
        if blocks:
            anthropic_messages.append({"role": "user", "content": blocks})

    payload: dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        # Anthropic rejects requests without max_tokens.
        "max_tokens": kwargs.get("max_tokens") or DEFAULT_MAX_TOKENS,
    }

    if system_blocks:
        payload["system"] = system_blocks

    if kwargs.get("temperature") is not None:
        payload["temperature"] = kwargs["temperature"]
    if kwargs.get("top_p") is not None:
        payload["top_p"] = kwargs["top_p"]
    if kwargs.get("top_k") is not None:
        payload["top_k"] = kwargs["top_k"]

    stop = kwargs.get("stop")
    if stop:
        payload["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)

    tools = _openai_tools_to_anthropic(kwargs.get("tools"))
    if tools:
        payload["tools"] = tools
        choice = _openai_tool_choice_to_anthropic(kwargs.get("tool_choice"))
        if choice:
            # disable_parallel_tool_use is the Anthropic spelling of
            # parallel_tool_calls=False.
            if kwargs.get("parallel_tool_calls") is False and choice["type"] in ("auto", "any"):
                choice = {**choice, "disable_parallel_tool_use": True}
            payload["tool_choice"] = choice

    if kwargs.get("user"):
        payload["metadata"] = {"user_id": str(kwargs["user"])}

    return payload


# --------------------------------------------------------------------------
# Response: Anthropic -> OpenAI
# --------------------------------------------------------------------------


def anthropic_response_to_openai(data: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a native Anthropic Messages response to OpenAI chat-completion shape.

    Cache token counts are carried through on ``usage`` so the billing layer can
    price cache reads and cache writes at their own rates.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in data.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", f"call_{uuid.uuid4().hex[:16]}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = data.get("usage", {}) or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0

    openai_usage: dict[str, Any] = {
        # Anthropic reports input_tokens EXCLUDING cached tokens, so the honest
        # prompt_tokens total is the sum of all three input classes.
        "prompt_tokens": input_tokens + cache_creation + cache_read,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + cache_creation + cache_read + output_tokens,
    }
    if cache_creation or cache_read:
        openai_usage["cache_creation_input_tokens"] = cache_creation
        openai_usage["cache_read_input_tokens"] = cache_read
        # OpenAI's own spelling, so generic clients can read it too.
        openai_usage["prompt_tokens_details"] = {"cached_tokens": cache_read}

    return {
        "id": data.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("model", model),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _STOP_REASON_MAP.get(data.get("stop_reason") or "", "stop"),
            }
        ],
        "usage": openai_usage,
    }


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def request_uses_caching(messages: list[dict[str, Any]], **kwargs: Any) -> bool:
    """True when the request carries at least one ``cache_control`` marker.

    Used by the router to decide whether a Claude request must take the native
    transport (caching supported) or may stay on the cheaper-to-maintain
    OpenAI-compatibility path.
    """
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("cache_control"):
                    return True
    for tool in kwargs.get("tools") or []:
        if isinstance(tool, dict) and tool.get("cache_control"):
            return True
    return False


def make_anthropic_native_request(messages, model, **kwargs):
    """Non-streaming call to the native Anthropic Messages API.

    Returns an OpenAI-shaped dict (``_rfield`` in the handler reads dicts and
    SDK objects alike).
    """
    payload = build_anthropic_payload(messages, model, **kwargs)

    logger.info("Making native Anthropic request with model: %s", sanitize_for_logging(model))
    logger.debug(
        "Native Anthropic payload: messages=%d, system=%s, tools=%d, cached=%s",
        len(payload.get("messages", [])),
        bool(payload.get("system")),
        len(payload.get("tools", []) or []),
        request_uses_caching(messages, **kwargs),
    )

    client = get_http_client()
    response = client.post(
        f"{ANTHROPIC_API_BASE}/v1/messages",
        headers=_headers(),
        json=payload,
        timeout=120.0,
    )
    response.raise_for_status()
    return anthropic_response_to_openai(response.json(), model)


def make_anthropic_native_request_stream(messages, model, **kwargs) -> Iterator[dict[str, Any]]:
    """Streaming call to the native Anthropic Messages API.

    Yields OpenAI-shaped ``chat.completion.chunk`` dicts, matching the sync
    iterator contract in ``providers/base.py``.

    Connection setup happens eagerly, before the generator is returned, so that
    auth and connection failures raise at call time like every other provider
    client rather than surfacing on first iteration. Deferring them would
    defeat the failover logic in ``chat_dispatch``, which only retries a
    different provider if the error arrives before the first chunk.
    """
    payload = build_anthropic_payload(messages, model, **kwargs)
    payload["stream"] = True

    # Validate credentials eagerly -- _headers() raises when the key is absent.
    headers = _headers()

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    # Index of the tool_call currently being streamed, and its accumulated
    # partial JSON. Anthropic streams tool arguments as input_json_delta
    # fragments that must be forwarded as OpenAI arguments deltas.
    tool_index = -1
    usage_totals: dict[str, int] = {}

    # Open the connection now so HTTP/auth errors raise from this call rather
    # than from the first next() on the returned generator.
    client = httpx.Client(timeout=300.0)
    try:
        stream_ctx = client.stream(
            "POST",
            f"{ANTHROPIC_API_BASE}/v1/messages",
            headers=headers,
            json=payload,
        )
        response = stream_ctx.__enter__()
        response.raise_for_status()
    except Exception:
        client.close()
        raise

    def _generate() -> Iterator[dict[str, Any]]:
        nonlocal tool_index, usage_totals
        try:
            yield _chunk({"role": "assistant", "content": ""})

            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[len("data: ") :].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Skipping unparseable Anthropic SSE line")
                    continue

                event_type = event.get("type")

                if event_type == "message_start":
                    usage = (event.get("message") or {}).get("usage") or {}
                    usage_totals = {
                        "prompt_tokens": (usage.get("input_tokens", 0) or 0)
                        + (usage.get("cache_creation_input_tokens", 0) or 0)
                        + (usage.get("cache_read_input_tokens", 0) or 0),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0)
                        or 0,
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                    }

                elif event_type == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        tool_index += 1
                        yield _chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": tool_index,
                                        "id": block.get("id", ""),
                                        "type": "function",
                                        "function": {
                                            "name": block.get("name", ""),
                                            "arguments": "",
                                        },
                                    }
                                ]
                            }
                        )

                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    delta_type = delta.get("type")
                    if delta_type == "text_delta":
                        yield _chunk({"content": delta.get("text", "")})
                    elif delta_type == "input_json_delta" and tool_index >= 0:
                        yield _chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": tool_index,
                                        "function": {"arguments": delta.get("partial_json", "")},
                                    }
                                ]
                            }
                        )

                elif event_type == "message_delta":
                    delta = event.get("delta") or {}
                    stop_reason = delta.get("stop_reason")
                    usage = event.get("usage") or {}
                    if usage.get("output_tokens") is not None:
                        usage_totals["completion_tokens"] = usage["output_tokens"]
                    if stop_reason:
                        final = _chunk({}, _STOP_REASON_MAP.get(stop_reason, "stop"))
                        if usage_totals:
                            prompt = usage_totals.get("prompt_tokens", 0)
                            completion = usage_totals.get("completion_tokens", 0)
                            final["usage"] = {
                                **usage_totals,
                                "completion_tokens": completion,
                                "total_tokens": prompt + completion,
                            }
                        yield final

                elif event_type == "error":
                    err = event.get("error") or {}
                    raise RuntimeError(
                        f"Anthropic stream error: {err.get('type')}: {err.get('message')}"
                    )
        finally:
            # Always hand the connection back to the pool, including when the
            # consumer abandons the generator mid-stream. Leaking here would
            # exhaust the pool and stall the whole gateway.
            try:
                stream_ctx.__exit__(None, None, None)
            finally:
                client.close()

    return _generate()


def process_anthropic_native_response(response):
    """Provider-contract ``process``. Responses are already OpenAI-shaped."""
    return response
