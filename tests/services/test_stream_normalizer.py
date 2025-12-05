"""
Tests for StreamNormalizer - Backend Streaming Standardization

These tests verify that the StreamNormalizer correctly standardizes
streaming responses from various AI providers to the OpenAI Chat Completions format.

Test coverage:
- OpenAI-compatible provider normalization
- Reasoning field extraction and standardization
- Google Vertex AI format normalization
- Anthropic format normalization
- Error chunk creation
- Finish reason mapping
- Usage extraction
"""

import json
import pytest
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from src.services.stream_normalizer import (
    StreamNormalizer,
    NormalizedChunk,
    NormalizedDelta,
    NormalizedChoice,
    ProviderType,
    PROVIDER_TYPE_MAP,
    REASONING_FIELD_NAMES,
    create_normalizer,
    normalize_stream,
    create_error_sse_chunk,
    create_done_sse,
)


# Mock classes to simulate OpenAI SDK streaming objects
@dataclass
class MockDelta:
    """Mock delta object for testing"""
    role: str | None = None
    content: str | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None
    thinking: str | None = None
    tool_calls: list | None = None


@dataclass
class MockChoice:
    """Mock choice object for testing"""
    index: int = 0
    delta: MockDelta = None
    finish_reason: str | None = None

    def __post_init__(self):
        if self.delta is None:
            self.delta = MockDelta()


@dataclass
class MockUsage:
    """Mock usage object for testing"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class MockChunk:
    """Mock chunk object for testing OpenAI-compatible streaming"""
    id: str = "chatcmpl-test123"
    object: str = "chat.completion.chunk"
    created: int = None
    model: str = "gpt-4"
    choices: list = None
    usage: MockUsage | None = None

    def __post_init__(self):
        if self.created is None:
            self.created = int(time.time())
        if self.choices is None:
            self.choices = [MockChoice()]


class TestStreamNormalizerInitialization:
    """Tests for StreamNormalizer initialization"""

    def test_init_with_openai_compatible_provider(self):
        """Test initialization with OpenAI-compatible provider"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        assert normalizer.provider == "openrouter"
        assert normalizer.model == "gpt-4"
        assert normalizer.provider_type == ProviderType.OPENAI_COMPATIBLE
        assert normalizer._chunk_count == 0

    def test_init_with_google_provider(self):
        """Test initialization with Google Vertex provider"""
        normalizer = StreamNormalizer(provider="google-vertex", model="gemini-pro")
        assert normalizer.provider_type == ProviderType.GOOGLE

    def test_init_with_qwen_provider(self):
        """Test initialization with Alibaba Cloud (Qwen) provider"""
        normalizer = StreamNormalizer(provider="alibaba-cloud", model="qwen-max")
        assert normalizer.provider_type == ProviderType.QWEN

    def test_init_with_deepseek_provider(self):
        """Test initialization with Fireworks (DeepSeek) provider"""
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        assert normalizer.provider_type == ProviderType.DEEPSEEK

    def test_detect_reasoning_model(self):
        """Test reasoning model detection"""
        thinking_models = [
            "deepseek-r1",
            "deepseek-v3",
            "qwen3-30b-thinking",
            "o1-preview",
            "o3-mini",
        ]
        for model in thinking_models:
            normalizer = StreamNormalizer(provider="openrouter", model=model)
            assert normalizer._is_reasoning_model, f"Expected {model} to be detected as reasoning model"

    def test_non_reasoning_model(self):
        """Test that regular models are not flagged as reasoning"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4-turbo")
        assert not normalizer._is_reasoning_model

    def test_unknown_provider_defaults_to_openai_compatible(self):
        """Test that unknown providers default to OpenAI compatible"""
        normalizer = StreamNormalizer(provider="unknown-provider", model="model")
        assert normalizer.provider_type == ProviderType.OPENAI_COMPATIBLE


class TestProviderTypeMapping:
    """Tests for provider type mapping"""

    def test_all_openai_compatible_providers(self):
        """Test that expected providers are mapped to OpenAI compatible"""
        openai_compatible = [
            "openrouter", "together", "groq", "mistral", "featherless",
            "huggingface", "xai", "cerebras", "chutes", "clarifai"
        ]
        for provider in openai_compatible:
            assert PROVIDER_TYPE_MAP.get(provider) == ProviderType.OPENAI_COMPATIBLE, \
                f"Expected {provider} to be OpenAI compatible"

    def test_google_provider_mapping(self):
        """Test Google Vertex provider mapping"""
        assert PROVIDER_TYPE_MAP.get("google-vertex") == ProviderType.GOOGLE

    def test_anthropic_provider_mapping(self):
        """Test Anthropic provider mapping"""
        assert PROVIDER_TYPE_MAP.get("anthropic") == ProviderType.ANTHROPIC


class TestOpenAIChunkNormalization:
    """Tests for OpenAI-compatible chunk normalization"""

    def test_normalize_basic_content_chunk(self):
        """Test normalizing a basic content chunk"""
        chunk = MockChunk(
            choices=[MockChoice(delta=MockDelta(content="Hello, world!"))]
        )
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result is not None
        assert result.id == "chatcmpl-test123"
        assert result.model == "gpt-4"
        assert len(result.choices) == 1
        assert result.choices[0]["delta"]["content"] == "Hello, world!"

    def test_normalize_chunk_with_role(self):
        """Test normalizing a chunk with role in delta"""
        chunk = MockChunk(
            choices=[MockChoice(delta=MockDelta(role="assistant", content="Hi"))]
        )
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result.choices[0]["delta"]["role"] == "assistant"
        assert result.choices[0]["delta"]["content"] == "Hi"

    def test_normalize_chunk_with_finish_reason(self):
        """Test normalizing a chunk with finish reason"""
        chunk = MockChunk(
            choices=[MockChoice(delta=MockDelta(), finish_reason="stop")]
        )
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result.choices[0]["finish_reason"] == "stop"

    def test_accumulate_content(self):
        """Test content accumulation across multiple chunks"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")

        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content=", "))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="world!"))]),
        ]

        for chunk in chunks:
            normalizer._normalize_openai_chunk(chunk)

        assert normalizer.get_accumulated_content() == "Hello, world!"

    def test_normalize_chunk_increments_counter(self):
        """Test that normalize_chunk (public method) increments chunk counter"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")

        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(content="A"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="B"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="C"))]),
        ]

        for chunk in chunks:
            normalizer.normalize_chunk(chunk)

        assert normalizer.get_chunk_count() == 3
        assert normalizer.get_accumulated_content() == "ABC"

    def test_normalize_chunk_with_usage(self):
        """Test normalizing a chunk with usage information"""
        chunk = MockChunk(
            choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
            usage=MockUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        )
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result.usage is not None
        assert result.usage["prompt_tokens"] == 10
        assert result.usage["completion_tokens"] == 20
        assert result.usage["total_tokens"] == 30


class TestReasoningFieldExtraction:
    """Tests for reasoning field extraction and standardization"""

    def test_extract_reasoning_from_reasoning_field(self):
        """Test extracting from 'reasoning' field"""
        delta = MockDelta(reasoning="Let me think about this...")
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        result = normalizer._extract_reasoning_from_delta(delta)
        assert result == "Let me think about this..."

    def test_extract_reasoning_from_reasoning_content_field(self):
        """Test extracting from 'reasoning_content' field"""
        delta = MockDelta(reasoning_content="My analysis...")
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        result = normalizer._extract_reasoning_from_delta(delta)
        assert result == "My analysis..."

    def test_extract_reasoning_from_thinking_field(self):
        """Test extracting from 'thinking' field"""
        delta = MockDelta(thinking="I need to consider...")
        normalizer = StreamNormalizer(provider="openrouter", model="claude-3")
        result = normalizer._extract_reasoning_from_delta(delta)
        assert result == "I need to consider..."

    def test_reasoning_accumulation(self):
        """Test reasoning content accumulation"""
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")

        chunk1 = MockChunk(choices=[MockChoice(delta=MockDelta(reasoning="Step 1: "))])
        chunk2 = MockChunk(choices=[MockChoice(delta=MockDelta(reasoning="Analyze input"))])

        normalizer._normalize_openai_chunk(chunk1)
        normalizer._normalize_openai_chunk(chunk2)

        assert normalizer.get_accumulated_reasoning() == "Step 1: Analyze input"

    def test_reasoning_field_in_normalized_output(self):
        """Test that reasoning appears as reasoning_content in output"""
        chunk = MockChunk(
            choices=[MockChoice(delta=MockDelta(
                content="The answer is 42.",
                reasoning="I calculated this by..."
            ))]
        )
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result.choices[0]["delta"]["content"] == "The answer is 42."
        assert result.choices[0]["delta"]["reasoning_content"] == "I calculated this by..."

    def test_no_reasoning_when_not_present(self):
        """Test that reasoning_content is not included when not present"""
        chunk = MockChunk(choices=[MockChoice(delta=MockDelta(content="Just content"))])
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert "reasoning_content" not in result.choices[0]["delta"]


class TestFinishReasonNormalization:
    """Tests for finish reason normalization"""

    @pytest.mark.parametrize("input_reason,expected", [
        ("stop", "stop"),
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("length", "length"),
        ("max_tokens", "length"),
        ("content_filter", "error"),
        ("safety", "error"),
        ("tool_calls", "tool_calls"),
        ("function_call", "function_call"),
        (None, None),
    ])
    def test_finish_reason_mapping(self, input_reason, expected):
        """Test various finish reason mappings"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_finish_reason(input_reason)
        assert result == expected

    def test_unknown_finish_reason_defaults_to_stop(self):
        """Test that unknown finish reasons default to 'stop'"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_finish_reason("unknown_reason")
        assert result == "stop"


class TestGoogleVertexNormalization:
    """Tests for Google Vertex AI format normalization"""

    def test_normalize_sse_string_chunk(self):
        """Test normalizing an SSE string chunk from Google wrapper"""
        sse_data = json.dumps({
            "id": "vertex-123",
            "object": "text_completion.chunk",
            "created": 1234567890,
            "model": "gemini-pro",
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "Hello!"},
                "finish_reason": None
            }]
        })
        chunk = f"data: {sse_data}"

        normalizer = StreamNormalizer(provider="google-vertex", model="gemini-pro")
        result = normalizer._normalize_google_chunk(chunk)

        assert result is not None
        assert result.id == "vertex-123"
        assert result.choices[0]["delta"]["content"] == "Hello!"

    def test_normalize_done_signal(self):
        """Test that [DONE] signal returns None"""
        normalizer = StreamNormalizer(provider="google-vertex", model="gemini-pro")
        result = normalizer._normalize_google_chunk("data: [DONE]")
        assert result is None

    def test_google_finish_reason_mapping(self):
        """Test Google finish reason numeric mapping"""
        normalizer = StreamNormalizer(provider="google-vertex", model="gemini-pro")

        assert normalizer._map_google_finish_reason(1) == "stop"
        assert normalizer._map_google_finish_reason(2) == "length"
        assert normalizer._map_google_finish_reason(3) == "error"
        assert normalizer._map_google_finish_reason(0) is None


class TestAnthropicNormalization:
    """Tests for Anthropic format normalization"""

    def test_normalize_content_block_delta(self):
        """Test normalizing Anthropic content_block_delta event"""
        chunk = {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": "Hello from Claude!"
            },
            "message": {"id": "msg-123", "model": "claude-3-opus"}
        }

        normalizer = StreamNormalizer(provider="anthropic", model="claude-3-opus")
        result = normalizer._normalize_anthropic_chunk(chunk)

        assert result is not None
        assert result.choices[0]["delta"]["content"] == "Hello from Claude!"

    def test_normalize_thinking_delta(self):
        """Test normalizing Anthropic thinking_delta event"""
        chunk = {
            "type": "content_block_delta",
            "delta": {
                "type": "thinking_delta",
                "thinking": "Let me reason about this..."
            },
            "message": {"id": "msg-123", "model": "claude-3-opus"}
        }

        normalizer = StreamNormalizer(provider="anthropic", model="claude-3-opus")
        result = normalizer._normalize_anthropic_chunk(chunk)

        assert result is not None
        assert result.choices[0]["delta"]["reasoning_content"] == "Let me reason about this..."

    def test_normalize_message_delta_stop_reason(self):
        """Test normalizing Anthropic message_delta with stop_reason"""
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "message": {"id": "msg-123", "model": "claude-3-opus"}
        }

        normalizer = StreamNormalizer(provider="anthropic", model="claude-3-opus")
        result = normalizer._normalize_anthropic_chunk(chunk)

        assert result.choices[0]["finish_reason"] == "stop"

    def test_anthropic_stop_reason_mapping(self):
        """Test Anthropic stop reason to finish reason mapping"""
        normalizer = StreamNormalizer(provider="anthropic", model="claude-3")

        assert normalizer._map_anthropic_stop_reason("end_turn") == "stop"
        assert normalizer._map_anthropic_stop_reason("max_tokens") == "length"
        assert normalizer._map_anthropic_stop_reason("stop_sequence") == "stop"
        assert normalizer._map_anthropic_stop_reason("tool_use") == "tool_calls"


class TestNormalizedChunkSerialization:
    """Tests for NormalizedChunk serialization"""

    def test_to_dict(self):
        """Test converting NormalizedChunk to dictionary"""
        chunk = NormalizedChunk(
            id="test-123",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}],
        )

        result = chunk.to_dict()

        assert result["id"] == "test-123"
        assert result["object"] == "chat.completion.chunk"
        assert result["model"] == "gpt-4"
        assert result["choices"][0]["delta"]["content"] == "Hi"
        assert "usage" not in result

    def test_to_dict_with_usage(self):
        """Test converting NormalizedChunk with usage to dictionary"""
        chunk = NormalizedChunk(
            id="test-123",
            model="gpt-4",
            choices=[],
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

        result = chunk.to_dict()
        assert result["usage"]["total_tokens"] == 30

    def test_to_sse(self):
        """Test converting NormalizedChunk to SSE format"""
        chunk = NormalizedChunk(
            id="test-123",
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}],
        )

        result = chunk.to_sse()

        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        assert "test-123" in result


class TestNormalizedDeltaSerialization:
    """Tests for NormalizedDelta serialization"""

    def test_to_dict_with_content(self):
        """Test delta with content only"""
        delta = NormalizedDelta(content="Hello")
        result = delta.to_dict()
        assert result == {"content": "Hello"}

    def test_to_dict_with_role_and_content(self):
        """Test delta with role and content"""
        delta = NormalizedDelta(role="assistant", content="Hello")
        result = delta.to_dict()
        assert result == {"role": "assistant", "content": "Hello"}

    def test_to_dict_with_reasoning(self):
        """Test delta with reasoning_content"""
        delta = NormalizedDelta(content="Answer", reasoning_content="Thinking...")
        result = delta.to_dict()
        assert result == {"content": "Answer", "reasoning_content": "Thinking..."}

    def test_to_dict_excludes_none_values(self):
        """Test that None values are excluded"""
        delta = NormalizedDelta(content="Hello")
        result = delta.to_dict()
        assert "role" not in result
        assert "reasoning_content" not in result
        assert "tool_calls" not in result


class TestErrorChunkCreation:
    """Tests for error chunk creation"""

    def test_create_error_sse_chunk_basic(self):
        """Test creating a basic error SSE chunk"""
        result = create_error_sse_chunk(
            error_message="Something went wrong",
            error_type="test_error"
        )

        assert result.startswith("data: ")
        assert result.endswith("\n\n")

        # Parse the JSON
        data = json.loads(result[6:-2])
        assert data["error"]["message"] == "Something went wrong"
        assert data["error"]["type"] == "test_error"

    def test_create_error_sse_chunk_with_context(self):
        """Test creating an error SSE chunk with provider/model context"""
        result = create_error_sse_chunk(
            error_message="Provider error",
            error_type="provider_error",
            provider="openrouter",
            model="gpt-4"
        )

        data = json.loads(result[6:-2])
        assert data["error"]["provider"] == "openrouter"
        assert data["error"]["model"] == "gpt-4"

    def test_create_done_sse(self):
        """Test creating the [DONE] SSE signal"""
        result = create_done_sse()
        assert result == "data: [DONE]\n\n"


class TestFactoryFunctions:
    """Tests for factory/convenience functions"""

    def test_create_normalizer(self):
        """Test create_normalizer factory function"""
        normalizer = create_normalizer(provider="openrouter", model="gpt-4")
        assert isinstance(normalizer, StreamNormalizer)
        assert normalizer.provider == "openrouter"
        assert normalizer.model == "gpt-4"

    def test_normalize_stream_function(self):
        """Test normalize_stream convenience function"""
        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(role="assistant"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hi"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")]),
        ]

        results = list(normalize_stream(iter(chunks), provider="openrouter", model="gpt-4"))

        assert len(results) == 3
        assert results[0].choices[0]["delta"]["role"] == "assistant"
        assert results[1].choices[0]["delta"]["content"] == "Hi"
        assert results[2].choices[0]["finish_reason"] == "stop"


class TestStreamNormalizerIntegration:
    """Integration tests for StreamNormalizer"""

    def test_full_stream_normalization(self):
        """Test normalizing a complete stream"""
        chunks = [
            MockChunk(
                id="chunk-1",
                choices=[MockChoice(delta=MockDelta(role="assistant"))]
            ),
            MockChunk(
                id="chunk-2",
                choices=[MockChoice(delta=MockDelta(content="The "))]
            ),
            MockChunk(
                id="chunk-3",
                choices=[MockChoice(delta=MockDelta(content="answer "))]
            ),
            MockChunk(
                id="chunk-4",
                choices=[MockChoice(delta=MockDelta(content="is 42."))]
            ),
            MockChunk(
                id="chunk-5",
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                usage=MockUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            ),
        ]

        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        results = list(normalizer.normalize(iter(chunks)))

        assert len(results) == 5
        assert normalizer.get_accumulated_content() == "The answer is 42."
        assert normalizer.get_chunk_count() == 5

        # Check last chunk has usage
        assert results[-1].usage["total_tokens"] == 15

    def test_stream_with_reasoning(self):
        """Test normalizing a stream with reasoning content"""
        chunks = [
            MockChunk(
                choices=[MockChoice(delta=MockDelta(
                    reasoning="Let me think..."
                ))]
            ),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(
                    reasoning="Analyzing the problem..."
                ))]
            ),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(
                    content="The answer is 42."
                ))]
            ),
        ]

        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        results = list(normalizer.normalize(iter(chunks)))

        assert normalizer.get_accumulated_reasoning() == "Let me think...Analyzing the problem..."
        assert normalizer.get_accumulated_content() == "The answer is 42."

        # Check that reasoning appears as reasoning_content in output
        assert results[0].choices[0]["delta"]["reasoning_content"] == "Let me think..."

    def test_error_handling_in_stream(self):
        """Test that errors during normalization are caught and returned"""
        def bad_stream():
            yield MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))])
            raise RuntimeError("Stream error!")

        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        results = list(normalizer.normalize(bad_stream()))

        # Should get the first chunk plus an error chunk
        assert len(results) == 2
        assert results[0].choices[0]["delta"]["content"] == "Hello"
        assert results[1].choices[0]["finish_reason"] == "error"


class TestReasoningFieldNames:
    """Tests for the reasoning field name constants"""

    def test_all_expected_fields_present(self):
        """Test that all expected reasoning field names are defined"""
        expected_fields = {
            "reasoning",
            "reasoning_content",
            "thinking",
            "analysis",
            "inner_thought",
            "thoughts",
            "thought",
            "chain_of_thought",
            "cot",
        }
        assert expected_fields.issubset(REASONING_FIELD_NAMES)

    def test_reasoning_fields_is_frozen(self):
        """Test that REASONING_FIELD_NAMES is immutable"""
        assert isinstance(REASONING_FIELD_NAMES, frozenset)


class TestEdgeCases:
    """Tests for edge cases and error conditions"""

    def test_empty_stream(self):
        """Test normalizing an empty stream"""
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        results = list(normalizer.normalize(iter([])))

        assert len(results) == 0
        assert normalizer.get_chunk_count() == 0
        assert normalizer.get_accumulated_content() == ""

    def test_chunk_with_empty_choices(self):
        """Test normalizing a chunk with empty choices"""
        chunk = MockChunk(choices=[])
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert result.choices == []

    def test_chunk_with_multiple_choices(self):
        """Test normalizing a chunk with multiple choices"""
        chunk = MockChunk(
            choices=[
                MockChoice(index=0, delta=MockDelta(content="First")),
                MockChoice(index=1, delta=MockDelta(content="Second")),
            ]
        )
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        assert len(result.choices) == 2
        assert result.choices[0]["delta"]["content"] == "First"
        assert result.choices[1]["delta"]["content"] == "Second"

    def test_none_content_not_added_to_delta(self):
        """Test that None content is not included in delta"""
        chunk = MockChunk(choices=[MockChoice(delta=MockDelta(content=None))])
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")
        result = normalizer._normalize_openai_chunk(chunk)

        # Content should not be in delta if it was None
        assert result.choices[0]["delta"].get("content") is None

    def test_empty_string_content_not_accumulated(self):
        """Test that empty string content is not accumulated"""
        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(content=""))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))]),
        ]
        normalizer = StreamNormalizer(provider="openrouter", model="gpt-4")

        for chunk in chunks:
            normalizer._normalize_openai_chunk(chunk)

        # Only "Hello" should be accumulated (empty string is falsy)
        assert normalizer.get_accumulated_content() == "Hello"
