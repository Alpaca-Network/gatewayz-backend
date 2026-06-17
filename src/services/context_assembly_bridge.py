"""Context-assembly bridge — wire the pure Phase 4 assembler into the live chat path.

The pure assembler (:mod:`src.services.context_assembly`) decides what fits in a
token budget and in what order: ``[system] [memory] [summary] [recent turns]``,
dropping the oldest turns first. This bridge maps the live chat message list
(role/content dicts) onto that assembler and back:

  * splits the request's system message(s) from the conversation turns,
  * resolves a token budget from the model's context window,
  * best-effort loads portable per-user memory (Phase 1 ``user_memory``) and an
    optional rolling summary,
  * returns the reassembled message list.

Safety: gated by ``Config.CONTEXT_ASSEMBLY_ENABLED`` (off by default) at the call
site. When disabled the caller never invokes this. When enabled but there is
nothing to add (no memory/summary) and the budget is ample, the output equals the
input turn order. Any failure returns the original messages unchanged.
"""

from __future__ import annotations

import logging

from src.services.context_assembly import (
    MemoryItem,
    assemble_context,
    estimate_tokens,
)

logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_LIMIT = 20


def _content_to_text(content) -> str:
    """Flatten a message ``content`` (str or multimodal list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def split_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Split into (combined system message, conversation turns).

    All ``role == "system"`` messages are merged (in order) into one system string;
    every other message is kept verbatim as a turn (preserving multimodal content).
    """
    system_parts: list[str] = []
    turns: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            text = _content_to_text(m.get("content"))
            if text:
                system_parts.append(text)
        else:
            turns.append(m)
    system_message = "\n\n".join(system_parts) if system_parts else None
    return system_message, turns


def _lookup_context_length(model: str) -> int | None:
    """Best-effort model context-window lookup from the cached catalog. None on miss."""
    try:
        from src.services.models import get_cached_models

        for m in get_cached_models("all") or []:
            if m.get("id") == model:
                cl = m.get("context_length")
                return int(cl) if cl else None
    except Exception as e:
        logger.debug("context_assembly: context-length lookup failed for %s: %s", model, e)
    return None


def _resolve_budget(model: str | None, token_budget: int | None, ratio: float, default: int) -> int:
    """Resolve the assembly token budget (explicit > model window × ratio > default)."""
    if token_budget and token_budget > 0:
        return token_budget
    cl = _lookup_context_length(model) if model else None
    if cl and cl > 0:
        return max(1, int(cl * ratio))
    return default


def _load_user_memory(user_id, limit: int = _DEFAULT_MEMORY_LIMIT) -> list[MemoryItem]:
    """Best-effort load of portable per-user memory (Phase 1 user_memory). [] on failure."""
    if user_id is None:
        return []
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        resp = (
            client.table("user_memory")
            .select("content,salience,kind")
            .eq("user_id", user_id)
            .order("salience", desc=True)
            .limit(limit)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return [
            MemoryItem(
                content=r["content"],
                salience=float(r.get("salience") if r.get("salience") is not None else 0.5),
                kind=r.get("kind") or "fact",
            )
            for r in rows
            if r.get("content")
        ]
    except Exception as e:
        logger.debug("context_assembly: user_memory load failed for %s: %s", user_id, e)
        return []


def apply_context_budget(
    messages: list[dict],
    *,
    model: str | None = None,
    token_budget: int | None = None,
    budget_ratio: float = 0.7,
    default_budget: int = 8192,
    user_id=None,
    memory_items: list[MemoryItem] | None = None,
    rolling_summary: str | None = None,
) -> list[dict]:
    """Reassemble ``messages`` within a token budget. Returns originals on failure.

    ``memory_items`` may be injected (tests); otherwise loaded best-effort by
    ``user_id``. ``rolling_summary`` is optional condensed older-history text.
    """
    if not messages:
        return messages
    try:
        budget = _resolve_budget(model, token_budget, budget_ratio, default_budget)
        system_message, turns = split_messages(messages)
        if memory_items is None:
            memory_items = _load_user_memory(user_id)
        assembled = assemble_context(
            recent_turns=turns,
            system_message=system_message,
            rolling_summary=rolling_summary,
            memory_items=memory_items,
            token_budget=budget,
            token_counter=estimate_tokens,
        )
        if assembled.usage.turns_dropped:
            logger.info(
                "context_assembly: budget=%d dropped %d oldest turn(s) for model=%s",
                budget,
                assembled.usage.turns_dropped,
                model,
            )
        return assembled.messages
    except Exception as e:  # never break the request over context shaping
        logger.warning("context_assembly failed (using original messages): %s", e)
        return messages
