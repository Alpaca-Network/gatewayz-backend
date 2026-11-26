"""Reasoning returned by reasoning models reaches /v1/chat/completions callers."""

import asyncio

from src.adapters.chat.openai import OpenAIChatAdapter
from src.schemas.internal.chat import InternalChatResponse, InternalStreamChunk, InternalUsage


def _response(**kwargs):
    return InternalChatResponse(
        id="r",
        model="moonshot/kimi-k2.6",
        content="391",
        finish_reason="stop",
        usage=InternalUsage(prompt_tokens=20, completion_tokens=155, total_tokens=175),
        provider_used="moonshot",
        cost_usd=0.0,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
        **kwargs,
    )


def test_message_carries_reasoning_content():
    out = OpenAIChatAdapter().from_internal_response(_response(reasoning_content="17*23=391"))
    assert out["choices"][0]["message"]["reasoning_content"] == "17*23=391"


def test_no_reasoning_field_when_there_is_none():
    out = OpenAIChatAdapter().from_internal_response(_response())
    assert "reasoning_content" not in out["choices"][0]["message"]


def test_stream_delta_carries_reasoning_content():
    async def stream():
        yield InternalStreamChunk(id="r", model="m", created=1, reasoning_content="thinking")
        yield InternalStreamChunk(id="r", model="m", created=1, content="391")

    async def collect():
        return [c async for c in OpenAIChatAdapter().from_internal_stream(stream())]

    lines = "".join(str(c) for c in asyncio.run(collect()))
    assert "thinking" in lines and "reasoning_content" in lines
    assert "391" in lines
