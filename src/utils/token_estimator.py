"""Utility helpers for estimating token counts from message payloads."""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable


def _extract_text_length_from_content(content: Any) -> int:
    """Return a rough character count for a message content payload."""
    if isinstance(content, str):
        return len(content)

    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    total += len(item.get("text", ""))
                elif "text" in item:
                    total += len(str(item.get("text", "")))
            elif isinstance(item, str):
                total += len(item)
        return total

    if content is None:
        return 0

    return len(str(content))


def estimate_message_tokens(
    messages: Iterable[dict] | None,
    max_tokens: int | None = None,
    *,
    fallback_tokens: int = 256,
) -> int:
    """Estimate a reasonable token count for plan/rate-limit prechecks.

    Args:
        messages: Iterable of OpenAI-format message dicts.
        max_tokens: Optional explicit max token request from the client.
        fallback_tokens: Minimum number of tokens to assume when no signal exists.

    Returns:
        Integer token estimate (always >= 1).
    """
    if max_tokens is not None and max_tokens > 0:
        return max_tokens

    total_chars = 0
    if messages:
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            total_chars += _extract_text_length_from_content(content)

    estimated_tokens = total_chars // 4  # Rough heuristic: 4 chars ≈ 1 token
    if estimated_tokens <= 0:
        estimated_tokens = fallback_tokens

    return max(1, estimated_tokens)
