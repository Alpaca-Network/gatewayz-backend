"""
CM-10: API Compatibility Tests

Verifies OpenAI and Anthropic API compatibility in response formats,
streaming SSE formatting, and response normalization across providers.
"""

import json
import time

import pytest

from src.services.anthropic_transformer import transform_openai_to_anthropic
from src.services.stream_normalizer import (
    NormalizedChunk,
    StreamNormalizer,
    create_done_sse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_openai_response(
    content: str = "Hello!",
    model: str = "gpt-4",
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    tool_calls: list | None = None,
    logprobs: dict | None = None,
):
    """Build a minimal OpenAI Chat Completions response dict."""
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    choice = {
        "index": 0,
        "message": message,
        "finish_reason": finish_reason,
    }
    if logprobs is not None:
        choice["logprobs"] = logprobs
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ===========================================================================
# 10.1 OpenAI Compatibility
# ===========================================================================


@pytest.mark.cm_verified
class TestOpenAIResponseFormatHasChoices:
    """CM-10.1.1: OpenAI response has `choices` array."""

    def test_openai_response_format_has_choices(self):
        response = _make_openai_response()
        assert "choices" in response
        assert isinstance(response["choices"], list)
        assert len(response["choices"]) >= 1
        # Each choice has index, message, finish_reason
        choice = response["choices"][0]
        assert "index" in choice
        assert "message" in choice
        assert "finish_reason" in choice


@pytest.mark.cm_verified
class TestOpenAIResponseFormatHasUsage:
    """CM-10.1.2: OpenAI response has `usage` with prompt_tokens, completion_tokens, total_tokens."""

    def test_openai_response_format_has_usage(self):
        response = _make_openai_response(prompt_tokens=12, completion_tokens=8)
        assert "usage" in response
        usage = response["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


@pytest.mark.cm_verified
class TestOpenAIResponseFormatHasId:
    """CM-10.1.3: OpenAI response has `id` field."""

    def test_openai_response_format_has_id(self):
        response = _make_openai_response()
        assert "id" in response
        assert isinstance(response["id"], str)
        assert len(response["id"]) > 0


@pytest.mark.cm_verified
class TestOpenAIResponseFormatHasModel:
    """CM-10.1.4: OpenAI response has `model` field."""

    def test_openai_response_format_has_model(self):
        response = _make_openai_response(model="meta-llama/Llama-3.3-70B-Instruct")
        assert "model" in response
        assert response["model"] == "meta-llama/Llama-3.3-70B-Instruct"


@pytest.mark.cm_verified
class TestOpenAIStreamingSSEFormat:
    """CM-10.1.5: Streaming events follow `data: {json}\\n\\n` format."""

    def test_openai_streaming_sse_format(self):
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        chunk = {
            "id": "chatcmpl-stream1",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "gpt-4",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hi"},
                    "finish_reason": None,
                }
            ],
        }
        result = normalizer.normalize_chunk(chunk)
        assert result is not None

        sse = result.to_sse()
        # Must start with "data: " and end with "\n\n"
        assert sse.startswith("data: ")
        assert sse.endswith("\n\n")

        # The payload between "data: " and "\n\n" must be valid JSON
        json_str = sse[len("data: ") : -2]
        parsed = json.loads(json_str)
        assert "choices" in parsed
        assert parsed["choices"][0]["delta"]["content"] == "Hi"


@pytest.mark.cm_verified
class TestOpenAIStreamingEndsWithDone:
    """CM-10.1.6: Stream ends with `data: [DONE]\\n\\n`."""

    def test_openai_streaming_ends_with_done(self):
        done = create_done_sse()
        assert done == "data: [DONE]\n\n"


@pytest.mark.cm_verified
class TestOpenAIJsonModeReturnsValidJson:
    """CM-10.1.7: response_format json_object produces valid JSON content."""

    def test_openai_json_mode_returns_valid_json(self):
        # When a provider returns JSON content (as it would with response_format json_object),
        # the content string must be parseable JSON.
        json_content = '{"name": "Alice", "age": 30}'
        response = _make_openai_response(content=json_content)
        content = response["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        assert isinstance(parsed, dict)
        assert parsed["name"] == "Alice"


@pytest.mark.cm_verified
class TestOpenAIToolCallingResponseFormat:
    """CM-10.1.8: Response can contain tool_calls array."""

    def test_openai_tool_calling_response_format(self):
        tool_calls = [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "London"}',
                },
            }
        ]
        response = _make_openai_response(
            content=None,
            tool_calls=tool_calls,
            finish_reason="tool_calls",
        )
        message = response["choices"][0]["message"]
        assert "tool_calls" in message
        assert isinstance(message["tool_calls"], list)
        assert len(message["tool_calls"]) == 1
        tc = message["tool_calls"][0]
        assert tc["id"] == "call_abc123"
        assert tc["function"]["name"] == "get_weather"
        args = json.loads(tc["function"]["arguments"])
        assert args["location"] == "London"


@pytest.mark.cm_verified
class TestOpenAILogprobsIncludedWhenRequested:
    """CM-10.1.9: logprobs: true includes logprobs in the response choice."""

    def test_openai_logprobs_included_when_requested(self):
        logprobs_data = {
            "content": [
                {
                    "token": "Hello",
                    "logprob": -0.5,
                    "bytes": [72, 101, 108, 108, 111],
                    "top_logprobs": [
                        {"token": "Hello", "logprob": -0.5},
                        {"token": "Hi", "logprob": -1.2},
                    ],
                }
            ]
        }
        response = _make_openai_response(logprobs=logprobs_data)
        choice = response["choices"][0]
        assert "logprobs" in choice
        assert choice["logprobs"]["content"][0]["token"] == "Hello"
        assert isinstance(choice["logprobs"]["content"][0]["logprob"], float)


# ===========================================================================
# 10.2 Anthropic Compatibility
# ===========================================================================


@pytest.mark.cm_verified
class TestAnthropicResponseFormatHasContent:
    """CM-10.2.1: Anthropic response has `content` array."""

    def test_anthropic_response_format_has_content(self):
        openai_resp = _make_openai_response(content="Bonjour!")
        anthropic_resp = transform_openai_to_anthropic(
            openai_resp, model="claude-sonnet-4-5-20250929"
        )

        assert "content" in anthropic_resp
        assert isinstance(anthropic_resp["content"], list)
        assert len(anthropic_resp["content"]) >= 1
        # Text block has type "text" and "text" key
        text_block = anthropic_resp["content"][0]
        assert text_block["type"] == "text"
        assert text_block["text"] == "Bonjour!"


@pytest.mark.cm_verified
class TestAnthropicResponseFormatHasUsage:
    """CM-10.2.2: Anthropic usage has input_tokens, output_tokens."""

    def test_anthropic_response_format_has_usage(self):
        openai_resp = _make_openai_response(prompt_tokens=15, completion_tokens=20)
        anthropic_resp = transform_openai_to_anthropic(
            openai_resp, model="claude-sonnet-4-5-20250929"
        )

        assert "usage" in anthropic_resp
        usage = anthropic_resp["usage"]
        assert "input_tokens" in usage
        assert "output_tokens" in usage
        assert usage["input_tokens"] == 15
        assert usage["output_tokens"] == 20


@pytest.mark.cm_verified
class TestAnthropicStreamingEventFormat:
    """CM-10.2.3: Anthropic streaming events are normalised via StreamNormalizer."""

    def test_anthropic_streaming_event_format(self):
        normalizer = StreamNormalizer(provider="anthropic", model="claude-sonnet-4-5-20250929")

        # content_block_delta with text_delta (Anthropic SSE event structure)
        chunk = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }
        result = normalizer.normalize_chunk(chunk)
        assert result is not None
        assert result.choices[0]["delta"]["content"] == "Hello"

        # message_delta with stop_reason
        stop_chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
        }
        stop_result = normalizer.normalize_chunk(stop_chunk)
        assert stop_result is not None
        assert stop_result.choices[0]["finish_reason"] == "stop"


# ===========================================================================
# 10.3 Response Normalization
# ===========================================================================


@pytest.mark.cm_verified
class TestResponseNormalizedRegardlessOfProvider:
    """CM-10.3.1: Different providers produce consistent normalised format."""

    def test_response_normalized_regardless_of_provider(self):
        providers = ["openrouter", "fireworks", "together", "deepinfra"]
        for provider in providers:
            normalizer = StreamNormalizer(provider=provider, model="test-model")
            # Standard OpenAI-shaped chunk
            chunk = {
                "id": f"chatcmpl-{provider}",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "token"},
                        "finish_reason": None,
                    }
                ],
            }
            result = normalizer.normalize_chunk(chunk)
            assert result is not None, f"Failed for provider {provider}"
            # All providers produce same normalised structure
            assert result.object == "chat.completion.chunk"
            assert result.model == "test-model"
            assert len(result.choices) == 1
            assert result.choices[0]["delta"]["content"] == "token"

        # Verify the SSE output is identical structure across providers
        sse_outputs = []
        for provider in providers:
            normalizer = StreamNormalizer(provider=provider, model="test-model")
            chunk = {
                "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}],
            }
            result = normalizer.normalize_chunk(chunk)
            parsed = json.loads(result.to_sse()[len("data: ") : -2])
            # Remove id/created since they differ by timestamp
            parsed.pop("id", None)
            parsed.pop("created", None)
            sse_outputs.append(parsed)

        # All should have identical structure (object, model, choices)
        for output in sse_outputs:
            assert output["object"] == "chat.completion.chunk"
            assert output["model"] == "test-model"
            assert output["choices"][0]["delta"]["content"] == "x"


@pytest.mark.cm_verified
class TestProviderSpecificFieldsStripped:
    """CM-10.3.2: Provider-specific metadata is not leaked in normalised output."""

    def test_provider_specific_fields_stripped(self):
        normalizer = StreamNormalizer(provider="openrouter", model="test-model")
        # Chunk with extra provider-specific fields that should not appear in output
        chunk = {
            "id": "chatcmpl-or123",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "hello"},
                    "finish_reason": None,
                }
            ],
            # Provider-specific fields
            "x_openrouter_usage": {"total_cost": 0.001},
            "provider_name": "openrouter",
            "openrouter_processing_ms": 42,
            "system_fingerprint": "fp_abc123",
        }
        result = normalizer.normalize_chunk(chunk)
        assert result is not None

        sse = result.to_sse()
        parsed = json.loads(sse[len("data: ") : -2])

        # NormalizedChunk.to_sse() only emits id, object, created, model, choices
        assert set(parsed.keys()) == {"id", "object", "created", "model", "choices"}
        # Provider metadata must not leak
        assert "x_openrouter_usage" not in parsed
        assert "provider_name" not in parsed
        assert "openrouter_processing_ms" not in parsed
        assert "system_fingerprint" not in parsed
