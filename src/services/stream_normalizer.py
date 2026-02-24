import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class NormalizedChunk:
    def __init__(self, id: str, object: str, created: int, model: str, choices: list[dict]):
        self.id = id
        self.object = object
        self.created = created
        self.model = model
        self.choices = choices

    def to_sse(self) -> str:
        data = {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": self.choices,
        }
        return f"data: {json.dumps(data)}\n\n"


class StreamNormalizer:
    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self.accumulated_content = ""
        self.accumulated_reasoning = ""
        self._id = f"chatcmpl-{int(time.time())}"
        self._created = int(time.time())

    def normalize_chunk(self, chunk: Any) -> NormalizedChunk | None:
        chunk_data = self._to_dict(chunk)

        if not chunk_data:
            return None

        # 1. Extract basic fields
        chunk_id = chunk_data.get("id", self._id)
        created = chunk_data.get("created", self._created)
        model = chunk_data.get("model", self.model)

        choices = []

        # Handle OpenAI format (choices list)
        if "choices" in chunk_data and isinstance(chunk_data["choices"], list):
            for choice in chunk_data["choices"]:
                normalized_choice = self._normalize_choice(choice)
                if normalized_choice:
                    choices.append(normalized_choice)

        # Handle Gemini format (candidates list)
        elif "candidates" in chunk_data and isinstance(chunk_data["candidates"], list):
            for i, candidate in enumerate(chunk_data["candidates"]):
                normalized_choice = self._normalize_gemini_candidate(candidate, i)
                if normalized_choice:
                    choices.append(normalized_choice)

        # Handle Anthropic format (top level content/delta)
        # Note: This is a simplified handler; actual Anthropic response structures might need more specific checks
        # depending on the client library used.
        elif "type" in chunk_data:
            normalized_choice = self._normalize_anthropic_event(chunk_data)
            if normalized_choice:
                choices.append(normalized_choice)

        # If we couldn't find choices/candidates but there is 'output' (Fireworks, etc. sometimes use this)
        elif "output" in chunk_data and isinstance(chunk_data["output"], list):
            # Similar to choices
            for choice in chunk_data["output"]:
                normalized_choice = self._normalize_choice(choice)
                if normalized_choice:
                    choices.append(normalized_choice)

        if not choices:
            # Fallback: check if it looks like a single choice flat structure
            # (Some custom providers might do this)
            if "content" in chunk_data or "delta" in chunk_data:
                normalized_choice = self._normalize_choice(
                    chunk_data
                )  # Treat the whole chunk as a choice-like dict
                if normalized_choice:
                    # It needs an index
                    if "index" not in normalized_choice:
                        normalized_choice["index"] = 0
                    choices.append(normalized_choice)

        if not choices:
            return None

        return NormalizedChunk(
            id=chunk_id,
            object="chat.completion.chunk",
            created=created,
            model=model,
            choices=choices,
        )

    def _to_dict(self, obj: Any) -> dict:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        # Last resort: try converting to string and parsing if it's JSON? No, that's unsafe.
        return {}

    def _normalize_choice(self, choice: Any) -> dict | None:
        choice = self._to_dict(choice)
        index = choice.get("index", 0)
        finish_reason = self._normalize_finish_reason(choice.get("finish_reason"))

        # 'delta' might be inside, or 'text' might be at top level
        delta = choice.get("delta", {})
        # If delta is None (sometimes happens), make it dict
        if delta is None:
            delta = {}

        # Ensure delta is dict
        delta = self._to_dict(delta)

        normalized_delta = {}

        # Role
        if "role" in delta:
            normalized_delta["role"] = delta["role"]

        # Content
        content = delta.get("content")
        # specific fallback for Fireworks/DeepSeek using 'text' sometimes
        if not content and "text" in choice:
            content = choice["text"]

        if content:
            normalized_delta["content"] = content
            self.accumulated_content += content

        # Reasoning extraction
        reasoning = self._extract_reasoning(delta)
        # Also check top level choice for reasoning
        if not reasoning:
            reasoning = self._extract_reasoning(choice)

        if reasoning:
            normalized_delta["reasoning_content"] = reasoning
            self.accumulated_reasoning += reasoning

        return {"index": index, "delta": normalized_delta, "finish_reason": finish_reason}

    def _extract_reasoning(self, data: dict) -> str | None:
        fields = [
            "reasoning",
            "reasoning_content",
            "thinking",
            "analysis",
            "inner_thought",
            "thoughts",
            "thought",
            "chain_of_thought",
            "cot",
        ]
        for field in fields:
            if field in data and data[field]:
                return data[field]

        # Handle Grok 4.1's reasoning_details array structure
        # reasoning_details is an array of reasoning steps used by x-ai models
        if "reasoning_details" in data and data["reasoning_details"]:
            reasoning_details = data["reasoning_details"]
            if isinstance(reasoning_details, list):
                # Extract text content from reasoning details array
                reasoning_texts = []
                for detail in reasoning_details:
                    if isinstance(detail, dict):
                        # Handle various possible structures in reasoning_details
                        text = detail.get("content") or detail.get("text") or detail.get("thinking")
                        if text:
                            reasoning_texts.append(text)
                    elif isinstance(detail, str):
                        reasoning_texts.append(detail)
                if reasoning_texts:
                    return "\n".join(reasoning_texts)
            elif isinstance(reasoning_details, str):
                return reasoning_details

        return None

    def _normalize_finish_reason(self, reason: Any) -> str | None:
        if not reason:
            return None
        reason = str(reason).lower()
        if reason in ["stop", "end_turn", "stop_sequence"]:
            return "stop"
        if reason in ["length", "max_tokens"]:
            return "length"
        if reason in ["content_filter", "safety", "error"]:
            return "error"
        if reason in ["tool_calls", "function_call"]:
            return reason
        return "stop"

    def _normalize_gemini_candidate(self, candidate: Any, index: int) -> dict:
        candidate = self._to_dict(candidate)
        content = ""
        # Gemini structure: content -> parts -> text
        if "content" in candidate and isinstance(candidate["content"], dict):
            parts = candidate["content"].get("parts", [])
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        content += part["text"]

        finish_reason = self._normalize_finish_reason(candidate.get("finishReason"))

        delta = {}
        if content:
            delta["content"] = content
            self.accumulated_content += content

        return {"index": index, "delta": delta, "finish_reason": finish_reason}

    def _normalize_anthropic_event(self, chunk: dict) -> dict | None:
        """Normalize Anthropic streaming events to OpenAI format.

        Handles all Anthropic streaming event types:
        - message_start: Initial message metadata
        - content_block_start: Start of a content block (text, thinking, tool_use)
        - content_block_delta: Content update (text_delta, thinking_delta, input_json_delta)
        - content_block_stop: End of a content block
        - message_delta: Message completion with stop_reason and usage
        - message_stop: Final message event
        - ping: Keep-alive event

        Reference: https://docs.anthropic.com/claude/reference/messages-streaming
        """
        event_type = chunk.get("type")
        delta = {}
        finish_reason = None

        if event_type == "content_block_delta":
            inner_delta = chunk.get("delta", {})
            delta_type = inner_delta.get("type", "")

            # Handle text content delta
            # Use explicit type checking first, fallback to key presence only when type is missing
            if delta_type == "text_delta" or (not delta_type and "text" in inner_delta):
                text = inner_delta.get("text", "")
                if text:
                    delta["content"] = text
                    self.accumulated_content += text

            # Handle thinking/reasoning delta (extended thinking feature)
            # Use explicit type checking first, fallback to key presence only when type is missing
            elif delta_type == "thinking_delta" or (not delta_type and "thinking" in inner_delta):
                thinking = inner_delta.get("thinking", "")
                if thinking:
                    delta["reasoning_content"] = thinking
                    self.accumulated_reasoning += thinking

            # Handle signature delta (extended thinking verification)
            elif delta_type == "signature_delta":
                # Signature is used for verifying thinking content authenticity
                # We can include it as metadata but don't accumulate it
                signature = inner_delta.get("signature", "")
                if signature:
                    delta["signature"] = signature

            # Handle tool input delta (for tool use streaming)
            elif delta_type == "input_json_delta":
                partial_json = inner_delta.get("partial_json", "")
                if partial_json:
                    delta["tool_input_delta"] = partial_json

        elif event_type == "message_delta":
            inner_delta = chunk.get("delta", {})
            if "stop_reason" in inner_delta:
                finish_reason = self._normalize_finish_reason(inner_delta["stop_reason"])
            # Usage info is in chunk.usage, not delta - handled elsewhere

        elif event_type == "content_block_start":
            # Content block starting - we can extract type info for context
            content_block = chunk.get("content_block", {})
            block_type = content_block.get("type", "")

            # For thinking blocks, extract initial thinking content if present
            if block_type == "thinking":
                thinking = content_block.get("thinking", "")
                if thinking:
                    delta["reasoning_content"] = thinking
                    self.accumulated_reasoning += thinking

            # For text blocks, extract initial text if present
            elif block_type == "text":
                text = content_block.get("text", "")
                if text:
                    delta["content"] = text
                    self.accumulated_content += text

        elif event_type == "message_stop":
            # Message completed - this is the final event
            finish_reason = "stop"

        # Events we acknowledge but don't produce output for:
        # - message_start: Contains initial message structure (id, type, role, model)
        # - content_block_stop: End of content block marker
        # - ping: Keep-alive signal

        if not delta and not finish_reason:
            return None

        return {"index": 0, "delta": delta, "finish_reason": finish_reason}

    def get_accumulated_content(self) -> str:
        return self.accumulated_content

    def get_accumulated_reasoning(self) -> str:
        return self.accumulated_reasoning


def create_error_sse_chunk(
    error_message: str, error_type: str, provider: str = None, model: str = None
) -> str:
    error_data = {
        "error": {
            "message": error_message,
            "type": error_type,
            "provider": provider,
            "model": model,
        }
    }
    return f"data: {json.dumps(error_data)}\n\n"


def create_done_sse() -> str:
    return "data: [DONE]\n\n"
