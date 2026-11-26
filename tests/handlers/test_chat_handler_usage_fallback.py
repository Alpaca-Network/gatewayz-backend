"""Streaming billing when the provider's usage is missing or the stream is cut
short (src/handlers/chat_handler.py, anthropic_native_client.py).

* Without provider usage, input is the estimate of every input the request
  sent, never less than the pre-check's estimate; output counts text,
  tool-call arguments and reasoning.
* Input usage reported when the stream began (Anthropic message_start) is
  billed when the stream ends before the final usage.
* A provider error after output reached the caller bills what was produced,
  once.
* Each billed stream counts once against its API key (request counter and
  rate-limit tokens).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage
from src.services.usage_normalization import EARLY_INPUT_USAGE_FIELD
from src.utils.token_estimator import count_tokens_text, estimate_request_input_tokens

REF = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"


def _chunk(content=None, usage=None, finish=None, tool_calls=None):
    delta = SimpleNamespace(content=content, role=None, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _handler():
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.user = {"id": 1, "key_id": 9}
    h.is_anonymous = False
    h._billing_ref = lambda: REF
    return h


def _req(content="a prompt"):
    return InternalChatRequest(
        messages=[InternalMessage(role="user", content=content)],
        model="openai/gpt-4o",
        stream=True,
    )


async def _drain():
    from src.handlers import chat_handler

    pending = list(chat_handler._BILLING_TASKS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class _Env:
    def __init__(self, handler, items, precheck_tokens=0):
        self.handler = handler
        self.items = items  # chunks, or an Exception to raise at that point
        self.precheck_tokens = precheck_tokens
        self.deduct = MagicMock()
        self.save = MagicMock()
        self.increment = MagicMock()
        self.rate = MagicMock()

    def _stream(self):
        items = self.items

        async def gen():
            for item in items:
                if isinstance(item, Exception):
                    raise item
                yield item

        return gen()

    def __enter__(self):
        h = self.handler

        async def _noop(*a, **k):
            return None

        async def _credit(*a, **k):
            h._precheck_input_tokens = self.precheck_tokens
            return 100

        self._patches = [
            patch.object(h, "_initialize_user_context", _noop),
            patch.object(h, "_check_credit_sufficiency", _credit),
            patch.object(h, "_call_provider_stream", lambda *a, **k: self._stream()),
            patch(
                "src.handlers.chat_handler.get_selector",
                return_value=MagicMock(registry=MagicMock(get_model=MagicMock(return_value=None))),
            ),
            patch(
                "src.handlers.chat_handler._loss_proof_cost_split",
                side_effect=lambda m, pm, p, c, *a, **k: ((p + c) * 1e-5, p * 1e-5, c * 1e-5),
            ),
            patch(
                "src.services.model_transformations.detect_provider_from_model_id",
                return_value="openai",
            ),
            patch(
                "src.services.model_transformations.transform_model_id",
                side_effect=lambda m, p: m,
            ),
            patch("src.handlers.chat_handler.deduct_credits", self.deduct),
            patch("src.handlers.chat_handler.record_usage"),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost", self.save),
            patch("src.db.api_keys.increment_api_key_usage", self.increment),
            patch("src.db.rate_limits.update_rate_limit_usage", self.rate),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    def billed(self):
        assert self.deduct.call_count == 1
        return self.deduct.call_args.args[3]


@pytest.mark.asyncio
async def test_missing_usage_never_bills_less_input_than_the_precheck_estimated():
    h = _handler()
    with _Env(h, [_chunk("hello", finish="stop")], precheck_tokens=5000) as env:
        async for _ in h.process_stream(_req()):
            pass
        await _drain()
    assert env.billed()["prompt_tokens"] == 5000


@pytest.mark.asyncio
async def test_missing_usage_counts_list_content_beyond_the_precheck():
    h = _handler()
    text = "word " * 3000
    req = InternalChatRequest(
        messages=[InternalMessage(role="user", content=[{"type": "text", "text": text}])],
        model="openai/gpt-4o",
        stream=True,
    )
    with _Env(h, [_chunk("ok", finish="stop")], precheck_tokens=10) as env:
        async for _ in h.process_stream(req):
            pass
        await _drain()
    expected = estimate_request_input_tokens([{"role": "user", "content": req.messages[0].content}])
    assert env.billed()["prompt_tokens"] == expected
    assert expected >= count_tokens_text(text)


@pytest.mark.asyncio
async def test_missing_usage_counts_tool_call_arguments_as_output():
    h = _handler()
    args = '{"path": "' + "src/" * 500 + '"}'
    call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "read"}}
    call["function"]["arguments"] = args
    with _Env(h, [_chunk(tool_calls=[call], finish="tool_calls")]) as env:
        async for _ in h.process_stream(_req()):
            pass
        await _drain()
    assert env.billed()["completion_tokens"] >= count_tokens_text(args)


@pytest.mark.asyncio
async def test_input_reported_at_stream_start_is_billed_when_the_stream_is_cut_short():
    h = _handler()
    early = {
        EARLY_INPUT_USAGE_FIELD: {
            "prompt_tokens": 9000,
            "cache_read_input_tokens": 8000,
            "cache_creation_input_tokens": 0,
            "completion_tokens": 1,
        },
        "choices": [],
    }
    with _Env(h, [early, _chunk("partial answer"), _chunk("never seen")]) as env:
        agen = h.process_stream(_req())
        await agen.__anext__()  # the caller got the first text chunk
        await agen.aclose()  # and disconnected before the final usage
        await _drain()
    billed = env.billed()
    assert billed["prompt_tokens"] == 9000
    assert billed["completion_tokens"] >= count_tokens_text("partial answer")


@pytest.mark.asyncio
async def test_a_provider_error_after_output_bills_what_was_produced_once():
    h = _handler()
    items = [_chunk("some output " * 20), RuntimeError("upstream reset")]
    with _Env(h, items, precheck_tokens=300) as env:
        with pytest.raises(RuntimeError, match="upstream reset"):
            async for _ in h.process_stream(_req()):
                pass
        await _drain()
        billed = env.billed()  # exactly one charge
        statuses = [c.kwargs["status"] for c in env.save.call_args_list]
    assert billed["prompt_tokens"] == 300
    assert billed["completion_tokens"] >= count_tokens_text("some output " * 20)
    assert statuses == ["completed"]  # no second, failed row for the same request
    assert h._stream_billed is True


@pytest.mark.asyncio
async def test_a_provider_error_before_any_output_bills_nothing():
    """The request may still fail over to another provider, which bills."""
    h = _handler()
    with _Env(h, [RuntimeError("refused")]) as env:
        with pytest.raises(RuntimeError):
            async for _ in h.process_stream(_req()):
                pass
        await _drain()
    env.deduct.assert_not_called()


@pytest.fixture
def stream_key_usage_on(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "STREAM_KEY_USAGE_ENABLED", True)


@pytest.mark.asyncio
async def test_streams_do_not_count_against_the_key_while_the_switch_is_off(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "STREAM_KEY_USAGE_ENABLED", False)
    h = _handler()
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    with _Env(h, [_chunk("hi"), _chunk("!", usage=usage, finish="stop")]) as env:
        async for _ in h.process_stream(_req()):
            pass
        await _drain()
    env.increment.assert_not_called()
    env.rate.assert_not_called()


@pytest.mark.asyncio
async def test_a_stream_counts_once_against_its_key(stream_key_usage_on):
    h = _handler()
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    with _Env(h, [_chunk("hi"), _chunk("!", usage=usage, finish="stop")]) as env:
        async for _ in h.process_stream(_req()):
            pass
        await _drain()
        # A second billing attempt for the same stream changes nothing.
        await h._bill_stream(
            model="m",
            provider_used="openai",
            provider_model_id="m",
            messages=[],
            accumulated_content="",
            prompt_tokens=1,
            completion_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cancelled=True,
        )
    env.increment.assert_called_once_with("gw_key")
    env.rate.assert_called_once_with("gw_key", 15)


@pytest.mark.asyncio
async def test_a_cancelled_stream_counts_once_against_its_key(stream_key_usage_on):
    h = _handler()
    with _Env(h, [_chunk("partial"), _chunk("never seen")]) as env:
        agen = h.process_stream(_req())
        await agen.__anext__()
        await agen.aclose()
        await _drain()
    env.increment.assert_called_once_with("gw_key")


def test_the_native_client_hands_over_input_usage_as_the_stream_begins():
    from src.services.providers import anthropic_native_client as native

    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 20,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 100,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 0,
                        "ephemeral_1h_input_tokens": 100,
                    },
                    "output_tokens": 1,
                }
            },
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
    ]
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    response = MagicMock()
    response.iter_lines.return_value = iter(lines)
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value = response
    client = MagicMock()
    client.stream.return_value = stream_ctx

    seen = []
    with (
        patch.object(native.httpx, "Client", return_value=client),
        patch.object(native.Config, "ANTHROPIC_API_KEY", "k"),
        patch.object(native.anthropic_passthrough, "current", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="overloaded"):
            for chunk in native.make_anthropic_native_request_stream(
                [{"role": "user", "content": "x"}], "claude-sonnet-4-5"
            ):
                seen.append(chunk)

    early = [c for c in seen if EARLY_INPUT_USAGE_FIELD in c]
    assert len(early) == 1
    assert early[0]["choices"] == []  # nothing for the caller
    usage = early[0][EARLY_INPUT_USAGE_FIELD]
    assert usage["prompt_tokens"] == 620
    assert usage["cache_read_input_tokens"] == 500
    assert usage["cache_creation_input_tokens"] == 100
    assert usage["cache_creation"]["ephemeral_1h_input_tokens"] == 100
    # It arrives before the first text chunk.
    first_text = next(
        i
        for i, c in enumerate(seen)
        if c.get("choices") and c["choices"][0]["delta"].get("content")
    )
    assert seen.index(early[0]) < first_text
