"""Pass-through of native Anthropic requests: the caller's body goes upstream
unchanged and Anthropic's events come back unchanged, while the OpenAI-shaped
output the pipeline bills from is still produced."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.providers import anthropic_native_client as native
from src.services.providers import anthropic_passthrough as pt

CALLER_BODY = {
    "model": "anthropic/claude-haiku-4-5",
    "max_tokens": 32000,
    "stream": True,
    "thinking": {"type": "enabled", "budget_tokens": 31999},
    "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    "system": [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}],
    "tools": [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
    ],
    "messages": [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hm", "signature": "SIG=="},
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"p": "a"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [{"type": "text", "text": "alpha"}],
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ],
}


def _passthrough(**overrides):
    body = json.loads(json.dumps(CALLER_BODY))
    body["tools"] = body["tools"][:1]
    return pt.Passthrough(body=body, requested_model=body["model"], **overrides)


@pytest.fixture
def bound():
    """Bind a pass-through for the test and always unbind it."""
    tokens = []

    def _bind(passthrough):
        tokens.append(pt.bind(passthrough))
        return passthrough

    yield _bind
    for token in tokens:
        pt.unbind(token)


# --- the object itself ---------------------------------------------------------


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MESSAGES_PASSTHROUGH_ENABLED", raising=False)
    assert pt.passthrough_enabled() is False
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "true")
    assert pt.passthrough_enabled() is True


def test_header_mode_needs_the_opt_in_header(monkeypatch):
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "header")
    assert pt.passthrough_enabled() is False
    assert pt.passthrough_enabled("false") is False
    assert pt.passthrough_enabled("true") is True
    monkeypatch.setenv("MESSAGES_PASSTHROUGH_ENABLED", "false")
    assert pt.passthrough_enabled("true") is False


def test_only_claude_models_qualify():
    assert pt.is_claude_model("anthropic/claude-sonnet-5-5")
    assert pt.is_claude_model("claude-haiku-4-5")
    assert not pt.is_claude_model("openai/gpt-5.5")
    assert not pt.is_claude_model(None)


def test_upstream_body_is_the_callers_body_with_the_provider_model_id():
    passthrough = _passthrough()
    sent = passthrough.upstream_body("claude-haiku-4-5-20251001", stream=True, max_tokens=32000)
    expected = json.loads(json.dumps(passthrough.body))
    expected["model"] = "claude-haiku-4-5-20251001"
    assert sent == expected
    # thinking signature, cache breakpoints and context management are intact
    assert sent["messages"][1]["content"][0]["signature"] == "SIG=="
    assert sent["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["context_management"] == CALLER_BODY["context_management"]
    # and the caller's copy was not touched
    assert passthrough.body["model"] == "anthropic/claude-haiku-4-5"


def test_non_streaming_body_has_no_stream_flag():
    sent = _passthrough().upstream_body("m", stream=False, max_tokens=None)
    assert "stream" not in sent


def test_pipeline_can_lower_max_tokens_and_the_thinking_budget_follows():
    sent = _passthrough().upstream_body("m", stream=True, max_tokens=4000)
    assert sent["max_tokens"] == 4000
    assert sent["thinking"]["budget_tokens"] == 3999

    sent = _passthrough().upstream_body("m", stream=True, max_tokens=800)
    assert sent["max_tokens"] == 800
    assert "thinking" not in sent


def test_pipeline_never_raises_max_tokens():
    sent = _passthrough().upstream_body("m", stream=True, max_tokens=64000)
    assert sent["max_tokens"] == 32000


def test_only_beta_and_version_headers_are_forwarded():
    passthrough = _passthrough(beta="interleaved-thinking-2025-05-14", version="2023-06-01")
    headers = passthrough.upstream_headers({"x-api-key": "ours", "anthropic-version": "default"})
    assert headers == {
        "x-api-key": "ours",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "interleaved-thinking-2025-05-14",
    }


def test_billed_server_tools_are_removed():
    body = json.loads(json.dumps(CALLER_BODY))
    body["tool_choice"] = {"type": "tool", "name": "web_search"}
    dropped = pt.strip_billed_server_tools(body)
    assert dropped == ["web_search_20250305"]
    assert [t["name"] for t in body["tools"]] == ["Read"]
    assert "tool_choice" not in body


def test_client_side_typed_tools_are_kept():
    body = {"tools": [{"type": "bash_20250124", "name": "bash"}, {"name": "Read"}]}
    assert pt.strip_billed_server_tools(body) == []
    assert len(body["tools"]) == 2


def test_removing_the_only_tool_removes_the_tools_field():
    body = {"tools": [{"type": "web_search_20250305", "name": "web_search"}], "tool_choice": "auto"}
    assert pt.strip_billed_server_tools(body) == ["web_search_20250305"]
    assert "tools" not in body and "tool_choice" not in body


# --- native client: non-streaming ------------------------------------------------

NATIVE_RESPONSE = {
    "id": "msg_real",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [
        {"type": "thinking", "thinking": "plan", "signature": "SIG2=="},
        {"type": "text", "text": "done"},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 5,
        "output_tokens": 40,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
    },
}


def test_non_streaming_request_sends_the_callers_body_and_keeps_the_raw_response(bound):
    passthrough = bound(_passthrough(beta="context-management-2025-06-27"))
    http = MagicMock()
    http.post.return_value.json.return_value = NATIVE_RESPONSE
    with (
        patch.object(native, "get_http_client", return_value=http),
        patch.object(native.Config, "ANTHROPIC_API_KEY", "ours"),
    ):
        result = native.make_anthropic_native_request(
            [{"role": "user", "content": "rebuilt"}], "claude-haiku-4-5-20251001", max_tokens=32000
        )

    sent = http.post.call_args.kwargs
    assert sent["json"]["messages"] == passthrough.body["messages"]
    assert sent["json"]["model"] == "claude-haiku-4-5-20251001"
    assert sent["json"]["thinking"] == passthrough.body["thinking"]
    assert sent["headers"]["anthropic-beta"] == "context-management-2025-06-27"
    assert sent["headers"]["x-api-key"] == "ours"
    assert passthrough.used is True
    assert passthrough.response_json == NATIVE_RESPONSE
    # the pipeline still gets OpenAI-shaped usage to bill from
    assert result["usage"]["prompt_tokens"] == 125
    assert result["usage"]["completion_tokens"] == 40


def test_without_a_passthrough_the_rebuilt_payload_is_sent():
    http = MagicMock()
    http.post.return_value.json.return_value = NATIVE_RESPONSE
    with (
        patch.object(native, "get_http_client", return_value=http),
        patch.object(native.Config, "ANTHROPIC_API_KEY", "ours"),
    ):
        native.make_anthropic_native_request(
            [{"role": "user", "content": "rebuilt"}], "claude-haiku-4-5-20251001", max_tokens=10
        )
    sent = http.post.call_args.kwargs
    assert sent["json"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "rebuilt"}]}
    ]
    assert "anthropic-beta" not in sent["headers"]


def test_a_failed_call_leaves_the_passthrough_unused(bound):
    passthrough = bound(_passthrough())
    http = MagicMock()
    http.post.return_value.raise_for_status.side_effect = RuntimeError("400")
    with (
        patch.object(native, "get_http_client", return_value=http),
        patch.object(native.Config, "ANTHROPIC_API_KEY", "ours"),
        pytest.raises(RuntimeError),
    ):
        native.make_anthropic_native_request([], "m", max_tokens=10)
    assert passthrough.used is False
    assert passthrough.response_json is None


# --- native client: streaming ----------------------------------------------------

RAW_EVENTS = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_real",
                "model": "claude-haiku-4-5-20251001",
                "usage": {"input_tokens": 5, "cache_read_input_tokens": 100},
            },
        },
    ),
    (
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Let me check."},
        },
    ),
    (
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta"}},
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("ping", {"type": "ping"}),
    (
        "content_block_start",
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    ),
    (
        "content_block_delta",
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "hi"}},
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 40},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def _sse_lines():
    lines = []
    for name, data in RAW_EVENTS:
        lines += [f"event: {name}", f"data: {json.dumps(data)}", ""]
    return lines


def _fake_stream_client():
    response = MagicMock()
    response.iter_lines.return_value = iter(_sse_lines())
    stream_ctx = MagicMock()
    stream_ctx.__enter__.return_value = response
    client = MagicMock()
    client.stream.return_value = stream_ctx
    return client


async def _run_stream(passthrough):
    passthrough.loop = asyncio.get_running_loop()
    passthrough.queue = asyncio.Queue()
    client = _fake_stream_client()
    with (
        patch.object(native.httpx, "Client", return_value=client),
        patch.object(native.Config, "ANTHROPIC_API_KEY", "ours"),
    ):
        # The pipeline iterates provider streams on worker threads.
        chunks = await asyncio.to_thread(
            lambda: list(
                native.make_anthropic_native_request_stream(
                    [{"role": "user", "content": "rebuilt"}],
                    "claude-haiku-4-5-20251001",
                    max_tokens=32000,
                )
            )
        )
    await asyncio.sleep(0)
    relayed = []
    while not passthrough.queue.empty():
        relayed.append(passthrough.queue.get_nowait())
    return client, chunks, relayed


def test_stream_relays_every_event_as_received_and_still_feeds_the_pipeline(bound):
    passthrough = bound(_passthrough(beta="interleaved-thinking-2025-05-14"))
    client, chunks, relayed = asyncio.run(_run_stream(passthrough))

    sent = client.stream.call_args.kwargs
    assert sent["json"]["messages"] == passthrough.body["messages"]
    assert sent["json"]["stream"] is True
    assert sent["headers"]["anthropic-beta"] == "interleaved-thinking-2025-05-14"
    assert passthrough.used is True

    # every event, in order, with its data string untouched
    assert [(kind, name) for kind, name, _ in relayed] == [("event", n) for n, _ in RAW_EVENTS]
    assert [data for _, _, data in relayed] == [json.dumps(d) for _, d in RAW_EVENTS]

    # the OpenAI-shaped chunks the pipeline bills from are unchanged (plus one
    # chunk with no choices that hands the input usage to billing early)
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
    assert text == "hi"
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["prompt_tokens"] == 105
    assert final["usage"]["completion_tokens"] == 40


def test_stream_without_a_passthrough_relays_nothing():
    async def run():
        client = _fake_stream_client()
        with (
            patch.object(native.httpx, "Client", return_value=client),
            patch.object(native.Config, "ANTHROPIC_API_KEY", "ours"),
        ):
            chunks = list(
                native.make_anthropic_native_request_stream(
                    [{"role": "user", "content": "rebuilt"}], "m", max_tokens=10
                )
            )
        return client, chunks

    client, chunks = asyncio.run(run())
    assert "anthropic-beta" not in client.stream.call_args.kwargs["headers"]
    assert chunks[-1]["usage"]["completion_tokens"] == 40


def test_stream_hands_thinking_to_billing(bound):
    """A stream cut before message_delta is billed from the text seen so far;
    the thinking must be part of it or it is free (audit 2026-11-26)."""
    passthrough = bound(_passthrough(beta="interleaved-thinking-2025-05-14"))
    _, chunks, _ = asyncio.run(_run_stream(passthrough))
    reasoning = "".join(
        c["choices"][0]["delta"].get("reasoning_content") or "" for c in chunks if c["choices"]
    )
    assert reasoning == "Let me check."
