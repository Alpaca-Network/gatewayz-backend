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
            if p["type"] == "content_block_delta" and p["delta"]["type"] == "input_json_delta"
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


class TestStreamErrorPropagation:
    """A provider error inside the inner stream must surface as an Anthropic
    ``error`` SSE event and terminate the stream — never be swallowed into a
    well-formed empty ``message_stop`` (issue #2236: streaming fabricated
    success for every failing provider)."""

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
    async def test_error_chunk_becomes_error_event(self):
        chunks = [
            'data: {"error":{"message":"The model provider is temporarily unavailable.",'
            '"type":"provider_error","status":502}}'
        ]
        events = await self._collect(chunks)
        assert any(e.startswith("event: error") for e in events)
        parsed = self._parse(events)
        error_events = [p for p in parsed if p["type"] == "error"]
        assert error_events, "expected an Anthropic error event"
        assert error_events[0]["error"]["type"] == "api_error"
        assert "temporarily unavailable" in error_events[0]["error"]["message"]

    @pytest.mark.asyncio
    async def test_error_terminates_stream_without_fabricated_success(self):
        chunks = [
            'data: {"error":{"message":"boom","type":"provider_error","status":502}}',
            # Anything after the error must not be processed.
            'data: {"choices":[{"delta":{"content":"ghost"},"finish_reason":"stop"}]}',
        ]
        parsed = self._parse(await self._collect(chunks))
        types = [p["type"] for p in parsed]
        assert "message_stop" not in types, "error stream must not end with message_stop"
        assert "message_delta" not in types, "error stream must not report end_turn/usage"
        assert not any(p["type"] == "content_block_delta" for p in parsed)

    @pytest.mark.asyncio
    async def test_rate_limit_maps_to_anthropic_rate_limit_error(self):
        chunks = [
            'data: {"error":{"message":"Rate limit exceeded. Please wait.",'
            '"type":"rate_limit_error","status":429}}'
        ]
        parsed = self._parse(await self._collect(chunks))
        error_events = [p for p in parsed if p["type"] == "error"]
        assert error_events[0]["error"]["type"] == "rate_limit_error"

    @pytest.mark.asyncio
    async def test_capacity_error_maps_to_overloaded(self):
        chunks = ['data: {"error":{"message":"capacity","type":"capacity_error","status":503}}']
        parsed = self._parse(await self._collect(chunks))
        error_events = [p for p in parsed if p["type"] == "error"]
        assert error_events[0]["error"]["type"] == "overloaded_error"

    @pytest.mark.asyncio
    async def test_error_after_content_still_surfaces(self):
        chunks = [
            'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
            'data: {"error":{"message":"died mid-stream","type":"stream_timeout","status":504}}',
        ]
        parsed = self._parse(await self._collect(chunks))
        types = [p["type"] for p in parsed]
        assert "error" in types
        assert "message_stop" not in types


class TestStreamErrorPropagationNonFatal:
    """Advisory error chunks (emitted after content was fully delivered) must
    NOT terminate the stream — the inverse fabrication of the #2236 bug."""

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
    async def test_normalization_warning_does_not_terminate(self):
        chunks = [
            'data: {"choices":[{"delta":{"content":"full answer"},"finish_reason":null}]}',
            'data: {"error":{"message":"Warning: 5 of 8 chunks could not be normalized",'
            '"type":"stream_normalization_warning","status":500}}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":10,"completion_tokens":5}}',
        ]
        parsed = self._parse(await self._collect(chunks))
        types = [p["type"] for p in parsed]
        assert "error" not in types, "advisory warning must not become a terminal error"
        assert "message_stop" in types
        usage = [p for p in parsed if p["type"] == "message_delta"][0]["usage"]
        assert usage["output_tokens"] == 5

    @pytest.mark.asyncio
    async def test_fatal_error_closes_inner_stream(self):
        closed = {"value": False}

        class _FakeStream:
            def __init__(self):
                self._chunks = iter(
                    ['data: {"error":{"message":"boom","type":"provider_error","status":502}}']
                )

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._chunks)
                except StopIteration:
                    raise StopAsyncIteration

            async def aclose(self):
                closed["value"] = True

        events = []
        async for e in _stream_anthropic_events(_FakeStream(), "m", "msg_1"):
            events.append(e)
        assert any(e.startswith("event: error") for e in events)
        assert closed["value"], "fatal error must aclose() the inner stream"


class TestNonStreaming503Mapping:
    @pytest.mark.asyncio
    async def _error_type_for(self, status, detail):
        from src.routes.messages import create_message

        req = AnthropicMessagesRequest(
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        with patch(
            "src.routes.chat.chat_completions",
            new=AsyncMock(side_effect=HTTPException(status_code=status, detail=detail)),
        ):
            with pytest.raises(HTTPException) as exc:
                await create_message(
                    req=req,
                    background_tasks=None,
                    request=_FakeRequest({"x-api-key": "k"}),
                    api_key="k",
                )
        return exc.value.detail["error"]["type"]

    @pytest.mark.asyncio
    async def test_transient_503_maps_to_overloaded(self):
        error_type = await self._error_type_for(
            503, {"error": {"message": "capacity limit on our side", "code": "capacity"}}
        )
        assert error_type == "overloaded_error"

    @pytest.mark.asyncio
    async def test_pricing_not_configured_503_is_not_retryable(self):
        error_type = await self._error_type_for(
            503,
            {
                "error": {
                    "message": "Pricing for model 'x' is not configured.",
                    "type": "service_unavailable",
                    "code": "pricing_not_configured",
                }
            },
        )
        assert error_type == "api_error"
