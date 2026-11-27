"""The pass-through runs on Gatewayz's Anthropic account: callers must not be
able to make that account reach remote servers, run containers, read stored
files or switch to pricier service tiers.

Found 2026-11-26 against production: `mcp_servers` was forwarded and Anthropic
tried to connect to the caller's URL; `container` and Files API references were
forwarded too.
"""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.services.anthropic_messages.tokens import count_tokens_text
from src.services.providers import anthropic_passthrough as pt


def _passthrough(body, beta=None):
    return pt.Passthrough(body=body, requested_model="anthropic/claude-haiku-4-5", beta=beta)


def test_only_known_fields_are_forwarded():
    body = {
        "model": "anthropic/claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "context_management": {"edits": []},
        "mcp_servers": [{"type": "url", "url": "https://attacker.example/mcp", "name": "x"}],
        "container": "container_abc",
        "service_tier": "priority",
        "speed": "fast",
    }
    sent = _passthrough(body).upstream_body("claude-haiku-4-5", stream=False, max_tokens=None)
    assert set(sent) == {"model", "max_tokens", "messages", "thinking", "context_management"}


def test_risky_betas_are_dropped_and_the_rest_kept():
    p = _passthrough(
        {},
        beta=(
            "interleaved-thinking-2025-05-14, files-api-2025-04-14,mcp-client-2025-04-04,"
            "code-execution-2025-05-22,skills-2025-10-02,claude-code-20250219"
        ),
    )
    headers = p.upstream_headers({"x-api-key": "ours"})
    assert headers["anthropic-beta"] == "interleaved-thinking-2025-05-14,claude-code-20250219"
    assert headers["x-api-key"] == "ours"


def test_no_beta_header_when_every_beta_is_blocked():
    headers = _passthrough({}, beta="files-api-2025-04-14").upstream_headers({})
    assert "anthropic-beta" not in headers


def test_mcp_toolsets_are_stripped_like_other_server_tools():
    body = {"tools": [{"name": "Read"}, {"type": "mcp_toolset", "mcp_server_name": "x"}]}
    assert pt.strip_billed_server_tools(body) == ["mcp_toolset"]
    assert body["tools"] == [{"name": "Read"}]


def test_file_references_are_found_anywhere():
    file_block = {"type": "document", "source": {"type": "file", "file_id": "file_x"}}
    assert pt.has_file_reference([{"role": "user", "content": [file_block]}])
    nested = {"type": "tool_result", "tool_use_id": "t", "content": [file_block]}
    assert pt.has_file_reference([{"role": "user", "content": [nested]}])
    inline = {"type": "document", "source": {"type": "text", "data": "x"}}
    assert not pt.has_file_reference([{"role": "user", "content": [inline]}])


def test_route_refuses_file_references_before_the_pipeline():
    called = []

    async def fake(**kwargs):
        called.append(True)

    with patch("src.routes.chat.chat_completions", new=fake):
        resp = TestClient(app).post(
            "/v1/messages",
            json={
                "model": "openai/gpt-5.5",
                "max_tokens": 10,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "document", "source": {"type": "file", "file_id": "file_x"}}
                        ],
                    }
                ],
            },
            headers={"x-api-key": "placeholder-key", "anthropic-version": "2023-06-01"},
        )
    assert resp.status_code == 400
    assert "File references" in json.dumps(resp.json())
    assert called == []


def test_count_tokens_does_not_tokenize_huge_text():
    with patch("src.utils.token_estimator.count_tokens_text") as tokenize:
        assert count_tokens_text("a" * 1_000_000) == 250_000
        tokenize.assert_not_called()
