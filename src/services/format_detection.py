"""
Format detection for unified chat endpoint.
Automatically detects OpenAI, Anthropic, or Responses API format.
"""

from enum import Enum
from typing import Any


class ChatFormat(str, Enum):
    """Supported chat API formats"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    RESPONSES = "responses"
    AUTO = "auto"


def detect_request_format(data: dict[str, Any]) -> ChatFormat:
    """
    Auto-detect the request format based on field presence and structure.

    Detection Algorithm:
    1. Explicit format parameter takes priority
    2. Check for 'input' field → Responses API
    3. Check for Anthropic-specific fields → Anthropic
    4. Default to OpenAI (most common)

    Args:
        data: Raw request body as dict

    Returns:
        ChatFormat enum indicating detected format

    Examples:
        >>> detect_request_format({"messages": [...]})
        ChatFormat.OPENAI

        >>> detect_request_format({"input": [...], "response_format": {...}})
        ChatFormat.RESPONSES

        >>> detect_request_format({"system": "...", "messages": [...]})
        ChatFormat.ANTHROPIC
    """

    # Priority 1: Explicit format parameter
    if "format" in data:
        try:
            return ChatFormat(data["format"].lower())
        except ValueError:
            pass  # Invalid format, continue with auto-detection

    # Priority 2: Responses API (has 'input' field instead of 'messages')
    if "input" in data:
        return ChatFormat.RESPONSES

    # Priority 3: Anthropic (has 'system' as string, not in messages)
    if "system" in data and isinstance(data.get("system"), str):
        return ChatFormat.ANTHROPIC

    # Priority 4: Check for Anthropic-specific fields
    anthropic_indicators = [
        "max_tokens_to_sample",  # Old Anthropic API
        "stop_sequences",        # Anthropic-specific
    ]

    if any(indicator in data for indicator in anthropic_indicators):
        return ChatFormat.ANTHROPIC

    # Default: OpenAI (most common format)
    return ChatFormat.OPENAI


def validate_format_compatibility(data: dict[str, Any], format: ChatFormat) -> bool:
    """
    Validate that request data is compatible with the detected format.

    Args:
        data: Request data
        format: Detected format

    Returns:
        True if valid, raises ValueError if invalid

    Raises:
        ValueError: If data is incompatible with format
    """

    if format == ChatFormat.OPENAI:
        if "messages" not in data:
            raise ValueError("OpenAI format requires 'messages' field")

    elif format == ChatFormat.ANTHROPIC:
        if "messages" not in data:
            raise ValueError("Anthropic format requires 'messages' field")

    elif format == ChatFormat.RESPONSES:
        if "input" not in data:
            raise ValueError("Responses API format requires 'input' field")

    return True
