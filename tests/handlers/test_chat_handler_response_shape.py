"""Regression tests for dict-vs-object provider-response handling in ChatInferenceHandler.

Most provider clients return OpenAI-SDK objects, but some (notably Google Vertex)
return OpenAI-*shaped* dicts. The authenticated handler previously extracted the
response with attribute access only, so a dict response raised
``ValueError("Provider response missing choices")``. These tests lock the contract of
``_rfield`` (the accessor that makes extraction work for both shapes) and prove that a
Vertex-shaped dict — including its finish-only streaming chunk — is handled correctly.
"""

from types import SimpleNamespace

from src.handlers.chat_handler import _rfield


class TestRfieldAccessor:
    def test_reads_dict_key(self):
        assert _rfield({"choices": [1, 2]}, "choices") == [1, 2]

    def test_reads_object_attribute(self):
        obj = SimpleNamespace(choices=[1, 2])
        assert _rfield(obj, "choices") == [1, 2]

    def test_missing_dict_key_returns_default(self):
        assert _rfield({}, "content", "x") == "x"

    def test_missing_attribute_returns_default(self):
        assert _rfield(SimpleNamespace(), "content", "x") == "x"

    def test_none_returns_default(self):
        assert _rfield(None, "content", "x") == "x"


def _extract_non_stream(provider_response):
    """Mirror of the non-streaming extraction block in ChatInferenceHandler.process."""
    usage = _rfield(provider_response, "usage")
    if usage:
        prompt_tokens = _rfield(usage, "prompt_tokens", 0) or 0
        completion_tokens = _rfield(usage, "completion_tokens", 0) or 0
    else:
        prompt_tokens = completion_tokens = 0

    choices = _rfield(provider_response, "choices")
    if not choices:
        raise ValueError("Provider response missing choices")
    choice = choices[0]
    message = _rfield(choice, "message")
    if not message:
        raise ValueError("Provider response missing message")
    return {
        "content": _rfield(message, "content", ""),
        "finish_reason": _rfield(choice, "finish_reason", "stop"),
        "tool_calls": _rfield(message, "tool_calls"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


class TestVertexShapedResponse:
    """A Google-Vertex-shaped dict (see make_google_vertex_request_openai)."""

    def _vertex_response(self):
        return {
            "id": "vertex-123",
            "object": "text_completion",
            "model": "google/gemini-1.5-pro",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello world"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }

    def test_dict_response_does_not_raise_missing_choices(self):
        result = _extract_non_stream(self._vertex_response())
        assert result["content"] == "hello world"
        assert result["finish_reason"] == "stop"
        assert result["prompt_tokens"] == 11
        assert result["completion_tokens"] == 3

    def test_dict_tool_calls_stay_native_dicts(self):
        resp = self._vertex_response()
        resp["choices"][0]["message"]["tool_calls"] = [
            {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        ]
        result = _extract_non_stream(resp)
        assert isinstance(result["tool_calls"], list)
        assert result["tool_calls"][0]["id"] == "call_1"

    def test_object_response_still_works(self):
        obj = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
        )
        result = _extract_non_stream(obj)
        assert result["content"] == "hi"
        assert result["prompt_tokens"] == 5


def _stream_should_emit(chunk):
    """Mirror of the streaming emit-guard in ChatInferenceHandler.process_stream."""
    choices = _rfield(chunk, "choices")
    if not choices:
        return None
    choice = choices[0]
    delta = _rfield(choice, "delta")
    chunk_finish_reason = _rfield(choice, "finish_reason")
    if delta is not None or chunk_finish_reason is not None:
        return {
            "content": _rfield(delta, "content"),
            "finish_reason": chunk_finish_reason,
        }
    return None


class TestVertexShapedStream:
    """Vertex streaming yields dict chunks; the finish chunk has an empty delta ({})."""

    def test_content_chunk_emitted(self):
        chunk = {
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "hi"}, "finish_reason": None}
            ],
        }
        out = _stream_should_emit(chunk)
        assert out is not None
        assert out["content"] == "hi"

    def test_finish_only_chunk_not_dropped(self):
        # Empty delta ({}) is falsy — the old `if delta:` guard dropped this and lost finish_reason.
        chunk = {
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        out = _stream_should_emit(chunk)
        assert out is not None
        assert out["finish_reason"] == "stop"
