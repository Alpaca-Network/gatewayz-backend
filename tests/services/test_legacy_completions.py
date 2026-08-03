"""Tests for the legacy /v1/completions shim.

The translation is lossy in specific ways. These tests pin the behaviour that
the losses are *reported* rather than silent — a caller batching 20 prompts and
receiving one answer needs to be told why.
"""

import pytest
from fastapi import HTTPException

from src.routes.completions import (
    CompletionsRequest,
    chat_response_to_completion,
    normalize_prompt,
    unsupported_params,
)


class TestNormalizePrompt:
    def test_string_prompt_passes_through(self):
        text, warnings = normalize_prompt("write a haiku")
        assert text == "write a haiku"
        assert warnings == []

    def test_single_element_list_has_no_warning(self):
        text, warnings = normalize_prompt(["only one"])
        assert text == "only one"
        assert warnings == []

    def test_batched_prompts_warn_rather_than_truncate_silently(self):
        text, warnings = normalize_prompt(["first", "second", "third"])
        assert text == "first"
        assert len(warnings) == 1
        assert "only the first was used" in warnings[0]

    def test_empty_list_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            normalize_prompt([])
        assert exc.value.status_code == 400


class TestUnsupportedParams:
    def test_clean_request_drops_nothing(self):
        req = CompletionsRequest(model="m", prompt="p")
        assert unsupported_params(req) == []

    def test_suffix_is_reported(self):
        req = CompletionsRequest(model="m", prompt="p", suffix="tail")
        assert "suffix" in unsupported_params(req)

    def test_echo_is_reported(self):
        req = CompletionsRequest(model="m", prompt="p", echo=True)
        assert "echo" in unsupported_params(req)

    def test_best_of_one_is_not_reported(self):
        """best_of=1 is the default and means nothing was requested."""
        req = CompletionsRequest(model="m", prompt="p", best_of=1)
        assert unsupported_params(req) == []

    def test_best_of_above_one_is_reported(self):
        req = CompletionsRequest(model="m", prompt="p", best_of=4)
        assert "best_of" in unsupported_params(req)

    def test_echo_false_is_not_reported(self):
        req = CompletionsRequest(model="m", prompt="p", echo=False)
        assert unsupported_params(req) == []


class TestResponseTranslation:
    CHAT = {
        "model": "openai/gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello world"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }

    def test_object_type_is_text_completion(self):
        result = chat_response_to_completion(self.CHAT, "m", "cmpl-1")
        assert result["object"] == "text_completion"

    def test_message_content_becomes_text(self):
        result = chat_response_to_completion(self.CHAT, "m", "cmpl-1")
        assert result["choices"][0]["text"] == "hello world"

    def test_finish_reason_preserved(self):
        result = chat_response_to_completion(self.CHAT, "m", "cmpl-1")
        assert result["choices"][0]["finish_reason"] == "stop"

    def test_usage_preserved(self):
        result = chat_response_to_completion(self.CHAT, "m", "cmpl-1")
        assert result["usage"]["total_tokens"] == 7

    def test_null_content_becomes_empty_string_not_none(self):
        """Legacy clients index into .text and would crash on None."""
        chat = {"choices": [{"index": 0, "message": {"content": None}, "finish_reason": "stop"}]}
        result = chat_response_to_completion(chat, "m", "cmpl-1")
        assert result["choices"][0]["text"] == ""

    def test_no_choices_yields_empty_list_not_an_error(self):
        result = chat_response_to_completion({"choices": []}, "m", "cmpl-1")
        assert result["choices"] == []

    def test_id_is_the_supplied_completion_id(self):
        result = chat_response_to_completion(self.CHAT, "m", "cmpl-abc")
        assert result["id"] == "cmpl-abc"


class TestRequestSchema:
    def test_minimal_request(self):
        req = CompletionsRequest(model="m", prompt="p")
        assert req.stream is False

    def test_unknown_fields_allowed(self):
        req = CompletionsRequest(model="m", prompt="p", future_param=1)
        assert req.model == "m"

    def test_list_prompt_accepted(self):
        req = CompletionsRequest(model="m", prompt=["a", "b"])
        assert isinstance(req.prompt, list)
