"""
Stream Normalizer for Standardizing AI Provider Streaming Responses

This module provides a unified StreamNormalizer class that converts streaming
responses from various AI providers into a standardized OpenAI Chat Completions format.

Standard output format (per chunk):
{
    "id": "chatcmpl-xxx",
    "object": "chat.completion.chunk",
    "created": 1234567890,
    "model": "model-name",
    "choices": [{
        "index": 0,
        "delta": {
            "role": "assistant",          # Only in first chunk
            "content": "text delta",      # Actual text content
            "reasoning_content": "thinking delta"  # Normalized reasoning/thinking
        },
        "finish_reason": null              # "stop", "length", or "error" at end
    }]
}

Key standardization rules:
- Content: Always in delta.content
- Reasoning: Always in delta.reasoning_content (normalized from reasoning, thinking,
  analysis, inner_thought, thoughts fields)
- Finish reason: Normalized to stop, length, or error
- Errors: Returned in consistent format within stream
"""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

logger = logging.getLogger(__name__)


class ProviderType(Enum):
    """Supported provider types for stream normalization"""
    # OpenAI-compatible providers (pass-through with minimal normalization)
    OPENAI_COMPATIBLE = "openai_compatible"
    # Providers requiring full format normalization
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    # Providers requiring reasoning field normalization
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


# Mapping of provider names to provider types
PROVIDER_TYPE_MAP: dict[str, ProviderType] = {
    # OpenAI-compatible (pass-through)
    "openrouter": ProviderType.OPENAI_COMPATIBLE,
    "together": ProviderType.OPENAI_COMPATIBLE,
    "groq": ProviderType.OPENAI_COMPATIBLE,
    "mistral": ProviderType.OPENAI_COMPATIBLE,
    "featherless": ProviderType.OPENAI_COMPATIBLE,
    "huggingface": ProviderType.OPENAI_COMPATIBLE,
    "aimo": ProviderType.OPENAI_COMPATIBLE,
    "xai": ProviderType.OPENAI_COMPATIBLE,
    "cerebras": ProviderType.OPENAI_COMPATIBLE,
    "chutes": ProviderType.OPENAI_COMPATIBLE,
    "near": ProviderType.OPENAI_COMPATIBLE,
    "vercel-ai-gateway": ProviderType.OPENAI_COMPATIBLE,
    "helicone": ProviderType.OPENAI_COMPATIBLE,
    "aihubmix": ProviderType.OPENAI_COMPATIBLE,
    "anannas": ProviderType.OPENAI_COMPATIBLE,
    "alpaca-network": ProviderType.OPENAI_COMPATIBLE,
    "clarifai": ProviderType.OPENAI_COMPATIBLE,
    "cloudflare-workers-ai": ProviderType.OPENAI_COMPATIBLE,
    "deepinfra": ProviderType.OPENAI_COMPATIBLE,
    "novita": ProviderType.OPENAI_COMPATIBLE,
    "modelz": ProviderType.OPENAI_COMPATIBLE,
    "nebius": ProviderType.OPENAI_COMPATIBLE,
    "onerouter": ProviderType.OPENAI_COMPATIBLE,
    "akash": ProviderType.OPENAI_COMPATIBLE,
    "ai-sdk": ProviderType.OPENAI_COMPATIBLE,
    # Providers requiring full normalization
    "google-vertex": ProviderType.GOOGLE,
    "anthropic": ProviderType.ANTHROPIC,
    # Providers requiring reasoning field normalization
    "fireworks": ProviderType.DEEPSEEK,  # Can serve DeepSeek models
    "alibaba-cloud": ProviderType.QWEN,   # Can serve Qwen models
}

# All possible reasoning field names that need normalization to "reasoning_content"
REASONING_FIELD_NAMES = frozenset({
    "reasoning",
    "reasoning_content",
    "thinking",
    "analysis",
    "inner_thought",
    "thoughts",
    "thought",
    "chain_of_thought",
    "cot",
})


@dataclass
class NormalizedChunk:
    """Represents a normalized streaming chunk"""
    id: str
    object: str = "chat.completion.chunk"
    created: int = field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": self.choices,
        }
        if self.usage:
            result["usage"] = self.usage
        return result

    def to_sse(self) -> str:
        """Convert to SSE format string"""
        return f"data: {json.dumps(self.to_dict())}\n\n"


@dataclass
class NormalizedDelta:
    """Represents the delta content in a normalized chunk"""
    role: str | None = None
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict] | None = None
    function_call: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        result = {}
        if self.role is not None:
            result["role"] = self.role
        if self.content is not None:
            result["content"] = self.content
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        if self.tool_calls is not None:
            result["tool_calls"] = self.tool_calls
        if self.function_call is not None:
            result["function_call"] = self.function_call
        return result


@dataclass
class NormalizedChoice:
    """Represents a choice in a normalized chunk"""
    index: int = 0
    delta: NormalizedDelta = field(default_factory=NormalizedDelta)
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "index": self.index,
            "delta": self.delta.to_dict(),
            "finish_reason": self.finish_reason,
        }


class StreamNormalizer:
    """
    Normalizes streaming responses from various AI providers to OpenAI Chat Completions format.

    Usage:
        normalizer = StreamNormalizer(provider="fireworks", model="deepseek-v3")
        for chunk in normalizer.normalize(provider_stream):
            yield chunk.to_sse()
    """

    def __init__(self, provider: str, model: str):
        """
        Initialize the stream normalizer.

        Args:
            provider: Provider name (e.g., "openrouter", "fireworks", "google-vertex")
            model: Model name being used
        """
        self.provider = provider
        self.model = model
        self.provider_type = PROVIDER_TYPE_MAP.get(provider, ProviderType.OPENAI_COMPATIBLE)
        self._chunk_count = 0
        self._accumulated_content = ""
        self._accumulated_reasoning = ""

        # Detect if model is a reasoning/thinking model
        self._is_reasoning_model = self._detect_reasoning_model(model)

        logger.debug(
            f"StreamNormalizer initialized: provider={provider}, model={model}, "
            f"type={self.provider_type.value}, is_reasoning={self._is_reasoning_model}"
        )

    def _detect_reasoning_model(self, model: str) -> bool:
        """Detect if the model is a reasoning/thinking model based on its name"""
        model_lower = model.lower()
        reasoning_indicators = [
            "thinking",
            "reasoning",
            "deepseek-r1",
            "deepseek-v3",
            "qwen3",
            "o1",
            "o3",
        ]
        return any(indicator in model_lower for indicator in reasoning_indicators)

    def normalize(self, stream: Iterator) -> Iterator[NormalizedChunk]:
        """
        Normalize a stream of provider-specific chunks to standardized format.

        Args:
            stream: Iterator of provider-specific chunk objects

        Yields:
            NormalizedChunk objects in standardized format
        """
        try:
            for raw_chunk in stream:
                self._chunk_count += 1
                normalized = self._normalize_chunk(raw_chunk)
                if normalized:
                    yield normalized
        except Exception as e:
            logger.error(f"Stream normalization error: {e}", exc_info=True)
            yield self._create_error_chunk(str(e))

    def normalize_chunk(self, chunk: Any) -> NormalizedChunk | None:
        """
        Normalize a single chunk based on provider type.

        This method increments the chunk counter and accumulates content.
        Use this when processing chunks directly instead of using normalize().

        Args:
            chunk: Raw chunk from provider

        Returns:
            NormalizedChunk or None if chunk should be skipped
        """
        self._chunk_count += 1

        if self.provider_type == ProviderType.GOOGLE:
            return self._normalize_google_chunk(chunk)
        elif self.provider_type == ProviderType.ANTHROPIC:
            return self._normalize_anthropic_chunk(chunk)
        else:
            # OpenAI-compatible providers (with potential reasoning normalization)
            return self._normalize_openai_chunk(chunk)

    def _normalize_chunk(self, chunk: Any) -> NormalizedChunk | None:
        """
        Internal method to normalize a single chunk based on provider type.
        Does NOT increment chunk count - use normalize_chunk() for that.

        Args:
            chunk: Raw chunk from provider

        Returns:
            NormalizedChunk or None if chunk should be skipped
        """
        if self.provider_type == ProviderType.GOOGLE:
            return self._normalize_google_chunk(chunk)
        elif self.provider_type == ProviderType.ANTHROPIC:
            return self._normalize_anthropic_chunk(chunk)
        else:
            # OpenAI-compatible providers (with potential reasoning normalization)
            return self._normalize_openai_chunk(chunk)

    def _normalize_openai_chunk(self, chunk: Any) -> NormalizedChunk | None:
        """
        Normalize OpenAI-compatible streaming chunk.

        Handles extraction and normalization of reasoning fields from various providers
        that serve reasoning models (DeepSeek, Qwen, etc.)
        """
        try:
            # Build choices list
            choices = []
            for choice in chunk.choices:
                delta = NormalizedDelta()

                # Extract role if present
                if hasattr(choice.delta, "role") and choice.delta.role:
                    delta.role = choice.delta.role

                # Extract content
                if hasattr(choice.delta, "content") and choice.delta.content:
                    delta.content = choice.delta.content
                    self._accumulated_content += choice.delta.content

                # Extract and normalize reasoning content from various field names
                reasoning = self._extract_reasoning_from_delta(choice.delta)
                if reasoning:
                    delta.reasoning_content = reasoning
                    self._accumulated_reasoning += reasoning

                # Extract tool calls if present
                if hasattr(choice.delta, "tool_calls") and choice.delta.tool_calls:
                    delta.tool_calls = self._serialize_tool_calls(choice.delta.tool_calls)

                # Extract function call if present (legacy format)
                if hasattr(choice.delta, "function_call") and choice.delta.function_call:
                    delta.function_call = self._serialize_function_call(choice.delta.function_call)

                # Normalize finish reason
                finish_reason = self._normalize_finish_reason(choice.finish_reason)

                choices.append(NormalizedChoice(
                    index=choice.index,
                    delta=delta,
                    finish_reason=finish_reason,
                ).to_dict())

            # Extract usage if present (usually in final chunk)
            usage = None
            if hasattr(chunk, "usage") and chunk.usage:
                usage = {
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                }

            return NormalizedChunk(
                id=chunk.id,
                object=getattr(chunk, "object", "chat.completion.chunk"),
                created=getattr(chunk, "created", int(time.time())),
                model=getattr(chunk, "model", self.model),
                choices=choices,
                usage=usage,
            )

        except Exception as e:
            logger.error(f"Error normalizing OpenAI chunk: {e}", exc_info=True)
            return self._create_error_chunk(f"Chunk normalization error: {e}")

    def _extract_reasoning_from_delta(self, delta: Any) -> str | None:
        """
        Extract reasoning content from delta, checking all known field names.

        Different providers use different field names for reasoning/thinking content:
        - DeepSeek: reasoning, reasoning_content
        - Qwen: thinking, thoughts
        - Claude (via OpenRouter): thinking
        - Others: analysis, inner_thought, chain_of_thought

        Returns:
            Reasoning content string or None
        """
        for field_name in REASONING_FIELD_NAMES:
            if hasattr(delta, field_name):
                value = getattr(delta, field_name)
                if value:
                    logger.debug(f"Extracted reasoning from field '{field_name}': {len(str(value))} chars")
                    return str(value)

        # Also check for dict-style access if delta is dict-like
        if hasattr(delta, "get") or isinstance(delta, dict):
            delta_dict = delta if isinstance(delta, dict) else {}
            for field_name in REASONING_FIELD_NAMES:
                value = delta_dict.get(field_name)
                if value:
                    logger.debug(f"Extracted reasoning from dict field '{field_name}': {len(str(value))} chars")
                    return str(value)

        return None

    def _normalize_google_chunk(self, chunk: Any) -> NormalizedChunk | None:
        """
        Normalize Google Vertex AI streaming chunk.

        Google's format uses candidates[].content.parts[] structure.
        Note: Current implementation receives already-converted SSE strings,
        so this method handles both raw protobuf and pre-converted formats.
        """
        try:
            # If chunk is already a string (SSE format from our wrapper), parse it
            if isinstance(chunk, str):
                if chunk.startswith("data: "):
                    data_str = chunk[6:].strip()
                    if data_str == "[DONE]":
                        return None
                    chunk_data = json.loads(data_str)
                    return self._normalize_from_dict(chunk_data)
                return None

            # Handle raw Vertex AI protobuf response
            if hasattr(chunk, "candidates"):
                choices = []
                for i, candidate in enumerate(chunk.candidates):
                    content = ""
                    if hasattr(candidate, "content") and candidate.content:
                        for part in getattr(candidate.content, "parts", []):
                            if hasattr(part, "text"):
                                content += part.text

                    delta = NormalizedDelta(content=content if content else None)
                    finish_reason = self._map_google_finish_reason(
                        getattr(candidate, "finish_reason", None)
                    )

                    choices.append(NormalizedChoice(
                        index=i,
                        delta=delta,
                        finish_reason=finish_reason,
                    ).to_dict())

                # Extract usage if available
                usage = None
                if hasattr(chunk, "usage_metadata"):
                    usage = {
                        "prompt_tokens": getattr(chunk.usage_metadata, "prompt_token_count", 0),
                        "completion_tokens": getattr(chunk.usage_metadata, "candidates_token_count", 0),
                        "total_tokens": (
                            getattr(chunk.usage_metadata, "prompt_token_count", 0) +
                            getattr(chunk.usage_metadata, "candidates_token_count", 0)
                        ),
                    }

                return NormalizedChunk(
                    id=f"vertex-{int(time.time() * 1000)}",
                    object="chat.completion.chunk",
                    created=int(time.time()),
                    model=self.model,
                    choices=choices,
                    usage=usage,
                )

            logger.warning(f"Unknown Google chunk format: {type(chunk)}")
            return None

        except Exception as e:
            logger.error(f"Error normalizing Google chunk: {e}", exc_info=True)
            return self._create_error_chunk(f"Google chunk normalization error: {e}")

    def _normalize_anthropic_chunk(self, chunk: Any) -> NormalizedChunk | None:
        """
        Normalize Anthropic streaming events.

        Anthropic uses event-based streaming with content_block_delta events.
        This handles both direct Anthropic API responses and OpenRouter-proxied responses.
        """
        try:
            # If this is a dict (parsed event), handle it
            if isinstance(chunk, dict):
                event_type = chunk.get("type")

                if event_type == "content_block_delta":
                    delta_data = chunk.get("delta", {})
                    delta_type = delta_data.get("type")

                    delta = NormalizedDelta()
                    if delta_type == "text_delta":
                        delta.content = delta_data.get("text")
                    elif delta_type == "thinking_delta":
                        delta.reasoning_content = delta_data.get("thinking")

                    return NormalizedChunk(
                        id=chunk.get("message", {}).get("id", f"anthropic-{int(time.time() * 1000)}"),
                        model=chunk.get("message", {}).get("model", self.model),
                        choices=[NormalizedChoice(delta=delta).to_dict()],
                    )

                elif event_type == "message_delta":
                    # Contains stop_reason
                    stop_reason = chunk.get("delta", {}).get("stop_reason")
                    finish_reason = self._map_anthropic_stop_reason(stop_reason)

                    return NormalizedChunk(
                        id=chunk.get("message", {}).get("id", f"anthropic-{int(time.time() * 1000)}"),
                        model=chunk.get("message", {}).get("model", self.model),
                        choices=[NormalizedChoice(finish_reason=finish_reason).to_dict()],
                    )

                elif event_type == "message_stop":
                    return None  # End of stream, handled elsewhere

            # If chunk has OpenAI-like structure (proxied via OpenRouter), use that normalizer
            if hasattr(chunk, "choices"):
                return self._normalize_openai_chunk(chunk)

            return None

        except Exception as e:
            logger.error(f"Error normalizing Anthropic chunk: {e}", exc_info=True)
            return self._create_error_chunk(f"Anthropic chunk normalization error: {e}")

    def _normalize_from_dict(self, chunk_data: dict) -> NormalizedChunk | None:
        """Normalize a chunk from dictionary format (for pre-parsed chunks)"""
        try:
            choices = []
            for choice_data in chunk_data.get("choices", []):
                delta_data = choice_data.get("delta", {})

                delta = NormalizedDelta(
                    role=delta_data.get("role"),
                    content=delta_data.get("content"),
                )

                # Extract reasoning from various fields
                for field_name in REASONING_FIELD_NAMES:
                    if field_name in delta_data and delta_data[field_name]:
                        delta.reasoning_content = delta_data[field_name]
                        break

                choices.append(NormalizedChoice(
                    index=choice_data.get("index", 0),
                    delta=delta,
                    finish_reason=self._normalize_finish_reason(choice_data.get("finish_reason")),
                ).to_dict())

            return NormalizedChunk(
                id=chunk_data.get("id", f"chunk-{int(time.time() * 1000)}"),
                object=chunk_data.get("object", "chat.completion.chunk"),
                created=chunk_data.get("created", int(time.time())),
                model=chunk_data.get("model", self.model),
                choices=choices,
                usage=chunk_data.get("usage"),
            )
        except Exception as e:
            logger.error(f"Error normalizing dict chunk: {e}", exc_info=True)
            return None

    def _normalize_finish_reason(self, reason: str | None) -> str | None:
        """
        Normalize finish reason to standard values: stop, length, error, or None.

        Different providers use different values:
        - OpenAI: stop, length, content_filter, tool_calls, function_call
        - Anthropic: end_turn, max_tokens, stop_sequence
        - Google: STOP, MAX_TOKENS, SAFETY, RECITATION
        """
        if reason is None:
            return None

        reason_lower = str(reason).lower()

        # Map to "stop"
        if reason_lower in ("stop", "end_turn", "stop_sequence", "1", "recitation"):
            return "stop"

        # Map to "length"
        if reason_lower in ("length", "max_tokens", "2"):
            return "length"

        # Map to "error" / content filter
        if reason_lower in ("content_filter", "safety", "3", "error"):
            return "error"

        # Tool calls - keep as-is for function calling support
        if reason_lower in ("tool_calls", "function_call"):
            return reason_lower

        # Unknown - default to stop
        logger.debug(f"Unknown finish_reason '{reason}', defaulting to 'stop'")
        return "stop"

    def _map_google_finish_reason(self, reason: Any) -> str | None:
        """Map Google Vertex AI finish reason to standard format"""
        if reason is None:
            return None

        # Google uses numeric enums
        reason_map = {
            0: None,     # FINISH_REASON_UNSPECIFIED
            1: "stop",   # STOP
            2: "length", # MAX_TOKENS
            3: "error",  # SAFETY
            4: "stop",   # RECITATION
        }

        if isinstance(reason, int):
            return reason_map.get(reason)

        return self._normalize_finish_reason(str(reason))

    def _map_anthropic_stop_reason(self, reason: str | None) -> str | None:
        """Map Anthropic stop reason to standard format"""
        if reason is None:
            return None

        reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }

        return reason_map.get(reason, "stop")

    def _serialize_tool_calls(self, tool_calls: list) -> list[dict]:
        """Serialize tool calls to dictionary format"""
        result = []
        for tc in tool_calls:
            if hasattr(tc, "model_dump"):
                result.append(tc.model_dump())
            elif hasattr(tc, "__dict__"):
                result.append(tc.__dict__)
            elif isinstance(tc, dict):
                result.append(tc)
            else:
                result.append({"raw": str(tc)})
        return result

    def _serialize_function_call(self, function_call: Any) -> dict:
        """Serialize function call to dictionary format"""
        if hasattr(function_call, "model_dump"):
            return function_call.model_dump()
        elif hasattr(function_call, "__dict__"):
            return function_call.__dict__
        elif isinstance(function_call, dict):
            return function_call
        return {"raw": str(function_call)}

    def _create_error_chunk(self, error_message: str) -> NormalizedChunk:
        """Create a standardized error chunk"""
        return NormalizedChunk(
            id=f"error-{int(time.time() * 1000)}",
            model=self.model,
            choices=[{
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            }],
        )

    def get_accumulated_content(self) -> str:
        """Get all accumulated content from the stream"""
        return self._accumulated_content

    def get_accumulated_reasoning(self) -> str:
        """Get all accumulated reasoning content from the stream"""
        return self._accumulated_reasoning

    def get_chunk_count(self) -> int:
        """Get the number of chunks processed"""
        return self._chunk_count


def create_normalizer(provider: str, model: str) -> StreamNormalizer:
    """
    Factory function to create a StreamNormalizer for the given provider and model.

    Args:
        provider: Provider name (e.g., "openrouter", "fireworks")
        model: Model name being used

    Returns:
        Configured StreamNormalizer instance
    """
    return StreamNormalizer(provider=provider, model=model)


def normalize_stream(
    stream: Iterator,
    provider: str,
    model: str,
) -> Iterator[NormalizedChunk]:
    """
    Convenience function to normalize a stream.

    Args:
        stream: Raw provider stream
        provider: Provider name
        model: Model name

    Yields:
        Normalized chunks
    """
    normalizer = StreamNormalizer(provider=provider, model=model)
    yield from normalizer.normalize(stream)


def create_error_sse_chunk(
    error_message: str,
    error_type: str = "stream_error",
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """
    Create a standardized error chunk in SSE format.

    Args:
        error_message: Human-readable error message
        error_type: Error type identifier
        provider: Optional provider name for context
        model: Optional model name for context

    Returns:
        SSE-formatted error chunk string
    """
    error_chunk = {
        "error": {
            "message": error_message,
            "type": error_type,
        }
    }
    if provider:
        error_chunk["error"]["provider"] = provider
    if model:
        error_chunk["error"]["model"] = model

    return f"data: {json.dumps(error_chunk)}\n\n"


def create_done_sse() -> str:
    """Create the SSE [DONE] signal"""
    return "data: [DONE]\n\n"
