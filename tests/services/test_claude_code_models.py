"""The model list served to Claude Code's /model picker, and the ids it sends back."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.identity import RequestIdentity
from src.services.claude_code_models import (
    claude_code_model_listing,
    is_agent_capable,
    picker_id,
    strip_claude_code_prefix,
)

CATALOG = [
    {
        "id": "openai/gpt-5.5",
        "name": "GPT-5.5",
        "context_length": 128000,
        "capabilities": {"tools": True},
    },
    {
        "id": "anthropic/claude-sonnet-5-5",
        "name": "Claude Sonnet 5.5",
        "context_length": 200000,
        "supported_parameters": ["max_tokens", "tools"],
    },
    {
        "id": "deepseek-ai/DeepSeek-V4.1-Flash",
        "name": "DeepSeek V4.1 Flash",
        "context_length": 0,
        "capabilities": {"tools": True},
    },
    {"id": "tiny/model", "name": "Tiny", "context_length": 8192, "capabilities": {"tools": True}},
    {"id": "no/tools", "name": "No Tools", "context_length": 200000, "capabilities": {}},
    {
        "id": "off/model",
        "name": "Off",
        "context_length": 200000,
        "is_active": False,
        "capabilities": {"tools": True},
    },
]


def test_prefix_round_trip():
    assert picker_id("openai/gpt-5.5") == "claude-code/openai/gpt-5.5"
    assert strip_claude_code_prefix("claude-code/openai/gpt-5.5") == "openai/gpt-5.5"


def test_claude_ids_and_plain_ids_are_left_alone():
    assert picker_id("anthropic/claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    assert strip_claude_code_prefix("anthropic/claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    assert strip_claude_code_prefix("openai/gpt-5.5") == "openai/gpt-5.5"
    assert strip_claude_code_prefix(None) is None


def test_only_agent_capable_models_are_listed():
    assert [is_agent_capable(m) for m in CATALOG] == [True, True, True, False, False, False]


def test_listing_shape_and_order():
    listing = claude_code_model_listing(CATALOG)
    assert [m["id"] for m in listing["data"]] == [
        "anthropic/claude-sonnet-5-5",
        "claude-code/deepseek-ai/DeepSeek-V4.1-Flash",
        "claude-code/openai/gpt-5.5",
    ]
    assert all("claude" in m["id"] for m in listing["data"])
    gpt = listing["data"][2]
    assert gpt["type"] == "model"
    assert gpt["display_name"] == "GPT-5.5"
    assert gpt["max_input_tokens"] == 128000
    assert "max_input_tokens" not in listing["data"][1]
    assert listing["has_more"] is False
    assert listing["first_id"] == "anthropic/claude-sonnet-5-5"
    assert listing["last_id"] == "claude-code/openai/gpt-5.5"


def test_empty_catalog():
    assert claude_code_model_listing([]) == {
        "data": [],
        "has_more": False,
        "first_id": None,
        "last_id": None,
    }


def test_models_route_answers_anthropic_clients_with_the_picker_listing():
    with patch(
        "src.routes.catalog.get_models", new=AsyncMock(return_value={"data": CATALOG})
    ) as get_models:
        resp = TestClient(app).get(
            "/v1/models?limit=1000", headers={"anthropic-version": "2023-06-01"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "has_more", "first_id", "last_id"}
    assert "claude-code/openai/gpt-5.5" in [m["id"] for m in body["data"]]
    assert get_models.await_args.kwargs["limit"] == 1000


def test_models_route_is_unchanged_for_everyone_else():
    plain = {"object": "list", "data": CATALOG}
    with patch("src.routes.catalog.get_models", new=AsyncMock(return_value=plain)):
        resp = TestClient(app).get("/v1/models?limit=5")
    assert resp.status_code == 200
    assert resp.json() == plain


def test_messages_route_accepts_a_picker_id():
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return {
            "id": "x",
            "model": "openai/gpt-5.5",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    identity = RequestIdentity(
        kind="api_key",
        user_id=7,
        api_key="placeholder-key",
        auth_method="privy",
        is_guest=False,
        wallet_addresses=(),
        user={"id": 7, "auth_method": "privy"},
    )
    with (
        patch("src.routes.chat.chat_completions", new=fake_chat_completions),
        patch("src.routes.messages.get_request_identity", new=AsyncMock(return_value=identity)),
    ):
        resp = TestClient(app).post(
            "/v1/messages",
            json={
                "model": "claude-code/openai/gpt-5.5",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"x-api-key": "placeholder-key", "anthropic-version": "2023-06-01"},
        )
    assert resp.status_code == 200, resp.text
    assert captured["req"].model == "openai/gpt-5.5"
