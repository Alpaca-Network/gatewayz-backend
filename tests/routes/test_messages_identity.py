"""Regression: /v1/messages must pass a resolved RequestIdentity into chat_completions.

gatewayz-backend#2274 added `identity: RequestIdentity = Depends(get_request_identity)`
to chat_completions(); messages.py calls it directly (bypassing FastAPI injection)
and was not updated, so every /v1/messages request hit
`identity.is_anonymous` on a bare `Depends` object and 500'd in production
(2026-09-03). This test drives the real route and asserts chat_completions
receives a real RequestIdentity built from the key messages.py resolved --
including the Anthropic-style `x-api-key` header path.
"""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.identity import ANONYMOUS, RequestIdentity

_BODY = {
    "model": "claude-3-5-haiku-latest",
    "max_tokens": 5,
    "messages": [{"role": "user", "content": "hi"}],
}

_OK = {
    "id": "chatcmpl-x",
    "object": "chat.completion",
    "model": "claude-3-5-haiku-latest",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _fake_identity(api_key: str) -> RequestIdentity:
    return RequestIdentity(
        kind="api_key",
        user_id=7,
        api_key=api_key,
        auth_method="privy",
        is_guest=False,
        wallet_addresses=(),
        user={"id": 7, "auth_method": "privy"},
    )


def test_messages_passes_real_identity_from_x_api_key():
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return _OK

    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch(
            "src.routes.messages.get_request_identity",
            new=AsyncMock(side_effect=lambda request, api_key=None: _fake_identity(api_key)),
        ),
    ):
        client = TestClient(app)
        resp = client.post(
            "/v1/messages",
            json=_BODY,
            headers={"x-api-key": "gw_live_test123", "anthropic-version": "2023-06-01"},
        )

    assert resp.status_code == 200, resp.text
    identity = captured.get("identity")
    assert isinstance(identity, RequestIdentity), f"identity not injected: {identity!r}"
    assert identity.api_key == "gw_live_test123"
    assert captured.get("api_key") == "gw_live_test123"


def test_messages_without_key_gets_anonymous_identity_not_500():
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return _OK

    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch("src.routes.messages.get_request_identity", new=AsyncMock(return_value=ANONYMOUS)),
    ):
        client = TestClient(app)
        resp = client.post("/v1/messages", json=_BODY, headers={"anthropic-version": "2023-06-01"})

    assert resp.status_code != 500, resp.text
    assert captured.get("identity") is ANONYMOUS
