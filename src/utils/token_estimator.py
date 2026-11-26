"""Utility helpers for estimating token counts from message payloads.

Provides two estimation strategies:

1. **tiktoken** (preferred): Uses OpenAI's tokenizer for accurate counts.
   Install with ``pip install tiktoken``. When available, token counts
   closely match what most LLM providers report.

2. **Character-based heuristic** (fallback): Uses ~1 token per 4
   characters, a simple and spec-compliant approximation.

Both strategies handle multimodal (list-of-parts) content gracefully,
extracting only the text segments.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# tiktoken lazy-loading
# ---------------------------------------------------------------------------

_TIKTOKEN_NOT_CHECKED = object()  # Sentinel: init not yet attempted
_tiktoken_encoding = _TIKTOKEN_NOT_CHECKED


def _get_tiktoken_encoding():
    """Return a cached tiktoken encoding, or *None* if tiktoken is unavailable.

    Uses ``cl100k_base`` which covers GPT-4, GPT-3.5-turbo, and most
    embedding models. It is a reasonable default for cross-provider
    estimation because it approximates the BPE split that the majority
    of hosted models use.
    """
    global _tiktoken_encoding

    if _tiktoken_encoding is _TIKTOKEN_NOT_CHECKED:
        try:
            import tiktoken  # noqa: F401

            _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
            logger.info("tiktoken loaded successfully - using cl100k_base for token estimation")
        except ImportError:
            _tiktoken_encoding = None
            logger.info(
                "tiktoken not installed - falling back to word-based token estimation. "
                "Install tiktoken for more accurate billing: pip install tiktoken"
            )
        except Exception as e:
            _tiktoken_encoding = None
            logger.warning(f"tiktoken failed to initialize: {e} - using word-based fallback")

    return _tiktoken_encoding


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


def _extract_text_from_content(content: Any) -> str:
    """Extract the raw text from a message ``content`` field.

    Handles plain strings, multimodal ``list[dict]`` payloads (extracting
    only ``type=text`` parts), and ``None``.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)

    if content is None:
        return ""

    return str(content)


def _extract_text_length_from_content(content: Any) -> int:
    """Return a rough character count for a message content payload.

    Kept for backward compatibility with callers that only need the
    length rather than the full text.
    """
    return len(_extract_text_from_content(content))


# ---------------------------------------------------------------------------
# Core counting functions
# ---------------------------------------------------------------------------

# Per-message overhead in tokens (role, delimiters, etc.) used by the
# ChatML / OpenAI chat format.  This is a rough constant that accounts
# for ``<|im_start|>role\n...content...<|im_end|>\n``.
_TOKENS_PER_MESSAGE_OVERHEAD = 4


def count_tokens_text(text: str) -> int:
    """Count tokens in a plain text string.

    Uses tiktoken when available, otherwise falls back to a character-based
    heuristic (~1 token per 4 characters).

    Returns:
        Integer token count (always >= 0).
    """
    if not text:
        return 0

    enc = _get_tiktoken_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            # If encoding fails for any reason, fall through to heuristic
            pass

    # Fallback: character-based heuristic (~1 token per 4 characters).
    return max(1, len(text) // 4)


def count_tokens_messages(messages: Iterable[dict] | None) -> int:
    """Count prompt tokens from an OpenAI-format messages array.

    Accounts for per-message overhead (role, delimiters). Handles both
    string and multimodal content payloads.

    Returns:
        Integer token count (always >= 1 when messages are provided).
    """
    if not messages:
        return 0

    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        text = _extract_text_from_content(content)
        total += count_tokens_text(text) + _TOKENS_PER_MESSAGE_OVERHEAD

        # Role name itself contributes ~1 token
        role = message.get("role", "")
        if role:
            total += 1

        # Function/tool call name adds tokens
        name = message.get("name", "")
        if name:
            total += count_tokens_text(name) + 1  # +1 for name delimiter

    # Final assistant reply priming tokens
    total += 3

    return max(1, total)


def count_completion_tokens(text: str) -> int:
    """Count completion tokens from accumulated response text.

    Returns:
        Integer token count (always >= 1 for non-empty text).
    """
    if not text:
        return 1  # Minimum 1 token even for empty completions

    tokens = count_tokens_text(text)
    return max(1, tokens)


def get_estimation_method() -> str:
    """Return the name of the active estimation method.

    Returns:
        ``"tiktoken"`` or ``"word_heuristic"``.
    """
    enc = _get_tiktoken_encoding()
    return "tiktoken" if enc is not None else "char_heuristic"


# ---------------------------------------------------------------------------
# Whole-request input estimate (credit pre-check and usage fallback)
# ---------------------------------------------------------------------------

# A request is charged for every input it sends: message text, tool
# definitions, tool calls and results, images and documents. Binary parts are
# not text, so they get a flat, deliberately high allowance rather than a
# count of their base64 characters.
#
# Images: providers bill roughly (width x height) / 750 tokens, about 1,600
# for a 1-megapixel image, and resize larger ones down to around that size.
IMAGE_PART_TOKENS = 1600
# Documents / files / audio: estimated from the decoded payload size at about
# one token per 3 bytes (PDF text plus page images are billed per page by
# most providers, which lands near this for typical files), never less than
# this floor -- a part whose size is unknown (a URL or file id) gets the floor.
DOCUMENT_PART_MIN_TOKENS = 1600
DOCUMENT_BYTES_PER_TOKEN = 3

_IMAGE_PART_TYPES = frozenset({"image", "image_url", "input_image"})
_DOCUMENT_PART_TYPES = frozenset({"document", "file", "input_file", "input_audio", "audio"})


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _base64_payload(part: dict) -> str | None:
    """The base64 data carried by a document/file/audio part, if any (OpenAI
    ``file.file_data`` / ``input_audio.data``, Anthropic ``source.data``)."""
    candidates = [
        (part.get("source") or {}).get("data") if isinstance(part.get("source"), dict) else None,
        (part.get("file") or {}).get("file_data") if isinstance(part.get("file"), dict) else None,
        (
            (part.get("input_audio") or {}).get("data")
            if isinstance(part.get("input_audio"), dict)
            else None
        ),
        part.get("file_data"),
        part.get("data"),
    ]
    for value in candidates:
        if isinstance(value, str) and value:
            # A data URL carries its payload after the comma.
            return value.split(",", 1)[1] if value.startswith("data:") else value
    return None


def _document_tokens(part: dict) -> int:
    source = part.get("source")
    if isinstance(source, dict) and source.get("type") == "text":
        # A plain-text document is counted as text.
        return max(DOCUMENT_PART_MIN_TOKENS, count_tokens_text(str(source.get("data") or "")))
    payload = _base64_payload(part)
    if not payload:
        return DOCUMENT_PART_MIN_TOKENS
    decoded_bytes = (len(payload) * 3) // 4
    return max(DOCUMENT_PART_MIN_TOKENS, decoded_bytes // DOCUMENT_BYTES_PER_TOKEN)


def _content_tokens(content: Any) -> int:
    """Tokens in one message's ``content`` (or a block's nested content):
    text, every text-bearing block, and the flat allowances for binary parts."""
    if content is None:
        return 0
    if isinstance(content, str):
        return count_tokens_text(content)
    if isinstance(content, list):
        return sum(_content_tokens(item) for item in content)
    if not isinstance(content, dict):
        return count_tokens_text(str(content))

    part_type = content.get("type")
    if part_type in _IMAGE_PART_TYPES:
        return IMAGE_PART_TOKENS
    if part_type in _DOCUMENT_PART_TYPES:
        return _document_tokens(content)
    if part_type == "text":
        return count_tokens_text(str(content.get("text") or ""))
    if part_type == "tool_result":
        return 4 + _content_tokens(content.get("content"))
    if part_type == "tool_use":
        return (
            4
            + count_tokens_text(str(content.get("name") or ""))
            + count_tokens_text(_json_text(content.get("input")))
        )
    if part_type in ("thinking", "redacted_thinking"):
        return count_tokens_text(str(content.get("thinking") or content.get("data") or ""))
    # Any other block: every field it carries is input.
    return count_tokens_text(_json_text(content))


def _tool_calls_tokens(tool_calls: Any) -> int:
    """Assistant ``tool_calls`` (OpenAI shape): names and arguments."""
    if not isinstance(tool_calls, list):
        return 0
    total = 0
    for call in tool_calls:
        if not isinstance(call, dict):
            total += count_tokens_text(_json_text(call))
            continue
        fn = call.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        total += 4
        total += count_tokens_text(str(fn.get("name") or ""))
        total += count_tokens_text(_json_text(fn.get("arguments")))
    return total


def estimate_request_input_tokens(
    messages: Iterable[dict] | None,
    tools: Any = None,
    tool_choice: Any = None,
    system: Any = None,
) -> int:
    """Estimate every input token a request sends.

    Counts message text and every text-bearing part of list content
    (``tool_result`` / ``tool_use`` / ``thinking`` blocks included), assistant
    ``tool_calls`` names and arguments, a flat allowance per image part
    (:data:`IMAGE_PART_TOKENS`) and per document, file or audio part (sized
    from its base64 payload, at least :data:`DOCUMENT_PART_MIN_TOKENS`),
    ``tools`` (their JSON schemas), ``tool_choice``, and ``system`` given as a
    string or a list of blocks (Anthropic shape). Accepts OpenAI- and
    Anthropic-shaped messages. Pure: no I/O.

    Returns:
        Integer token estimate (>= 1 when anything was sent, else 0).
    """
    total = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        total += _TOKENS_PER_MESSAGE_OVERHEAD + _content_tokens(message.get("content"))
        if message.get("role"):
            total += 1
        name = message.get("name")
        if name:
            total += count_tokens_text(str(name)) + 1
        total += _tool_calls_tokens(message.get("tool_calls"))
        function_call = message.get("function_call")
        if function_call:
            total += count_tokens_text(_json_text(function_call))
    if system:
        total += _TOKENS_PER_MESSAGE_OVERHEAD + _content_tokens(system)
    if tools:
        total += count_tokens_text(_json_text(tools))
    if tool_choice is not None and tool_choice != "auto":
        total += count_tokens_text(_json_text(tool_choice))
    if total:
        # Final assistant reply priming tokens, as count_tokens_messages.
        total += 3
    return total


def estimate_output_tokens(text: str | None, tool_calls: Any = None, reasoning: str = "") -> int:
    """Estimate generated tokens from what a response produced: its text,
    any tool-call names and arguments, and any reasoning text."""
    total = count_tokens_text(text or "") + count_tokens_text(reasoning or "")
    if isinstance(tool_calls, str):
        total += count_tokens_text(tool_calls)
    else:
        total += _tool_calls_tokens(tool_calls)
    return total


def request_uses_one_hour_cache(*parts: Any) -> bool:
    """True when any ``cache_control`` marker anywhere in the given request
    parts (messages, tools, system) sets ``ttl`` to ``"1h"``."""
    stack: list[Any] = list(parts)
    seen = 0
    while stack and seen < 100_000:
        seen += 1
        item = stack.pop()
        if isinstance(item, dict):
            marker = item.get("cache_control")
            if isinstance(marker, dict) and str(marker.get("ttl") or "") == "1h":
                return True
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(v for v in item if isinstance(v, (dict, list)))
    return False


# ---------------------------------------------------------------------------
# Legacy API (backward compatible)
# ---------------------------------------------------------------------------


def estimate_message_tokens(
    messages: Iterable[dict] | None,
    max_tokens: int | None = None,
    *,
    fallback_tokens: int = 256,
) -> int:
    """Estimate a reasonable token count for plan/rate-limit prechecks.

    This is the original API kept for backward compatibility. New callers
    should prefer :func:`count_tokens_messages` for accuracy.

    Args:
        messages: Iterable of OpenAI-format message dicts.
        max_tokens: Optional explicit max token request from the client.
        fallback_tokens: Minimum number of tokens to assume when no signal exists.

    Returns:
        Integer token estimate (always >= 1).
    """
    if max_tokens is not None and max_tokens > 0:
        return max_tokens

    if not messages:
        return fallback_tokens

    estimated = count_tokens_messages(messages)
    if estimated <= 0:
        estimated = fallback_tokens

    return max(1, estimated)
