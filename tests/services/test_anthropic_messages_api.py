"""Tests for the Anthropic Messages compatibility endpoint.

The endpoint exists so Claude Code can point ANTHROPIC_BASE_URL at the gateway,
so the tests that matter are: the Anthropic request shape is accepted, cache
markers survive translation, the response comes back in Anthropic shape, and
x-api-key authenticates.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.routes.messages import (
    AnthropicMessagesRequest,
    _resolve_api_key,
    _stream_anthropic_events,
)
from src.services.providers.anthropic_transformer import transform_anthropic_to_openai


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class TestApiKeyResolution:
    def test_bearer_token_preferred_when_present(self):
        req = _FakeRequest({"x-api-key": "from-header"})
        assert _resolve_api_key(req, "from-bearer") == "from-bearer"

    def test_falls_back_to_x_api_key(self):
        """Claude Code sends x-api-key, not Authorization: Bearer."""
        req = _FakeRequest({"x-api-key": "gw-key-123"})
        assert _resolve_api_key(req, None) == "gw-key-123"

    def test_whitespace_stripped(self):
        req = _FakeRequest({"x-api-key": "  gw-key  "})
        assert _resolve_api_key(req, None) == "gw-key"

    def test_no_key_anywhere_returns_none(self):
        assert _resolve_api_key(_FakeRequest({}), None) is None

    def test_none_request_is_safe(self):
        assert _resolve_api_key(None, None) is None


class TestRequestSchema:
    def test_minimal_valid_request(self):
        req = AnthropicMessagesRequest(
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
        )
        assert req.max_tokens == 100
        assert req.stream is False

    def test_max_tokens_is_required(self):
        """Anthropic requires it; accepting a request without it would 400 upstream."""
        with pytest.raises(Exception):
            AnthropicMessagesRequest(
                model="claude-sonnet-4", messages=[{"role": "user", "content": "hi"}]
            )

    def test_unknown_fields_are_allowed(self):
        """Newer Anthropic params must pass through rather than 422 the client."""
        req = AnthropicMessagesRequest(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
            some_future_param={"a": 1},
        )
        assert req.model == "m"


class TestCacheControlSurvivesTranslation:
    def test_system_cache_control_preserved_as_blocks(self):
        """The coding-agent system prompt is the highest-value cache breakpoint."""
        messages, _ = transform_anthropic_to_openai(
            messages=[{"role": "user", "content": "hi"}],
            system=[
                {
                    "type": "text",
                    "text": "You are a coding agent",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            max_tokens=100,
        )
        system_msg = messages[0]
        assert system_msg["role"] == "system"
        assert isinstance(system_msg["content"], list)
        assert system_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_system_without_cache_control_still_flattens_to_string(self):
        """Non-caching requests keep the simpler string form every provider accepts."""
        messages, _ = transform_anthropic_to_openai(
            messages=[{"role": "user", "content": "hi"}],
            system=[{"type": "text", "text": "be terse"}],
            max_tokens=100,
        )
        assert messages[0]["content"] == "be terse"

    def test_user_message_cache_control_preserved(self):
        messages, _ = transform_anthropic_to_openai(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<repo context>",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": "now do the thing"},
                    ],
                }
            ],
            max_tokens=100,
        )
        blocks = messages[0]["content"]
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_string_system_prompt_unaffected(self):
        messages, _ = transform_anthropic_to_openai(
            messages=[{"role": "user", "content": "hi"}],
            system="plain string",
            max_tokens=100,
        )
        assert messages[0]["content"] == "plain string"


class TestStreamTranslation:
    async def _collect(self, chunks, model="claude-sonnet-4"):
        async def _fake_stream():
            for c in chunks:
                yield c

        events = []
        async for e in _stream_anthropic_events(_fake_stream(), model, "msg_1"):
            events.append(e)
        return events

    def _parse(self, events):
        parsed = []
        for e in events:
            for line in e.strip().split("\n"):
                if line.startswith("data: "):
                    parsed.append(json.loads(line[len("data: ") :]))
        return parsed

    @pytest.mark.asyncio
    async def test_emits_anthropic_event_sequence(self):
        chunks = [
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        ]
        events = await self._collect(chunks)
        types = [p["type"] for p in self._parse(events)]
        assert types[0] == "message_start"
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert types[-1] == "message_stop"
        assert "message_delta" in types

    @pytest.mark.asyncio
    async def test_text_delta_carries_content(self):
        chunks = ['data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}']
        parsed = self._parse(await self._collect(chunks))
        deltas = [p for p in parsed if p["type"] == "content_block_delta"]
        assert deltas[0]["delta"]["text"] == "Hi"

    @pytest.mark.asyncio
    async def test_tool_calls_become_tool_use_blocks(self):
        chunks = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"{\\"p\\":1}"}}]},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        ]
        parsed = self._parse(await self._collect(chunks))
        starts = [p for p in parsed if p["type"] == "content_block_start"]
        assert starts[0]["content_block"]["type"] == "tool_use"
        assert starts[0]["content_block"]["name"] == "read_file"
        json_deltas = [
            p
            for p in parsed
            if p["type"] == "content_block_delta"
            and p["delta"]["type"] == "input_json_delta"
        ]
        assert json_deltas[0]["delta"]["partial_json"] == '{"p":1}'

    @pytest.mark.asyncio
    async def test_finish_reason_maps_to_stop_reason(self):
        chunks = ['data: {"choices":[{"delta":{},"finish_reason":"length"}]}']
        parsed = self._parse(await self._collect(chunks))
        message_delta = [p for p in parsed if p["type"] == "message_delta"][0]
        assert message_delta["delta"]["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_cache_usage_surfaces_in_message_delta(self):
        chunks = [
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":1000,"completion_tokens":10,'
            '"cache_read_input_tokens":900,"cache_creation_input_tokens":50}}'
        ]
        parsed = self._parse(await self._collect(chunks))
        usage = [p for p in parsed if p["type"] == "message_delta"][0]["usage"]
        assert usage["cache_read_input_tokens"] == 900
        assert usage["cache_creation_input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_done_sentinel_and_garbage_lines_ignored(self):
        chunks = [
            "data: [DONE]",
            "not an sse line",
            'data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}',
        ]
        parsed = self._parse(await self._collect(chunks))
        assert any(p["type"] == "content_block_delta" for p in parsed)


class TestErrorEnvelope:
    @pytest.mark.asyncio
    async def test_upstream_http_error_rewrapped_in_anthropic_shape(self):
        from src.routes.messages import create_message

        req = AnthropicMessagesRequest(
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        with patch(
            "src.routes.chat.chat_completions",
            new=AsyncMock(side_effect=HTTPException(status_code=429, detail="slow down")),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_message(
                    req=req,
                    background_tasks=None,
                    request=_FakeRequest({"x-api-key": "k"}),
                    api_key="k",
                )
        assert exc.value.status_code == 429
        assert exc.value.detail["type"] == "error"
        assert exc.value.detail["error"]["type"] == "rate_limit_error"

    @pytest.mark.asyncio
    async def test_empty_messages_rejected(self):
        from src.routes.messages import create_message

        req = AnthropicMessagesRequest(model="m", messages=[], max_tokens=10)
        with pytest.raises(HTTPException) as exc:
            await create_message(
                req=req, background_tasks=None, request=_FakeRequest({}), api_key="k"
            )
        assert exc.value.status_code == 400
