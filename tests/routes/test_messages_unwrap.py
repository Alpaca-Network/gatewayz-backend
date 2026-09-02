#!/usr/bin/env python3
"""
Tests for _unwrap_chat_result — the /v1/messages ↔ chat_completions seam.

Regression context: chat_completions returns JSONResponse (rendered bytes in
.body) since rate-limit headers were added; the messages route only handled
plain dicts, so every successful Anthropic upstream call surfaced as
500 "unexpected upstream response shape". CM tests only exercised the
dict-based path, which is how it slipped through.
"""

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.routes.messages import _unwrap_chat_result

PAYLOAD = {
    "id": "chatcmpl-1",
    "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
}


class TestUnwrapChatResult:
    def test_plain_dict_passes_through(self):
        assert _unwrap_chat_result(PAYLOAD) is PAYLOAD

    def test_jsonresponse_bytes_body_is_decoded(self):
        resp = JSONResponse(content=PAYLOAD, headers={"X-RateLimit-Remaining": "9"})
        out = _unwrap_chat_result(resp)
        assert out == PAYLOAD
        assert out["choices"][0]["message"]["content"] == "OK"

    def test_object_with_dict_body(self):
        class R:
            body = PAYLOAD

        assert _unwrap_chat_result(R()) == PAYLOAD

    def test_non_json_bytes_raise_anthropic_500(self):
        class R:
            body = b"\x89PNG not json"

        with pytest.raises(HTTPException) as exc:
            _unwrap_chat_result(R())
        assert exc.value.status_code == 500

    def test_json_but_not_dict_raises(self):
        class R:
            body = json.dumps([1, 2, 3]).encode()

        with pytest.raises(HTTPException):
            _unwrap_chat_result(R())

    def test_garbage_object_raises(self):
        with pytest.raises(HTTPException):
            _unwrap_chat_result(object())
