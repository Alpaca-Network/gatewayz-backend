"""/v1/messages pass-through for Claude models: what the caller receives."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.main import app
from src.security.identity import RequestIdentity
from src.services.anthropic_messages.stream import relay_passthrough_stream
from src.services.providers import anthropic_passthrough as pt

_HEADERS = {
    "x-api-key": "placeholder-key",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "interleaved-thinking-2025-05-14",
}
_BODY = {
    "model": "anthropic/claude-haiku-4-5",
    "max_tokens": 2000,
    "thinking": {"type": "enabled", "budget_tokens": 1500},
    "messages": [{"role": "user", "content": "hi"}],
}

RAW = [
    (
        "message_start",
        '{"type":"message_start","message":{"id":"msg_real","model":"claude-haiku-4-5-20251001",'
        '"usage":{"input_tokens":5}}}',
    ),
    (
        "content_block_start",
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}',
    ),
    (
        "content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":{"type":"signature_delta",'
        '"signature":"SIG=="}}',
    ),
    ("content_block_stop", '{"type":"content_block_stop","index":0}'),
    ("some_future_event", '{"type":"some_future_event","x":1}'),
    (
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":9}}',
    ),
    ("message_stop", '{"type":"message_stop"}'),
]


def _openai(delta=None, finish=None):
    body = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n"


async def _collect(make_inner, *, used):
    passthrough = pt.Passthrough(
        body={},
        requested_model="anthropic/claude-haiku-4-5",
        loop=asyncio.get_running_loop(),
        queue=asyncio.Queue(),
    )
    frames = []
    async for frame in relay_passthrough_stream(
        make_inner(passthrough, used), "anthropic/claude-haiku-4-5", "msg_local", passthrough
    ):
        frames.append(frame)
    return frames


def _native_served(passthrough, used):
    """A pipeline stream whose provider was the native Anthropic client."""

    async def inner():
        passthrough.used = used
        yield _openai({"role": "assistant", "content": ""})
        for name, data in RAW:
            passthrough.push_event(name, data)
            await asyncio.sleep(0)
            yield _openai({"content": "x"})
        yield _openai(finish="stop")
        yield "data: [DONE]\n\n"

    return inner()


def test_events_are_relayed_as_received():
    frames = asyncio.run(_collect(_native_served, used=True))
    names = [f.split("\n", 1)[0][len("event: ") :] for f in frames]
    assert names == [name for name, _ in RAW]
    # every event after message_start is byte-for-byte what Anthropic sent
    for frame, (name, data) in list(zip(frames, RAW, strict=True))[1:]:
        assert frame == f"event: {name}\ndata: {data}\n\n"
    assert '"signature":"SIG=="' in frames[2]
    assert frames[4] == 'event: some_future_event\ndata: {"type":"some_future_event","x":1}\n\n'


def test_message_start_reports_the_model_the_caller_asked_for():
    frames = asyncio.run(_collect(_native_served, used=True))
    start = json.loads(frames[0].split("data: ", 1)[1])
    assert start["message"]["model"] == "anthropic/claude-haiku-4-5"
    assert start["message"]["id"] == "msg_real"
    assert start["message"]["usage"] == {"input_tokens": 5}


def test_another_provider_serving_the_model_is_translated_as_before():
    def other_provider(passthrough, used):
        async def inner():
            yield _openai({"role": "assistant", "content": ""})
            yield _openai({"content": "hello"})
            yield _openai(finish="stop")
            yield "data: [DONE]\n\n"

        return inner()

    frames = asyncio.run(_collect(other_provider, used=False))
    kinds = [json.loads(f.split("data: ", 1)[1])["type"] for f in frames]
    assert kinds == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert json.loads(frames[0].split("data: ", 1)[1])["message"]["id"] == "msg_local"


def test_a_stream_that_ends_without_message_stop_ends_with_an_error():
    def cut_short(passthrough, used):
        async def inner():
            passthrough.used = True
            passthrough.push_event(*RAW[0])
            await asyncio.sleep(0)
            yield _openai({"content": "x"})

        return inner()

    frames = asyncio.run(_collect(cut_short, used=True))
    assert frames[0].startswith("event: message_start")
    assert frames[-1].startswith("event: error")


def test_a_pipeline_error_becomes_one_clean_error_event():
    def failing(passthrough, used):
        async def inner():
            passthrough.used = True
            passthrough.push_event(*RAW[0])
            await asyncio.sleep(0)
            error = {"error": {"type": "internal_error", "message": "boom at https://x.internal/y"}}
            yield f"data: {json.dumps(error)}\n\n"
            yield "data: [DONE]\n\n"

        return inner()

    frames = asyncio.run(_collect(failing, used=True))
    errors = [f for f in frames if f.startswith("event: error")]
    assert len(errors) == 1
    assert "internal" not in errors[0].split("data: ", 1)[1].replace("internal_error", "")


def test_the_pipeline_stream_is_always_consumed_to_the_end():
    seen = {"finished": False}

    def tracked(passthrough, used):
        async def inner():
            passthrough.used = True
            for name, data in RAW:
                passthrough.push_event(name, data)
            await asyncio.sleep(0)
            yield _openai({"content": "x"})
            yield "data: [DONE]\n\n"
            seen["finished"] = True  # where the pipeline records usage and bills

        return inner()

    asyncio.run(_collect(tracked, used=True))
    assert seen["finished"] is True


# --- route level -----------------------------------------------------------------


def _identity(api_key):
    return RequestIdentity(
        kind="api_key",
        user_id=7,
        api_key=api_key,
        auth_method="privy",
        is_guest=False,
        wallet_addresses=(),
        user={"id": 7, "auth_method": "privy"},
    )


def _post(fake_chat_completions, body):
    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch(
            "src.routes.messages.get_request_identity",
            new=AsyncMock(side_effect=lambda request, api_key=None: _identity(api_key)),
        ),
    ):
        return TestClient(app).post("/v1/messages", json=body, headers=_HEADERS)


_OPENAI_OK = {
    "id": "chatcmpl-x",
    "object": "chat.completion",
    "model": "anthropic/claude-haiku-4-5",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
}
_NATIVE_OK = {
    "id": "msg_real",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [
        {"type": "thinking", "thinking": "plan", "signature": "SIG=="},
        {"type": "text", "text": "ok"},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 5, "output_tokens": 9},
}


def test_flag_off_binds_nothing(monkeypatch):
    monkeypatch.delenv("MESSAGES_PASSTHROUGH_ENABLED", raising=False)
    seen = {}

    async def fake(**kwargs):
        seen["passthrough"] = pt.current()
        return _OPENAI_OK

    resp = _post(fake, _BODY)
    assert resp.status_code == 200
    assert seen["passthrough"] is None
    assert resp.json()["content"] == [{"type": "text", "text": "ok"}]


def test_flag_on_gives_the_provider_the_callers_body_and_returns_the_native_response(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    seen = {}

    async def fake(**kwargs):
        passthrough = pt.current()
        seen["body"] = passthrough.body
        seen["beta"] = passthrough.beta
        passthrough.response_json = _NATIVE_OK  # what the native client does
        passthrough.used = True
        return _OPENAI_OK

    resp = _post(fake, _BODY)
    assert resp.status_code == 200
    assert seen["body"] == _BODY
    assert seen["beta"] == "interleaved-thinking-2025-05-14"
    out = resp.json()
    assert out["id"] == "msg_real"
    assert out["model"] == "anthropic/claude-haiku-4-5"
    assert out["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG=="}
    assert pt.current() is None


def test_flag_on_but_served_elsewhere_falls_back_to_translation(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")

    async def fake(**kwargs):
        return _OPENAI_OK  # the native client never ran

    resp = _post(fake, _BODY)
    assert resp.status_code == 200
    assert resp.json()["content"] == [{"type": "text", "text": "ok"}]


def test_other_models_are_never_passed_through(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    seen = {}

    async def fake(**kwargs):
        seen["passthrough"] = pt.current()
        return _OPENAI_OK

    _post(fake, {**_BODY, "model": "openai/gpt-5.4-mini"})
    assert seen["passthrough"] is None


def test_billed_server_tools_are_removed_and_reported(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    seen = {}

    async def fake(**kwargs):
        seen["tools"] = pt.current().body.get("tools")
        return _OPENAI_OK

    body = {
        **_BODY,
        "tools": [
            {"name": "Read", "description": "r", "input_schema": {"type": "object"}},
            {"type": "web_search_20250305", "name": "web_search"},
        ],
    }
    resp = _post(fake, body)
    assert resp.status_code == 200
    assert [t["name"] for t in seen["tools"]] == ["Read"]
    assert resp.headers["x-gatewayz-dropped"] == "server_tools"


def test_streaming_route_relays_native_events(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")

    async def fake(**kwargs):
        passthrough = pt.current()

        async def body():
            passthrough.used = True
            yield _openai({"role": "assistant", "content": ""})
            for name, data in RAW:
                passthrough.push_event(name, data)
                await asyncio.sleep(0)
                yield _openai({"content": "x"})
            yield "data: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    resp = _post(fake, {**_BODY, "stream": True})
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    events = [line[len("event: ") :] for line in lines if line.startswith("event: ")]
    assert events == [name for name, _ in RAW]
    assert '"signature":"SIG=="' in resp.text


def test_header_mode_passes_through_only_requests_that_opt_in(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "header")
    seen = {}

    async def fake(**kwargs):
        seen["passthrough"] = pt.current()
        return _OPENAI_OK

    _post(fake, _BODY)
    assert seen["passthrough"] is None

    with (
        patch("src.routes.chat.chat_completions", new=fake),
        patch(
            "src.routes.messages.get_request_identity",
            new=AsyncMock(side_effect=lambda request, api_key=None: _identity(api_key)),
        ),
    ):
        TestClient(app).post(
            "/v1/messages", json=_BODY, headers={**_HEADERS, "x-gatewayz-passthrough": "true"}
        )
    assert seen["passthrough"] is not None


# --- Claude Code auto mode: safeguards ----------------------------------------------
#
# In auto mode Claude Code sends `anthropic-beta: dangerous-tool-use-*` and a
# top-level `safeguards` field, and reads `safeguard_results` from the reply.
# When any of the three is lost it falls back to running the safety check as
# extra billed model calls on the caller's key.

_SAFEGUARDS = {"tool_use": {"enabled": True}}
_AUTO_MODE_BETA = "interleaved-thinking-2025-05-14,dangerous-tool-use-2026-09-03"


def _post_auto_mode(fake_chat_completions, body):
    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch(
            "src.routes.messages.get_request_identity",
            new=AsyncMock(side_effect=lambda request, api_key=None: _identity(api_key)),
        ),
    ):
        return TestClient(app).post(
            "/v1/messages",
            json=body,
            headers={**_HEADERS, "anthropic-beta": _AUTO_MODE_BETA},
        )


def test_passthrough_forwards_safeguards_and_beta_and_returns_results(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    seen = {}

    async def fake(**kwargs):
        passthrough = pt.current()
        seen["upstream_body"] = passthrough.upstream_body(
            "claude-haiku-4-5-20251001", stream=False, max_tokens=None
        )
        seen["upstream_headers"] = passthrough.upstream_headers({"x-api-key": "ours"})
        passthrough.response_json = {**_NATIVE_OK, "safeguard_results": [{"verdict": "allow"}]}
        passthrough.used = True
        return _OPENAI_OK

    resp = _post_auto_mode(fake, {**_BODY, "safeguards": _SAFEGUARDS})
    assert resp.status_code == 200
    assert seen["upstream_body"]["safeguards"] == _SAFEGUARDS
    assert seen["upstream_headers"]["anthropic-beta"] == _AUTO_MODE_BETA
    assert resp.json()["safeguard_results"] == [{"verdict": "allow"}]


def test_passthrough_stream_relays_safeguard_results(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    results = (
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"tool_use"},'
        '"usage":{"output_tokens":9},"safeguard_results":[{"verdict":"allow"}]}',
    )

    async def fake(**kwargs):
        passthrough = pt.current()

        async def body():
            passthrough.used = True
            for name, data in [RAW[0], results, RAW[-1]]:
                passthrough.push_event(name, data)
                await asyncio.sleep(0)
                yield _openai({"content": "x"})
            yield "data: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    resp = _post_auto_mode(fake, {**_BODY, "stream": True, "safeguards": _SAFEGUARDS})
    assert resp.status_code == 200
    assert f"event: {results[0]}\ndata: {results[1]}\n\n" in resp.text


def test_safeguards_without_passthrough_get_the_documented_400(monkeypatch):
    """Not silently dropped: a 400 naming `safeguards`, before anything is billed."""
    monkeypatch.delenv("MESSAGES_PASSTHROUGH_ENABLED", raising=False)
    called = {"pipeline": False}

    async def fake(**kwargs):
        called["pipeline"] = True
        return _OPENAI_OK

    resp = _post_auto_mode(fake, {**_BODY, "safeguards": _SAFEGUARDS})
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "safeguards" in error["message"]
    assert called["pipeline"] is False


def test_safeguards_on_a_non_claude_model_get_the_documented_400(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")

    async def fake(**kwargs):
        raise AssertionError("pipeline must not run")

    resp = _post_auto_mode(
        fake, {**_BODY, "model": "openai/gpt-5.4-mini", "safeguards": _SAFEGUARDS}
    )
    assert resp.status_code == 400
    assert "safeguards" in resp.text


def test_requests_without_safeguards_are_unaffected(monkeypatch):
    monkeypatch.delenv("MESSAGES_PASSTHROUGH_ENABLED", raising=False)

    async def fake(**kwargs):
        return _OPENAI_OK

    resp = _post_auto_mode(fake, _BODY)
    assert resp.status_code == 200
