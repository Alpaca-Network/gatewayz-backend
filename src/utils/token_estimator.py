"""Utility helpers for estimating token counts from message payloads.

Provides two estimation strategies:

1. **tiktoken** (preferred): Uses OpenAI's tokenizer for accurate counts.
   Install with ``pip install tiktoken``. When available, token counts
   closely match what most LLM providers report.

2. **Word-based heuristic** (fallback): Splits on whitespace and applies
   a ~0.75 tokens-per-word ratio, which empirically outperforms the
   naive "4 chars = 1 token" rule across English and code.

Both strategies handle multimodal (list-of-parts) content gracefully,
extracting only the text segments.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# tiktoken lazy-loading
# ---------------------------------------------------------------------------

_tiktoken_encoding = None
_tiktoken_available: bool | None = None  # None = not yet checked


def _get_tiktoken_encoding():
    """Return a cached tiktoken encoding, or *None* if tiktoken is unavailable.

    Uses ``cl100k_base`` which covers GPT-4, GPT-3.5-turbo, and most
    embedding models. It is a reasonable default for cross-provider
    estimation because it approximates the BPE split that the majority
    of hosted models use.
    """
    global _tiktoken_encoding, _tiktoken_available

    if _tiktoken_available is False:
        return None

    if _tiktoken_encoding is not None:
        return _tiktoken_encoding

    try:
        import tiktoken  # noqa: F401

        _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
        _tiktoken_available = True
        logger.info("tiktoken loaded successfully - using cl100k_base for token estimation")
        return _tiktoken_encoding
    except ImportError:
        _tiktoken_available = False
        logger.info(
            "tiktoken not installed - falling back to word-based token estimation. "
            "Install tiktoken for more accurate billing: pip install tiktoken"
        )
        return None
    except Exception as e:
        _tiktoken_available = False
        logger.warning(f"tiktoken failed to initialize: {e} - using word-based fallback")
        return None


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

    Uses tiktoken when available, otherwise falls back to a word-based
    heuristic (0.75 tokens per whitespace-delimited word).

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

    # Fallback: word-based heuristic
    # ~0.75 tokens per word is empirically more accurate than 4 chars / token.
    # Rationale: average English word is ~4.7 chars, and average token is ~3.5
    # chars for BPE tokenizers, giving a ratio of about 0.74 tokens per word.
    # For code, the ratio is somewhat higher (~1.0) due to punctuation, but
    # 0.75 is a good middle ground across mixed workloads.
    word_count = len(text.split())
    return max(0, int(word_count * 0.75))


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
    return "tiktoken" if enc is not None else "word_heuristic"


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
