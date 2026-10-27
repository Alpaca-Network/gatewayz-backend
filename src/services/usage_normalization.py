"""Provider usage → the completion-token count we bill.

Providers disagree on whether hidden reasoning is inside ``completion_tokens``:

* OpenAI, OpenRouter, DeepSeek: inside. ``completion_tokens_details.reasoning_tokens``
  is a breakdown of ``completion_tokens``; ``total = prompt + completion``.
* xAI: outside. ``completion_tokens`` is the visible answer only, reasoning is
  reported separately, ``total = prompt + completion + reasoning``, and xAI bills
  reasoning at the completion rate.

Billing ``completion_tokens`` as reported therefore under-bills xAI reasoning
models by every reasoning token. ``billed_completion_tokens`` adds reasoning back
only when the usage block itself shows it is outside the completion count.
(Vertex reports thinking as ``thoughtsTokenCount``; its client folds that in.)
"""

from __future__ import annotations

from typing import Any


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def reported_reasoning_tokens(usage: Any) -> int:
    """``completion_tokens_details.reasoning_tokens``, else top-level ``reasoning_tokens``."""
    details = _get(usage, "completion_tokens_details")
    return _int(_get(details, "reasoning_tokens")) or _int(_get(usage, "reasoning_tokens"))


def reasoning_outside_completion(usage: Any, prompt_tokens: int, completion_tokens: int) -> bool:
    """True when the provider counted reasoning on top of ``completion_tokens``.

    Evidence, either of: reasoning larger than the completion count (it cannot be
    a breakdown of it), or ``total_tokens`` equal to prompt + completion + reasoning.
    Exact equality, not >=, so a total inflated by something else (cache counts)
    is never mistaken for unbilled reasoning.
    """
    reasoning = reported_reasoning_tokens(usage)
    if not reasoning:
        return False
    completion_tokens = _int(completion_tokens)
    if reasoning > completion_tokens:
        return True
    total = _get(usage, "total_tokens")
    return (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total == _int(prompt_tokens) + completion_tokens + reasoning
    )


def billed_completion_tokens(usage: Any, prompt_tokens: int, completion_tokens: int) -> int:
    """Completion tokens to bill: reasoning included exactly once."""
    completion_tokens = _int(completion_tokens)
    if reasoning_outside_completion(usage, prompt_tokens, completion_tokens):
        return completion_tokens + reported_reasoning_tokens(usage)
    return completion_tokens


def client_wants_usage(stream_options: Any) -> bool:
    return bool(_get(stream_options, "include_usage"))
