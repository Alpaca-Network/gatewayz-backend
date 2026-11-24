"""Tool calls from OpenAI-SDK-backed providers must reach the internal models.

Provider clients built on the OpenAI SDK return tool calls as SDK objects. The
internal response models declare ``tool_calls`` as ``list[dict]``, so passing the
objects through raised a pydantic validation error and every tool-calling
response from those providers (OpenAI, Moonshot, DeepSeek, ...) became a 400 /
stream error, on both /v1/chat/completions and /v1/messages.
"""

from types import SimpleNamespace

from openai.types.chat import ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_chunk import (
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message_tool_call import Function

from src.handlers.chat_handler import _tool_calls_as_dicts
from src.schemas.internal.chat import InternalChatResponse, InternalStreamChunk, InternalUsage


def _usage():
    return InternalUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)


def test_none_and_empty_stay_none():
    assert _tool_calls_as_dicts(None) is None
    assert _tool_calls_as_dicts([]) is None


def test_dicts_pass_through_unchanged():
    calls = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    assert _tool_calls_as_dicts(calls) == calls


def test_sdk_message_tool_call_validates_in_internal_response():
    args = '{"file_path": "a"}'
    sdk_call = ChatCompletionMessageToolCall(
        id="call_1", type="function", function=Function(name="Read", arguments=args)
    )
    calls = _tool_calls_as_dicts([sdk_call])
    assert calls == [
        {"id": "call_1", "type": "function", "function": {"name": "Read", "arguments": args}}
    ]
    response = InternalChatResponse(
        id="r",
        model="openai/gpt-5.4-mini",
        content="",
        finish_reason="tool_calls",
        usage=_usage(),
        tool_calls=calls,
        provider_used="openai",
        cost_usd=0.0,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
    )
    assert response.tool_calls[0]["function"]["name"] == "Read"


def test_sdk_stream_delta_tool_call_validates_in_internal_chunk():
    first = ChoiceDeltaToolCall(
        index=0,
        id="call_1",
        type="function",
        function=ChoiceDeltaToolCallFunction(name="Read", arguments=""),
    )
    first_expected = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "Read", "arguments": ""},
    }
    later = ChoiceDeltaToolCall(index=0, function=ChoiceDeltaToolCallFunction(arguments='{"fi'))
    later_expected = {"index": 0, "function": {"arguments": '{"fi'}}
    for sdk_call, expected in ((first, first_expected), (later, later_expected)):
        calls = _tool_calls_as_dicts([sdk_call])
        assert calls == [expected]
        chunk = InternalStreamChunk(id="r", model="m", created=1, tool_calls=calls)
        assert chunk.tool_calls == [expected]


def test_parallel_calls_keep_order_and_index():
    calls = _tool_calls_as_dicts(
        [
            ChoiceDeltaToolCall(
                index=0, id="a", type="function", function=ChoiceDeltaToolCallFunction(name="x")
            ),
            ChoiceDeltaToolCall(
                index=1, id="b", type="function", function=ChoiceDeltaToolCallFunction(name="y")
            ),
        ]
    )
    assert [c["index"] for c in calls] == [0, 1]
    assert [c["id"] for c in calls] == ["a", "b"]


def test_unknown_object_shape_is_read_by_attribute():
    obj = SimpleNamespace(
        id="call_9",
        type=None,
        index=2,
        function=SimpleNamespace(name="Bash", arguments='{"command": "ls"}'),
    )
    assert _tool_calls_as_dicts([obj]) == [
        {
            "id": "call_9",
            "type": "function",
            "function": {"name": "Bash", "arguments": '{"command": "ls"}'},
            "index": 2,
        }
    ]
