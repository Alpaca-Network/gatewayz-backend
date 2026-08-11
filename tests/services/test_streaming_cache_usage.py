"""Streaming must report the cache tokens it bills for."""

import json

import pytest

from src.adapters.chat.openai import OpenAIChatAdapter
from src.schemas.internal.chat import InternalStreamChunk, InternalUsage


def _chunk(**usage_kw):
    return InternalStreamChunk(
        id="x",
        model="m",
        created=1,
        content="hi",
        finish_reason="stop",
        usage=InternalUsage(prompt_tokens=100, completion_tokens=10, total_tokens=110, **usage_kw),
    )


async def _collect(chunk):
    async def gen():
        yield chunk

    out = []
    async for line in OpenAIChatAdapter().from_internal_stream(gen()):
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            out.append(json.loads(line[6:].strip()))
    return out


class TestStreamingCacheUsage:
    """Billing already used these counts; the client could not see them.

    Verified in production 2026-08-11: non-streaming reported
    cache_creation_input_tokens=3201 while an identical streaming request
    reported none. The charge was correct either way, so a caller was billed
    the cache rate with no way to verify the saving.
    """

    @pytest.mark.asyncio
    async def test_cache_tokens_reach_the_sse_usage_chunk(self):
        chunks = await _collect(_chunk(cache_read_input_tokens=900, cache_creation_input_tokens=50))
        usage = [c for c in chunks if "usage" in c][0]["usage"]
        assert usage["cache_read_input_tokens"] == 900
        assert usage["cache_creation_input_tokens"] == 50
        assert usage["prompt_tokens_details"]["cached_tokens"] == 900

    @pytest.mark.asyncio
    async def test_absent_cache_fields_are_not_invented(self):
        chunks = await _collect(_chunk())
        usage = [c for c in chunks if "usage" in c][0]["usage"]
        assert "cache_read_input_tokens" not in usage

    @pytest.mark.asyncio
    async def test_core_token_counts_are_unchanged(self):
        chunks = await _collect(_chunk(cache_read_input_tokens=900))
        usage = [c for c in chunks if "usage" in c][0]["usage"]
        assert usage["prompt_tokens"] == 100
        assert usage["total_tokens"] == 110
