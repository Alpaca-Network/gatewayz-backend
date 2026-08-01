"""Tests for the native Anthropic Messages transport.

The reason this transport exists is prompt caching, so the load-bearing tests
are the ones asserting that ``cache_control`` survives the OpenAI -> Anthropic
conversion and that cache token counts survive the return trip.
"""

import json

from src.services.providers.anthropic_native_client import (
    anthropic_response_to_openai,
    build_anthropic_payload,
    request_uses_caching,
)


class TestCacheControlPreservation:
    def test_cache_control_survives_on_text_block(self):
        """The whole point of the native transport."""
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are a coding agent.",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": "hi"},
        ]
        payload = build_anthropic_payload(messages, "claude-sonnet-4")
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_survives_on_user_content_block(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "<big file>",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ]
        payload = build_anthropic_payload(messages, "claude-sonnet-4")
        assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_survives_on_tool_definitions(self):
        tools = [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
                "cache_control": {"type": "ephemeral"},
            }
        ]
        payload = build_anthropic_payload([{"role": "user", "content": "x"}], "m", tools=tools)
        assert payload["tools"][0]["cache_control"] == {"type": "ephemeral"}

    def test_request_uses_caching_detects_message_breakpoint(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}],
            }
        ]
        assert request_uses_caching(messages) is True

    def test_request_uses_caching_detects_tool_breakpoint(self):
        tools = [{"type": "function", "cache_control": {"type": "ephemeral"}}]
        assert request_uses_caching([{"role": "user", "content": "x"}], tools=tools) is True

    def test_request_uses_caching_false_for_plain_request(self):
        assert request_uses_caching([{"role": "user", "content": "hello"}]) is False


class TestRequestConversion:
    def test_system_message_hoisted_to_system_field(self):
        messages = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
        payload = build_anthropic_payload(messages, "claude-sonnet-4")
        assert payload["system"][0]["text"] == "be terse"
        assert all(m["role"] != "system" for m in payload["messages"])

    def test_max_tokens_always_present(self):
        """Anthropic rejects requests without max_tokens; OpenAI treats it as optional."""
        payload = build_anthropic_payload([{"role": "user", "content": "hi"}], "m")
        assert payload["max_tokens"] > 0

    def test_explicit_max_tokens_respected(self):
        payload = build_anthropic_payload(
            [{"role": "user", "content": "hi"}], "m", max_tokens=99
        )
        assert payload["max_tokens"] == 99

    def test_stop_becomes_stop_sequences(self):
        payload = build_anthropic_payload([{"role": "user", "content": "x"}], "m", stop="END")
        assert payload["stop_sequences"] == ["END"]

    def test_openai_tools_become_anthropic_tools(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        payload = build_anthropic_payload([{"role": "user", "content": "x"}], "m", tools=tools)
        tool = payload["tools"][0]
        assert tool["name"] == "read_file"
        assert tool["description"] == "Read a file"
        assert tool["input_schema"] == {"type": "object", "properties": {}}

    def test_tool_choice_required_maps_to_any(self):
        payload = build_anthropic_payload(
            [{"role": "user", "content": "x"}],
            "m",
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice="required",
        )
        assert payload["tool_choice"] == {"type": "any"}

    def test_tool_choice_specific_function_maps_to_tool(self):
        payload = build_anthropic_payload(
            [{"role": "user", "content": "x"}],
            "m",
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice={"type": "function", "function": {"name": "f"}},
        )
        assert payload["tool_choice"] == {"type": "tool", "name": "f"}

    def test_parallel_tool_calls_false_disables_parallel_use(self):
        payload = build_anthropic_payload(
            [{"role": "user", "content": "x"}],
            "m",
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice="auto",
            parallel_tool_calls=False,
        )
        assert payload["tool_choice"]["disable_parallel_tool_use"] is True

    def test_assistant_tool_calls_become_tool_use_blocks(self):
        messages = [
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                    }
                ],
            },
        ]
        payload = build_anthropic_payload(messages, "m")
        block = payload["messages"][1]["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "call_1"
        assert block["input"] == {"path": "a.py"}

    def test_malformed_tool_arguments_do_not_raise(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c", "function": {"name": "f", "arguments": "not json{"}}
                ],
            }
        ]
        payload = build_anthropic_payload(messages, "m")
        assert payload["messages"][0]["content"][0]["input"] == {}

    def test_tool_result_becomes_user_tool_result_block(self):
        messages = [
            {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
        ]
        payload = build_anthropic_payload(messages, "m")
        block = payload["messages"][0]["content"][0]
        assert payload["messages"][0]["role"] == "user"
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "call_1"

    def test_consecutive_tool_results_merge_into_one_user_turn(self):
        messages = [
            {"role": "tool", "tool_call_id": "a", "content": "1"},
            {"role": "tool", "tool_call_id": "b", "content": "2"},
        ]
        payload = build_anthropic_payload(messages, "m")
        assert len(payload["messages"]) == 1
        assert len(payload["messages"][0]["content"]) == 2

    def test_base64_image_converted_to_anthropic_source(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    }
                ],
            }
        ]
        payload = build_anthropic_payload(messages, "m")
        source = payload["messages"][0]["content"][0]["source"]
        assert source["type"] == "base64"
        assert source["media_type"] == "image/png"
        assert source["data"] == "AAAA"

    def test_malformed_data_url_is_skipped_not_fatal(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "keep me"},
                    {"type": "image_url", "image_url": {"url": "data:garbage"}},
                ],
            }
        ]
        payload = build_anthropic_payload(messages, "m")
        blocks = payload["messages"][0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["text"] == "keep me"


class TestResponseConversion:
    def test_text_content_becomes_message_content(self):
        data = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = anthropic_response_to_openai(data, "claude-sonnet-4")
        assert result["choices"][0]["message"]["content"] == "hello"
        assert result["choices"][0]["finish_reason"] == "stop"

    def test_tool_use_becomes_openai_tool_calls(self):
        data = {
            "id": "msg_1",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"p": "a"}}
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = anthropic_response_to_openai(data, "m")
        call = result["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"p": "a"}
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    def test_max_tokens_stop_reason_maps_to_length(self):
        data = {"content": [], "stop_reason": "max_tokens", "usage": {}}
        assert anthropic_response_to_openai(data, "m")["choices"][0]["finish_reason"] == "length"

    def test_cache_tokens_surface_in_usage(self):
        data = {
            "content": [{"type": "text", "text": "x"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 900,
            },
        }
        usage = anthropic_response_to_openai(data, "m")["usage"]
        assert usage["cache_read_input_tokens"] == 900
        assert usage["cache_creation_input_tokens"] == 50
        assert usage["prompt_tokens_details"]["cached_tokens"] == 900

    def test_prompt_tokens_includes_all_input_classes(self):
        """Anthropic reports input_tokens excluding cached; the total must not."""
        data = {
            "content": [],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 900,
            },
        }
        usage = anthropic_response_to_openai(data, "m")["usage"]
        assert usage["prompt_tokens"] == 1050
        assert usage["total_tokens"] == 1070

    def test_no_cache_fields_when_caching_unused(self):
        data = {"content": [], "stop_reason": "end_turn", "usage": {"input_tokens": 5}}
        usage = anthropic_response_to_openai(data, "m")["usage"]
        assert "cache_read_input_tokens" not in usage

    def test_response_shape_is_openai_chat_completion(self):
        data = {"content": [], "stop_reason": "end_turn", "usage": {}}
        result = anthropic_response_to_openai(data, "m")
        assert result["object"] == "chat.completion"
        assert "choices" in result and "usage" in result
        assert result["choices"][0]["message"]["role"] == "assistant"
