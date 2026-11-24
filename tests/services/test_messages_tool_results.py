"""Anthropic tool results must become real tool messages.

An agent sends the result of each tool call back as a ``tool_result`` block in
a user message. Those were rewritten as plain text, so the assistant turn that
made the calls was followed by no tool results at all. Anthropic and OpenAI
both reject that, and no agent loop got past its first tool call.
"""

from src.services.providers.anthropic_native_client import build_anthropic_payload
from src.services.providers.anthropic_transformer import (
    anthropic_input_tokens,
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)

ASSISTANT_TWO_CALLS = {
    "role": "assistant",
    "content": [
        {"type": "thinking", "thinking": "plan", "signature": "sig"},
        {"type": "text", "text": "Reading both."},
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "a.txt"}},
        {"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"file_path": "b.txt"}},
    ],
}


def _convert(user_blocks, **kwargs):
    messages = [
        {"role": "user", "content": "read the files"},
        ASSISTANT_TWO_CALLS,
        {"role": "user", "content": user_blocks},
    ]
    out, _ = transform_anthropic_to_openai(messages=messages, max_tokens=100, **kwargs)
    return out


def test_tool_results_become_tool_messages_in_call_order():
    out = _convert(
        [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"},
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": [{"type": "text", "text": "beta"}, {"type": "text", "text": "more"}],
            },
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool"]
    assert out[2] == {"role": "tool", "tool_call_id": "toolu_1", "content": "alpha"}
    assert out[3] == {"role": "tool", "tool_call_id": "toolu_2", "content": "beta\nmore"}
    assert [c["id"] for c in out[1]["tool_calls"]] == ["toolu_1", "toolu_2"]


def test_no_tool_result_text_markers_remain():
    out = _convert([{"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"}])
    assert "[Tool Result]" not in str(out)


def test_error_and_empty_results():
    out = _convert(
        [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": ""},
        ]
    )
    assert out[2]["content"] == "Error: boom"
    assert out[3]["content"] == "(no output)"


def test_user_text_in_the_same_turn_follows_the_tool_messages():
    out = _convert(
        [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "beta"},
            {"type": "text", "text": "now summarise"},
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool", "user"]
    assert out[4]["content"] == "now summarise"


def test_image_in_a_tool_result_moves_to_a_user_message():
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}
    out = _convert(
        [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [image]},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "beta"},
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool", "user"]
    assert out[2]["content"] == "(see attached image)"
    parts = out[4]["content"]
    assert parts[0] == {"type": "text", "text": "Image output of tool call toolu_1:"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AA"


def test_prior_thinking_blocks_are_not_replayed():
    out = _convert([{"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"}])
    assert out[1]["content"] == "Reading both."
    assert "thinking" not in str(out[1])


def test_cache_marker_on_a_tool_result_is_dropped_by_default():
    out = _convert(
        [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "alpha",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )
    assert out[2]["content"] == "alpha"


def test_cache_marker_reaches_the_native_tool_result_block():
    out = _convert(
        [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"},
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": "beta",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        keep_tool_result_cache_control=True,
    )
    payload = build_anthropic_payload(out, "claude-haiku-4-5", max_tokens=100)
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant", "user"]
    assistant_types = [b["type"] for b in payload["messages"][1]["content"]]
    assert assistant_types == ["text", "tool_use", "tool_use"]
    results = payload["messages"][2]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in results] == ["toolu_1", "toolu_2"]
    assert "cache_control" not in results[0]
    assert results[1]["cache_control"] == {"type": "ephemeral"}
    assert results[1]["content"] == [{"type": "text", "text": "beta"}]


def test_input_tokens_exclude_cache_tokens():
    usage = {
        "prompt_tokens": 20289,
        "cache_read_input_tokens": 17270,
        "cache_creation_input_tokens": 3016,
    }
    assert anthropic_input_tokens(usage) == 3
    assert anthropic_input_tokens({"prompt_tokens": 50}) == 50
    assert anthropic_input_tokens({"prompt_tokens": 5, "cache_read_input_tokens": 9}) == 0
    assert anthropic_input_tokens(None) == 0


def test_response_puts_text_before_tool_use_and_ends_with_tool_use():
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "Reading.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "Read", "arguments": '{"file_path": "a"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 7, "cache_read_input_tokens": 80},
    }
    out = transform_openai_to_anthropic(response, "m")
    assert [b["type"] for b in out["content"]] == ["text", "tool_use"]
    assert out["content"][1]["input"] == {"file_path": "a"}
    assert out["stop_reason"] == "tool_use"
    assert out["usage"]["input_tokens"] == 20
    assert out["usage"]["cache_read_input_tokens"] == 80
