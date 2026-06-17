"""Context & Memory assembly — budgeted prompt construction (Gatewayz One, Phase 4).

Pure, deterministic logic for assembling a model-agnostic conversation context
within a per-request token budget. It combines, in priority order:

  1. the request's own system message (mandatory — always included),
  2. the most-recent conversation turns verbatim (continuity is highest value),
  3. a rolling summary of older turns (condensed history),
  4. salient, model-agnostic user-memory items.

It has no I/O — the thread store and memory store (and their migrations) live
elsewhere; this module only decides *what fits* and *in what order*, so it is
fully unit-testable in isolation. A token-counter is injected (default: a
char/4 heuristic matching the codebase's fallback estimator) so tests are
deterministic and tiktoken is not required.

Final message order: ``[system] [memory note] [summary note] [recent turns…]``.
When over budget, the oldest turns are dropped first, then the summary, then
memory — the system message and the newest turns are never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Heuristic: ~4 characters per token (matches the chat pipeline's fallback).
_CHARS_PER_TOKEN = 4

MEMORY_PREAMBLE = "Relevant context about the user:"
SUMMARY_PREAMBLE = "Summary of earlier conversation:"


def estimate_tokens(text: str | None) -> int:
    """Cheap, deterministic token estimate (char/4, min 1 for non-empty)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass(frozen=True)
class MemoryItem:
    """A model-agnostic fact/preference/summary about the user."""

    content: str
    salience: float = 0.5  # 0..1, higher = more important
    kind: str = "fact"


@dataclass(frozen=True)
class ContextUsage:
    """Breakdown of what made it into the assembled context."""

    total_tokens: int = 0
    system_tokens: int = 0
    memory_tokens: int = 0
    summary_tokens: int = 0
    turn_tokens: int = 0
    turns_included: int = 0
    turns_dropped: int = 0
    memory_included: int = 0
    summary_included: bool = False


@dataclass(frozen=True)
class AssembledContext:
    messages: list[dict] = field(default_factory=list)
    usage: ContextUsage = field(default_factory=ContextUsage)


def _turn_tokens(turn: dict, counter) -> int:
    content = turn.get("content")
    if isinstance(content, list):  # multimodal → estimate text parts only
        text = " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        text = content if isinstance(content, str) else ""
    return counter(text) + counter(turn.get("role", ""))


def assemble_context(
    *,
    recent_turns: list[dict],
    system_message: str | None = None,
    rolling_summary: str | None = None,
    memory_items: list[MemoryItem] | None = None,
    token_budget: int,
    token_counter=estimate_tokens,
) -> AssembledContext:
    """Assemble a budgeted context.

    Args:
        recent_turns: conversation messages in chronological order (oldest first),
            each a dict with at least ``role`` and ``content``.
        system_message: the request's system prompt (always kept).
        rolling_summary: condensed text of older turns (kept if it fits).
        memory_items: user-memory items (highest-salience kept first if they fit).
        token_budget: maximum tokens for the assembled context (> 0).
        token_counter: ``str -> int`` estimator (injected for testability).

    Returns:
        AssembledContext with the ordered messages and a usage breakdown.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    memory_items = memory_items or []
    remaining = token_budget

    # 1. System message — mandatory, reserved first (may exceed budget if huge).
    system_tokens = token_counter(system_message) if system_message else 0
    remaining -= system_tokens

    # 2. Recent turns, newest-first; keep a contiguous recent window.
    kept_turns: list[dict] = []
    turn_tokens_total = 0
    dropped = 0
    stop = False
    for turn in reversed(recent_turns):
        t = _turn_tokens(turn, token_counter)
        if not stop and t <= remaining:
            kept_turns.append(turn)
            remaining -= t
            turn_tokens_total += t
        else:
            stop = True  # once one doesn't fit, drop all older turns
            dropped += 1
    kept_turns.reverse()  # restore chronological order

    # 3. Rolling summary — include if it fits in what's left.
    summary_tokens = 0
    summary_included = False
    if rolling_summary:
        s = token_counter(f"{SUMMARY_PREAMBLE} {rolling_summary}")
        if s <= remaining:
            summary_tokens = s
            summary_included = True
            remaining -= s

    # 4. Memory — highest salience first, include those that fit.
    memory_kept: list[MemoryItem] = []
    memory_tokens_total = 0
    for item in sorted(memory_items, key=lambda m: m.salience, reverse=True):
        m_tokens = token_counter(item.content)
        if m_tokens <= remaining:
            memory_kept.append(item)
            memory_tokens_total += m_tokens
            remaining -= m_tokens

    # Assemble in final prompt order.
    messages: list[dict] = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    if memory_kept:
        body = "\n".join(f"- {m.content}" for m in memory_kept)
        messages.append({"role": "system", "content": f"{MEMORY_PREAMBLE}\n{body}"})
    if summary_included:
        messages.append({"role": "system", "content": f"{SUMMARY_PREAMBLE} {rolling_summary}"})
    messages.extend(kept_turns)

    usage = ContextUsage(
        total_tokens=system_tokens + memory_tokens_total + summary_tokens + turn_tokens_total,
        system_tokens=system_tokens,
        memory_tokens=memory_tokens_total,
        summary_tokens=summary_tokens,
        turn_tokens=turn_tokens_total,
        turns_included=len(kept_turns),
        turns_dropped=dropped,
        memory_included=len(memory_kept),
        summary_included=summary_included,
    )
    return AssembledContext(messages=messages, usage=usage)


def should_summarize(
    recent_turns: list[dict],
    token_budget: int,
    *,
    token_counter=estimate_tokens,
    trigger_ratio: float = 0.7,
) -> bool:
    """True when the thread is large enough that older turns should be rolled
    into a summary.

    Triggers when the conversation's total token estimate exceeds
    ``trigger_ratio`` of the budget — i.e. before assembly is forced to start
    dropping turns.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    total = sum(_turn_tokens(t, token_counter) for t in recent_turns)
    return total > token_budget * trigger_ratio
