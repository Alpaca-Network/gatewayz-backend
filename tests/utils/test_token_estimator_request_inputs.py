"""A request is charged for every input it sends
(src/utils/token_estimator.py::estimate_request_input_tokens).

Message text, tool definitions, tool calls and results, images, documents and
a system prompt given as blocks all count; binary parts get the named flat
allowances.
"""

from __future__ import annotations

import base64

from src.services.billing.credit_precheck import calculate_maximum_cost
from src.utils.token_estimator import (
    DOCUMENT_BYTES_PER_TOKEN,
    DOCUMENT_PART_MIN_TOKENS,
    IMAGE_PART_TOKENS,
    count_tokens_text,
    estimate_output_tokens,
    estimate_request_input_tokens,
    request_uses_one_hour_cache,
)

USER = [{"role": "user", "content": "hello"}]


def _tool(description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": description,
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }


def test_plain_messages_count_as_before():
    from src.utils.token_estimator import count_tokens_messages

    assert estimate_request_input_tokens(USER) == count_tokens_messages(USER)


def test_tool_definitions_are_counted():
    base = estimate_request_input_tokens(USER)
    with_tools = estimate_request_input_tokens(USER, tools=[_tool("find things")])
    assert with_tools > base


def test_a_large_tool_description_raises_the_estimate_accordingly():
    description = "word " * 120_000  # ~600 KB
    base = estimate_request_input_tokens(USER)
    estimate = estimate_request_input_tokens(USER, tools=[_tool(description)])
    assert estimate - base >= count_tokens_text(description)


def test_tool_choice_naming_a_tool_is_counted():
    base = estimate_request_input_tokens(USER, tools=[_tool("x")])
    named = estimate_request_input_tokens(
        USER, tools=[_tool("x")], tool_choice={"type": "function", "function": {"name": "x"}}
    )
    assert named > base


def test_each_image_part_gets_the_flat_allowance():
    text_only = [{"role": "user", "content": [{"type": "text", "text": "what is this"}]}]
    with_images = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 9000}},
                {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
            ],
        }
    ]
    diff = estimate_request_input_tokens(with_images) - estimate_request_input_tokens(text_only)
    assert diff == 2 * IMAGE_PART_TOKENS


def test_a_document_is_sized_from_its_decoded_payload():
    payload = base64.b64encode(b"%PDF" + b"\x00" * 300_000).decode()
    doc = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": payload},
                }
            ],
        }
    ]
    empty = [{"role": "user", "content": []}]
    diff = estimate_request_input_tokens(doc) - estimate_request_input_tokens(empty)
    expected = ((len(payload) * 3) // 4) // DOCUMENT_BYTES_PER_TOKEN
    assert diff == expected > DOCUMENT_PART_MIN_TOKENS


def test_a_small_or_unsized_document_gets_the_floor():
    small = {"type": "file", "file": {"file_data": "data:application/pdf;base64,QUJD"}}
    by_id = {"type": "file", "file": {"file_id": "file-123"}}
    empty = [{"role": "user", "content": []}]
    for part in (small, by_id):
        msgs = [{"role": "user", "content": [part]}]
        diff = estimate_request_input_tokens(msgs) - estimate_request_input_tokens(empty)
        assert diff == DOCUMENT_PART_MIN_TOKENS


def test_assistant_tool_call_arguments_are_counted():
    args = '{"path": "' + "a/" * 2000 + '"}'
    without = [{"role": "assistant", "content": None}]
    with_call = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": args}}
            ],
        }
    ]
    diff = estimate_request_input_tokens(with_call) - estimate_request_input_tokens(without)
    assert diff >= count_tokens_text(args)


def test_tool_result_and_tool_use_blocks_are_counted():
    result_text = "line of output\n" * 2000
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "run", "input": {"cmd": "ls"}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": result_text}],
        },
    ]
    assert estimate_request_input_tokens(msgs) >= count_tokens_text(result_text)


def test_a_system_prompt_given_as_blocks_is_counted():
    system = [{"type": "text", "text": "You are a careful assistant. " * 200}]
    base = estimate_request_input_tokens(USER)
    assert estimate_request_input_tokens(USER, system=system) - base >= count_tokens_text(
        system[0]["text"]
    )
    assert estimate_request_input_tokens(USER, system="short") > base


def test_the_maximum_cost_counts_tools_and_the_original_body_floor(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "src.services.billing.credit_precheck.calculate_cost",
        lambda model, inp, out: seen.append(inp) or inp * 1e-6,
    )
    _, plain, _ = calculate_maximum_cost("m", USER, 10)
    _, tools, _ = calculate_maximum_cost("m", USER, 10, tools=[_tool("w " * 5000)])
    _, floored, _ = calculate_maximum_cost("m", USER, 10, input_tokens_floor=50_000)
    assert tools > plain
    assert floored == 50_000


def test_output_estimate_counts_tool_arguments_and_reasoning():
    assert estimate_output_tokens("hi", '{"a": "' + "b" * 400 + '"}', "thinking " * 50) > (
        count_tokens_text("hi") + 50
    )


def test_one_hour_cache_markers_are_found_anywhere():
    block = {"type": "text", "text": "x", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    marked = [{"role": "user", "content": [block]}]
    assert request_uses_one_hour_cache(marked)
    assert request_uses_one_hour_cache(USER, [{"name": "t", "cache_control": {"ttl": "1h"}}])
    assert not request_uses_one_hour_cache(
        [{"role": "user", "content": [{"type": "text", "cache_control": {"type": "ephemeral"}}]}]
    )
