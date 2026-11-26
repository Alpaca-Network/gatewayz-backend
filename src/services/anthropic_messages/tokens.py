"""Input-token estimate for ``POST /v1/messages/count_tokens``."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class AnthropicCountTokensRequest(BaseModel):
    """Body of ``POST /v1/messages/count_tokens``. Same shape as a message request,
    without ``max_tokens``."""

    model: str = Field(..., description="Model identifier")
    messages: list[dict[str, Any]] = Field(..., description="Conversation turns")
    system: str | list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None

    class Config:
        extra = "allow"


def _count_block_tokens(value: Any) -> int:
    """Tokens in one piece of Anthropic content: text, or any JSON-shaped block."""
    from src.utils.token_estimator import count_tokens_text

    if value is None:
        return 0
    if isinstance(value, str):
        return count_tokens_text(value)
    if isinstance(value, list):
        return sum(_count_block_tokens(item) for item in value)
    if isinstance(value, dict):
        block_type = value.get("type")
        if block_type == "text":
            return count_tokens_text(value.get("text", ""))
        if block_type in ("image", "document"):
            # Binary payloads are not text. A flat allowance keeps a base64 blob
            # from being counted as hundreds of thousands of tokens.
            return 1500
        if block_type == "tool_result":
            return 4 + _count_block_tokens(value.get("content"))
        if block_type in ("thinking", "redacted_thinking"):
            return count_tokens_text(value.get("thinking", ""))
        return count_tokens_text(json.dumps(value, separators=(",", ":")))
    return count_tokens_text(str(value))


def estimate_input_tokens(body: AnthropicCountTokensRequest) -> int:
    """Estimated input tokens for a message request.

    An estimate from the gateway's tokenizer, not the provider's count: close
    enough for a client's context meter, and it costs no upstream call.
    """
    total = _count_block_tokens(body.system)
    for message in body.messages:
        total += 4 + _count_block_tokens(message.get("content"))
    for tool in body.tools or []:
        total += _count_block_tokens(tool)
    return max(total, 1)
