"""Anthropic event streams for ``/v1/messages``.

Two ways to produce them:

- ``translate_stream``: re-emit the chat pipeline's OpenAI-shaped SSE stream
  as Anthropic events (any non-Claude model, or a Claude request that was not
  passed through).
- ``relay_passthrough_stream``: relay Anthropic's own events for a Claude
  request the native client passed through unchanged, while the pipeline's
  stream is consumed in the background for billing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from src.services.anthropic_messages.errors import (
    NON_FATAL_STREAM_ERROR_TYPES,
    STREAM_ERROR_TYPE_MAP,
    safe_error_message,
)
from src.services.job_usage_v2 import CONTENT_HASH_HEADER
from src.services.providers import anthropic_passthrough
from src.services.providers.anthropic_transformer import anthropic_input_tokens

logger = logging.getLogger(__name__)

# Job keys: the usage line's content hash, emitted by chat_dispatch before [DONE].
_CONTENT_HASH_COMMENT = f": {CONTENT_HASH_HEADER.lower()}: "


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _aclose_stream(openai_stream) -> None:
    """Close the inner generator so its finally blocks (provider connection
    release, cleanup) run now, not at GC time."""
    if hasattr(openai_stream, "aclose"):
        try:
            await openai_stream.aclose()
        except Exception:
            pass


async def translate_stream(
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
    events = translate_stream_events(openai_stream, model, message_id)
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


# OpenAI finish_reason -> Anthropic stop_reason.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}

_MID_STREAM_FAILURE = (
    "The upstream provider connection failed mid-stream. The response is incomplete."
)


def _error_event(error_type: str, message: str) -> str:
    return sse("error", {"type": "error", "error": {"type": error_type, "message": message}})


class _ContentBlocks:
    """The content blocks of one streamed message.

    Anthropic clients treat a block as finished only on its content_block_stop,
    so exactly one block is open at a time and it is closed before the next one
    starts. (Leaving earlier tool blocks open made clients keep only one of
    several parallel tool calls.) Methods return the SSE frames to send.
    """

    def __init__(self) -> None:
        self.count = 0
        self.open_index: int | None = None
        self.open_kind: str | None = None  # "thinking", "text" or "tool_use"
        self.tool_blocks: dict[int, int] = {}  # OpenAI tool_call index -> block index

    def start(self, content_block: dict[str, Any]) -> list[str]:
        frames = self.finish()
        self.open_index, self.open_kind = self.count, content_block["type"]
        self.count += 1
        frames.append(
            sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.open_index,
                    "content_block": content_block,
                },
            )
        )
        return frames

    @staticmethod
    def delta(index: int, delta: dict[str, Any]) -> str:
        return sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": index, "delta": delta},
        )

    def finish(self) -> list[str]:
        if self.open_index is None:
            return []
        frame = sse("content_block_stop", {"type": "content_block_stop", "index": self.open_index})
        self.open_index = self.open_kind = None
        return [frame]


async def translate_stream_events(openai_stream, model: str, message_id: str):
    """Re-emit an OpenAI SSE stream as Anthropic Messages stream events.

    Anthropic's event sequence is:
        message_start -> (content_block_start -> content_block_delta*
        -> content_block_stop)* -> message_delta -> message_stop

    Reasoning, text and each tool call become separate content blocks.
    """
    yield sse(
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

    blocks = _ContentBlocks()
    stop_reason = "end_turn"
    usage: dict[str, Any] = {}

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

            # A provider failure arrives as an error chunk (stream_normalizer.
            # create_error_sse_chunk). Ending the stream normally here would be a
            # fabricated success (issue #2236): send Anthropic's error event.
            if chunk.get("error"):
                err = chunk["error"] if isinstance(chunk["error"], dict) else {}
                if err.get("type") in NON_FATAL_STREAM_ERROR_TYPES:
                    continue
                yield _error_event(
                    STREAM_ERROR_TYPE_MAP.get(err.get("type"), "api_error"),
                    safe_error_message(err.get("message")),
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
                stop_reason = _STOP_REASONS.get(choice["finish_reason"], "end_turn")

            # Reasoning (Kimi, DeepSeek-R1, ...) becomes a thinking block ahead
            # of the answer; it is billed as output, so it is shown.
            reasoning = delta.get("reasoning_content")
            if reasoning and blocks.open_kind != "text" and not blocks.tool_blocks:
                if blocks.open_kind != "thinking":
                    for frame in blocks.start({"type": "thinking", "thinking": ""}):
                        yield frame
                yield blocks.delta(
                    blocks.open_index, {"type": "thinking_delta", "thinking": reasoning}
                )

            content = delta.get("content")
            if content:
                if blocks.open_kind != "text":
                    for frame in blocks.start({"type": "text", "text": ""}):
                        yield frame
                yield blocks.delta(blocks.open_index, {"type": "text_delta", "text": content})

            for tool_call in delta.get("tool_calls") or []:
                tc_index = tool_call.get("index", 0)
                fn = tool_call.get("function") or {}
                if tc_index not in blocks.tool_blocks:
                    content_block = {
                        "type": "tool_use",
                        "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": fn.get("name", ""),
                        "input": {},
                    }
                    for frame in blocks.start(content_block):
                        yield frame
                    blocks.tool_blocks[tc_index] = blocks.open_index
                if fn.get("arguments"):
                    yield blocks.delta(
                        blocks.tool_blocks[tc_index],
                        {"type": "input_json_delta", "partial_json": fn["arguments"]},
                    )
    except Exception:
        # The inner iterator raised (reset connection, read timeout). The 200
        # and maybe some content are already sent, so this cannot become an HTTP
        # error; without an error event the client would take the cut-off answer
        # as complete. CancelledError / GeneratorExit (the client left) are
        # BaseException and deliberately not caught. The exception text is not
        # sent: it can carry hostnames and request ids.
        logger.exception("Upstream stream raised mid-flight for model %s", model)
        yield _error_event("api_error", _MID_STREAM_FAILURE)
        await _aclose_stream(openai_stream)
        return

    for frame in blocks.finish():
        yield frame
    if blocks.tool_blocks:
        # A turn that made tool calls ends with tool_use, whatever the provider said.
        stop_reason = "tool_use"

    anthropic_usage = {
        "input_tokens": anthropic_input_tokens(usage),
        "output_tokens": usage.get("completion_tokens", 0),
    }
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if usage.get(key):
            anthropic_usage[key] = usage[key]

    yield sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": anthropic_usage,
        },
    )
    yield sse("message_stop", {"type": "message_stop"})


def _relay_event(name: str | None, data: str, model: str) -> tuple[str, str | None]:
    """One raw Anthropic event as an SSE frame, and its type.

    Sent on as received. The one edit: ``message_start`` reports the model id
    the caller asked for rather than the provider's own id for it.
    """
    event_type = name
    if name in (None, "message_start"):
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            event_type = name or parsed.get("type")
            message = parsed.get("message")
            if parsed.get("type") == "message_start" and isinstance(message, dict):
                message["model"] = model
                data = json.dumps(parsed)
    if event_type:
        return f"event: {event_type}\ndata: {data}\n\n", event_type
    return f"data: {data}\n\n", None


def _inner_error(raw: str) -> dict[str, Any] | None:
    """The fatal error carried by a pipeline SSE line, if it carries one."""
    if not raw or not raw.startswith("data: "):
        return None
    payload = raw[len("data: ") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return None
    err = chunk.get("error") if isinstance(chunk, dict) else None
    if not err:
        return None
    err = err if isinstance(err, dict) else {}
    if err.get("type") in NON_FATAL_STREAM_ERROR_TYPES:
        return None
    return err


# Items on a pass-through request's queue (see anthropic_passthrough):
_EVENT = "event"  # (_EVENT, name, data): a raw Anthropic event from the native client
_LINE = "line"  # (_LINE, raw): a line of the pipeline's OpenAI-shaped SSE stream
_FAILED = "failed"  # (_FAILED,): the pipeline's stream raised
_DONE = anthropic_passthrough.STREAM_DONE  # (_DONE,): the pipeline's stream ended

_ENDED_EARLY = "The upstream provider connection ended before the response was complete."


async def _first_items(queue: asyncio.Queue, passthrough) -> tuple[bool, list[tuple]]:
    """Read the queue until it is clear whether the native client served the request.

    Returns (relay, items read so far). Relay when an Anthropic event arrives or
    the native client has marked the pass-through used; translate when the
    pipeline sends its first data line, fails or ends without either.
    """
    items: list[tuple] = []
    while True:
        item = await queue.get()
        items.append(item)
        kind = item[0]
        if kind == _EVENT or (kind == _LINE and passthrough.used):
            return True, items
        if kind in (_DONE, _FAILED):
            return False, items
        if kind == _LINE and item[1] and item[1].startswith("data: "):
            return False, items


async def _all_items(queue: asyncio.Queue, first: list[tuple]):
    for item in first:
        yield item
    while True:
        yield await queue.get()


async def _pipeline_lines(items):
    """The pipeline's SSE lines, for translate_stream_events."""
    async for item in items:
        if item[0] is _DONE:
            return
        if item[0] == _FAILED:
            raise RuntimeError("pipeline stream failed")
        if item[0] == _LINE:
            yield item[1]


async def _relay(items, model: str):
    """Anthropic's events as received, plus pipeline errors and job-key comments."""
    finished = False  # a message_stop or error event has gone out
    async for item in items:
        kind = item[0]
        if kind is _DONE:
            if not finished:
                yield _error_event("api_error", _ENDED_EARLY)
            return
        if kind == _EVENT:
            frame, event_type = _relay_event(item[1], item[2], model)
            yield frame
            finished = finished or event_type in ("message_stop", "error")
        elif kind == _LINE:
            raw = item[1]
            if raw and raw.startswith(_CONTENT_HASH_COMMENT):
                yield raw
            elif not finished and (err := _inner_error(raw)) is not None:
                yield _error_event(
                    STREAM_ERROR_TYPE_MAP.get(err.get("type"), "api_error"),
                    safe_error_message(err.get("message")),
                )
                finished = True
        elif kind == _FAILED and not finished:
            yield _error_event("api_error", _MID_STREAM_FAILURE)
            finished = True


async def relay_passthrough_stream(
    openai_stream,
    model: str,
    message_id: str,
    passthrough: anthropic_passthrough.Passthrough,
):
    """Stream a /v1/messages response for a request that may be passed through.

    The pipeline's stream is always consumed to the end in the background: that
    is what records usage and bills. What the caller receives depends on whether
    the native Anthropic client served the request:

    - it did: Anthropic's own events, as received (``_relay``);
    - it did not (another provider served the model, or a failover): the
      pipeline's chunks translated, exactly as without pass-through.
    """
    queue = passthrough.queue

    async def pump():
        try:
            async for raw in openai_stream:
                queue.put_nowait((_LINE, raw))
        except Exception:
            logger.exception("Pipeline stream raised mid-flight for model %s", model)
            queue.put_nowait((_FAILED,))
        finally:
            queue.put_nowait((_DONE,))

    pump_task = asyncio.create_task(pump())
    try:
        relay, first = await _first_items(queue, passthrough)
        items = _all_items(queue, first)
        if relay:
            frames = _relay(items, model)
        else:
            frames = translate_stream_events(_pipeline_lines(items), model, message_id)
        async for frame in frames:
            yield frame
    finally:
        # Stop the pump first: cancelling it unwinds the pipeline's generator,
        # which is what bills a response the client walked away from.
        pump_task.cancel()
        with contextlib.suppress(BaseException):
            await pump_task
        with contextlib.suppress(BaseException):
            await _aclose_stream(openai_stream)
