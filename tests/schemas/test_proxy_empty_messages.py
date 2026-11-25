"""Empty messages from agent clients are tolerated, not answered with a 422.

A translation layer that strips a thinking-only assistant turn leaves an
assistant message with empty content; a command with no output becomes an
empty tool message. Both arrived from real agent sessions and failed the
whole request.
"""

import pytest
from pydantic import ValidationError

from src.schemas.proxy import EMPTY_TOOL_RESULT_PLACEHOLDER, ProxyRequest, drop_empty_messages


def _request(messages):
    return ProxyRequest(model="openai/gpt-5.5", messages=messages)


def test_empty_assistant_and_user_messages_are_dropped():
    req = _request(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": ""},
            {"role": "user", "content": "read a.txt"},
        ]
    )
    assert [(m.role, m.content) for m in req.messages] == [
        ("system", "be brief"),
        ("user", "hi"),
        ("user", "read a.txt"),
    ]


def test_whitespace_null_and_empty_list_count_as_empty():
    req = _request(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "   "},
            {"role": "assistant", "content": None},
            {"role": "user", "content": []},
        ]
    )
    assert len(req.messages) == 1


def test_assistant_with_tool_calls_and_no_content_is_kept():
    call = {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}
    req = _request(
        [
            {"role": "user", "content": "read"},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c1", "content": "alpha"},
        ]
    )
    assert [m.role for m in req.messages] == ["user", "assistant", "tool"]


def test_empty_tool_result_is_kept_with_a_placeholder():
    call = {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}
    req = _request(
        [
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c1", "content": ""},
        ]
    )
    tool = req.messages[2]
    assert tool.role == "tool"
    assert tool.tool_call_id == "c1"
    assert tool.content == EMPTY_TOOL_RESULT_PLACEHOLDER


def test_a_request_with_only_empty_messages_is_still_rejected():
    with pytest.raises(ValidationError):
        _request([{"role": "user", "content": ""}])
    with pytest.raises(ValidationError):
        _request([])


def test_messages_with_content_are_untouched():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": "hello"},
    ]
    assert drop_empty_messages(messages) == messages
    assert drop_empty_messages("not a list") == "not a list"
