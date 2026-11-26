"""Every content block on the Anthropic stream gets its own stop event.

Anthropic clients finish a block only when its content_block_stop arrives.
Earlier tool blocks were left open when the next one started, so a client that
was sent three parallel tool calls kept one.
"""

import asyncio
import json

from src.services.anthropic_messages.stream import translate_stream


def _chunk(delta=None, finish=None, usage=None):
    body = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


def _tool(index, call_id=None, name=None, arguments=""):
    call = {"index": index, "function": {"arguments": arguments}}
    if call_id:
        call["id"] = call_id
        call["type"] = "function"
        call["function"]["name"] = name
    return {"tool_calls": [call]}


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


def _run(chunks):
    return asyncio.run(_collect(chunks))


def _assert_well_formed(events):
    """Blocks open in index order and each closes before the next opens."""
    open_index = None
    next_index = 0
    for event in events:
        kind = event["type"]
        if kind == "content_block_start":
            assert open_index is None, "a block opened while another was still open"
            assert event["index"] == next_index
            open_index = event["index"]
            next_index += 1
        elif kind == "content_block_delta":
            assert event["index"] == open_index
        elif kind == "content_block_stop":
            assert event["index"] == open_index
            open_index = None
    assert open_index is None, "a block was never closed"
    return next_index


def test_three_parallel_tool_calls_each_open_and_close():
    events = _run(
        [
            _chunk({"role": "assistant", "content": ""}),
            _chunk({"content": "Reading all three."}),
            _chunk(_tool(0, "call_a", "Read")),
            _chunk(_tool(0, arguments='{"file_path": "a.txt"}')),
            _chunk(_tool(1, "call_b", "Read")),
            _chunk(_tool(1, arguments='{"file_path": "b.txt"}')),
            _chunk(_tool(2, "call_c", "Read")),
            _chunk(_tool(2, arguments='{"file_path": "c.txt"}')),
            _chunk(finish="tool_calls", usage={"prompt_tokens": 10, "completion_tokens": 5}),
            "data: [DONE]\n\n",
        ]
    )
    assert _assert_well_formed(events) == 4
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    assert [b["type"] for b in starts] == ["text", "tool_use", "tool_use", "tool_use"]
    assert [b["id"] for b in starts[1:]] == ["call_a", "call_b", "call_c"]
    assert sum(e["type"] == "content_block_stop" for e in events) == 4
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
    assert events[-1]["type"] == "message_stop"


def test_text_after_a_tool_call_opens_a_new_block():
    events = _run(
        [
            _chunk(_tool(0, "call_a", "Read", "{}")),
            _chunk({"content": "done"}),
            _chunk(finish="stop"),
        ]
    )
    assert _assert_well_formed(events) == 2


def test_tool_calls_force_tool_use_stop_reason():
    events = _run([_chunk(_tool(0, "call_a", "Read", "{}")), _chunk(finish="stop")])
    assert events[-2]["delta"]["stop_reason"] == "tool_use"


def test_missing_tool_call_id_gets_a_generated_one():
    call = {"index": 0, "id": None, "type": "function", "function": {"name": "Read"}}
    events = _run([_chunk({"tool_calls": [call]}), _chunk(finish="tool_calls")])
    start = next(e for e in events if e["type"] == "content_block_start")
    assert start["content_block"]["id"].startswith("toolu_")


def test_final_usage_reports_input_tokens_without_cache_tokens():
    usage = {
        "prompt_tokens": 20289,
        "completion_tokens": 315,
        "cache_read_input_tokens": 17270,
        "cache_creation_input_tokens": 3016,
    }
    events = _run([_chunk({"content": "hi"}), _chunk(finish="stop", usage=usage)])
    final = events[-2]["usage"]
    assert final == {
        "input_tokens": 3,
        "output_tokens": 315,
        "cache_read_input_tokens": 17270,
        "cache_creation_input_tokens": 3016,
    }


def test_reasoning_becomes_a_thinking_block_before_the_answer():
    events = _run(
        [
            _chunk({"role": "assistant", "content": ""}),
            _chunk({"reasoning_content": "17 times 23 "}),
            _chunk({"reasoning_content": "is 391."}),
            _chunk({"content": "391"}),
            _chunk(finish="stop", usage={"prompt_tokens": 20, "completion_tokens": 155}),
        ]
    )
    assert _assert_well_formed(events) == 2
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    assert [b["type"] for b in starts] == ["thinking", "text"]
    thinking = "".join(
        e["delta"]["thinking"]
        for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "thinking_delta"
    )
    assert thinking == "17 times 23 is 391."


def test_reasoning_then_tool_call_closes_the_thinking_block():
    events = _run(
        [
            _chunk({"reasoning_content": "I should read the file."}),
            _chunk(_tool(0, "call_a", "Read", '{"file_path": "a.txt"}')),
            _chunk(finish="tool_calls"),
        ]
    )
    assert _assert_well_formed(events) == 2
    starts = [e["content_block"]["type"] for e in events if e["type"] == "content_block_start"]
    assert starts == ["thinking", "tool_use"]
