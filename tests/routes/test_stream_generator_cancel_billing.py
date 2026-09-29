"""stream_generator (chat_streaming) must schedule billing when the client aborts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.routes import chat_streaming


def _chunk(content):
    delta = SimpleNamespace(content=content, role="assistant", tool_calls=None, function_call=None)
    return SimpleNamespace(
        id="c1",
        object="chat.completion.chunk",
        created=1,
        model="gpt-4o",
        choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)],
        usage=None,
    )


async def _provider(n_after=1):
    yield _chunk("q" * 120)
    for _ in range(n_after):
        yield _chunk("tail")


def _gen():
    return chat_streaming.stream_generator(
        stream=_provider(),
        user={"id": 7},
        api_key="gw_key",
        model="gpt-4o",
        trial={"is_trial": False},
        environment_tag="live",
        session_id=None,
        messages=[{"role": "user", "content": "hello there"}],
        provider="openai",
        is_async_stream=True,
        request_id="req-1",
        api_key_id=3,
    )


async def _drain():
    pending = list(getattr(chat_streaming, "_CANCEL_BILLING_TASKS", ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_generator_close_schedules_billing_once_with_partial_usage():
    calls = []

    async def fake_bg(**kwargs):
        calls.append(kwargs)

    with patch.object(chat_streaming, "_process_stream_completion_background", fake_bg):
        agen = _gen()
        await agen.__anext__()
        await agen.aclose()
        await _drain()
        await asyncio.sleep(0)

    assert len(calls) == 1
    assert calls[0]["cancelled"] is True
    assert calls[0]["request_id"] == "req-1"
    assert calls[0]["completion_tokens"] > 0  # estimated from streamed text
    assert calls[0]["prompt_tokens"] > 0
    assert calls[0]["total_tokens"] == calls[0]["prompt_tokens"] + calls[0]["completion_tokens"]


@pytest.mark.asyncio
async def test_normal_completion_schedules_billing_exactly_once():
    calls = []

    async def fake_bg(**kwargs):
        calls.append(kwargs)

    with (
        patch.object(chat_streaming, "_process_stream_completion_background", fake_bg),
        patch.object(chat_streaming, "enforce_plan_limits", lambda *a, **k: {"allowed": True}),
    ):
        async for _ in _gen():
            pass
        await asyncio.sleep(0.05)
        await _drain()

    assert len(calls) == 1
    assert not calls[0].get("cancelled")
