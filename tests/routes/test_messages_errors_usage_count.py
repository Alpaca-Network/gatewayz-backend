"""/v1/messages: terminal plan limits, clean error text, stream usage, count_tokens."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.security.identity import RequestIdentity
from src.services.anthropic_messages.errors import safe_error_message
from src.services.anthropic_messages.stream import translate_stream
from src.services.anthropic_messages.tokens import (
    AnthropicCountTokensRequest,
    estimate_input_tokens,
)

_HEADERS = {"x-api-key": "placeholder-key", "anthropic-version": "2023-06-01"}
_BODY = {
    "model": "openai/gpt-5.4-mini",
    "max_tokens": 5,
    "messages": [{"role": "user", "content": "hi"}],
}
_OK = {
    "id": "chatcmpl-x",
    "object": "chat.completion",
    "model": "openai/gpt-5.4-mini",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


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


def _post(fake_chat_completions, body=None, path="/v1/messages"):
    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch(
            "src.routes.messages.get_request_identity",
            new=AsyncMock(side_effect=lambda request, api_key=None: _identity(api_key)),
        ),
    ):
        return TestClient(app).post(path, json=body or _BODY, headers=_HEADERS)


# --- plan limits -------------------------------------------------------------


def test_daily_plan_limit_is_terminal_not_a_retryable_429():
    async def refuse(**kwargs):
        raise HTTPException(
            status_code=429,
            detail="Plan limit exceeded: Daily token limit exceeded (250000) for test environment",
        )

    resp = _post(refuse)
    assert resp.status_code == 402
    error = resp.json()["detail"]["error"] if "detail" in resp.json() else resp.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["message"].startswith("Gatewayz plan limit reached: Daily token limit exceeded")
    assert "Retrying will not help" in error["message"]


def test_per_minute_limit_stays_429():
    async def refuse(**kwargs):
        raise HTTPException(status_code=429, detail="Minute request limit exceeded")

    resp = _post(refuse)
    assert resp.status_code == 429


# --- error text --------------------------------------------------------------


def test_upstream_url_is_removed_from_error_text():
    raw = (
        "Client error '400 Bad Request' for url 'https://api.anthropic.com/v1/messages'\n"
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400"
    )
    cleaned = safe_error_message(raw)
    assert "http" not in cleaned
    assert "anthropic.com" not in cleaned
    assert "400 Bad Request" in cleaned


def test_internal_validation_dump_is_replaced():
    raw = (
        "1 validation error for InternalStreamChunk\ntool_calls.0\n"
        "  Input should be a valid dictionary [type=dict_type, input_value=ChoiceDeltaToolCall]"
    )
    cleaned = safe_error_message(raw)
    assert "Internal" not in cleaned
    assert "pydantic" not in cleaned
    assert cleaned == "The gateway could not process the provider's response."


def test_our_own_links_and_plain_messages_are_kept():
    assert safe_error_message("Model 'x/y' not found") == "Model 'x/y' not found"
    kept = safe_error_message("Out of credits. Top up at https://gatewayz.ai/settings/credits")
    assert "https://gatewayz.ai/settings/credits" in kept
    assert safe_error_message(None) == "The model provider returned an error."


# --- stream ------------------------------------------------------------------


async def _collect(chunks):
    async def source():
        for chunk in chunks:
            yield chunk

    events = []
    async for raw in translate_stream(source(), "m", "msg_1"):
        for line in raw.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def sse(body):
    return f"data: {json.dumps(body)}\n\n"


def test_plan_limit_after_a_delivered_answer_does_not_fail_the_stream():
    events = asyncio.run(
        _collect(
            [
                sse({"choices": [{"index": 0, "delta": {"content": "done"}}]}),
                sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
                sse({"error": {"type": "plan_limit_exceeded", "message": "Plan limit exceeded"}}),
                "data: [DONE]\n\n",
            ]
        )
    )
    kinds = [e["type"] for e in events]
    assert "error" not in kinds
    assert kinds[-1] == "message_stop"


def test_stream_error_text_is_cleaned():
    events = asyncio.run(
        _collect(
            [
                sse(
                    {
                        "error": {
                            "type": "internal_error",
                            "message": "Client error for url 'https://api.example.com/v1/x'",
                        }
                    }
                )
            ]
        )
    )
    error = next(e for e in events if e["type"] == "error")
    assert "http" not in error["error"]["message"]


def test_usage_only_chunk_reaches_the_final_event():
    events = asyncio.run(
        _collect(
            [
                sse({"choices": [{"index": 0, "delta": {"content": "hi"}}]}),
                sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
                sse({"choices": [], "usage": {"prompt_tokens": 10473, "completion_tokens": 150}}),
                "data: [DONE]\n\n",
            ]
        )
    )
    assert events[-2]["usage"] == {"input_tokens": 10473, "output_tokens": 150}


def test_streaming_requests_ask_the_pipeline_for_usage():
    captured = {}

    async def fake(**kwargs):
        captured.update(kwargs)
        raise HTTPException(status_code=400, detail="stop here")

    _post(fake, body={**_BODY, "stream": True})
    options = captured["req"].stream_options
    include = options["include_usage"] if isinstance(options, dict) else options.include_usage
    assert include is True

    captured.clear()
    _post(fake)
    assert captured["req"].stream_options is None


# --- count_tokens ------------------------------------------------------------


def test_count_tokens_returns_an_estimate():
    body = {
        "model": "openai/gpt-5.4-mini",
        "system": [{"type": "text", "text": "You are a careful assistant."}],
        "tools": [
            {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}}
        ],
        "messages": [
            {"role": "user", "content": "Read a.txt"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "a"}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "alpha " * 200}
                ],
            },
        ],
    }
    resp = _post(None, body=body, path="/v1/messages/count_tokens")
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"input_tokens"}
    assert 150 < resp.json()["input_tokens"] < 1500


def test_count_tokens_requires_a_key():
    resp = TestClient(app).post(
        "/v1/messages/count_tokens",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_images_get_a_flat_allowance():
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * 400000},
    }
    question = {"type": "text", "text": "what is this"}
    body = AnthropicCountTokensRequest(
        model="m", messages=[{"role": "user", "content": [image, question]}]
    )
    assert estimate_input_tokens(body) < 2000


def test_count_tokens_rejects_an_invalid_x_api_key():
    with patch(
        "src.routes.messages.validate_api_key_security",
        side_effect=ValueError("Invalid API key"),
    ):
        resp = TestClient(app).post(
            "/v1/messages/count_tokens",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers=_HEADERS,
        )
    assert resp.status_code == 401
