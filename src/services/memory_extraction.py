"""Heuristic user-memory capture (Gatewayz One Phase 4 — the "populate" half).

Extracts durable, self-stated facts from a user's chat messages and persists them
to ``user_memory`` (read back by the context assembler). Deliberately heuristic and
high-precision — it only captures explicit first-person statements ("my name is…",
"I prefer…", "remember that…"), not inferred or sensitive content — so it adds no
LLM cost and writes only memory-worthy, user-volunteered facts.

The extractor is pure + unit-tested. ``capture_user_memory`` is the I/O entry point
(runs as a post-response background task): it dedupes against existing memory,
respects a per-user cap, never raises, and is a no-op when the flag is off.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Each pattern captures group(1) = the salient phrase. Applied to USER messages only.
# (regex, fact template, salience, kind). Templates use {0} for the captured phrase.
_PATTERNS: list[tuple[re.Pattern, str, float, str]] = [
    (
        re.compile(r"\bmy name is ([A-Z][\w'’-]+(?:\s[A-Z][\w'’-]+){0,2})"),
        "User's name is {0}",
        0.9,
        "identity",
    ),
    (re.compile(r"\b(?:call me|i go by) ([A-Z][\w'’-]+)"), "User goes by {0}", 0.85, "identity"),
    (
        re.compile(r"\bremember (?:that |this[:,]? )?([^.?!\n]{4,120})", re.IGNORECASE),
        "{0}",
        0.85,
        "fact",
    ),
    (
        re.compile(r"\bI work (?:at|for|as)\s+([^.?!\n]{2,60})", re.IGNORECASE),
        "User works {0}",
        0.7,
        "fact",
    ),
    (
        re.compile(
            r"\bI (?:prefer|like to use|always use|usually use|use)\s+([^.?!\n]{2,60})",
            re.IGNORECASE,
        ),
        "User prefers {0}",
        0.6,
        "preference",
    ),
    (
        re.compile(r"\bI'?m (?:a |an )([a-z][^.?!\n]{2,50})", re.IGNORECASE),
        "User is a {0}",
        0.55,
        "identity",
    ),
]

_MAX_CANDIDATES_PER_CALL = 5
_RECENT_USER_TURNS = 6
# Per-pattern regex quantifiers bound the longer phrases; this global floor only
# rejects empty/single-char junk, so short names ("Sam", "Ada") still pass.
_MIN_LEN, _MAX_LEN = 2, 160


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _clean(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase).strip().strip(" .,;:'\"")


def extract_candidate_facts(messages: list[dict]) -> list[tuple[str, float, str]]:
    """Extract (content, salience, kind) candidates from a user's recent messages.

    Pure. Scans only ``role == 'user'`` messages (most recent ``_RECENT_USER_TURNS``),
    deduplicates case-insensitively, and caps the result. Returns [] when nothing
    memory-worthy is found.
    """
    user_texts = [
        _message_text(m.get("content"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    user_texts = [t for t in user_texts if t][-_RECENT_USER_TURNS:]

    out: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    for text in user_texts:
        for pattern, template, salience, kind in _PATTERNS:
            for match in pattern.finditer(text):
                phrase = _clean(match.group(1))
                if not (_MIN_LEN <= len(phrase) <= _MAX_LEN):
                    continue
                content = template.format(phrase)
                key = content.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((content, salience, kind))
                if len(out) >= _MAX_CANDIDATES_PER_CALL:
                    return out
    return out


def capture_user_memory(user_id, messages: list[dict]) -> int:
    """Persist newly-stated facts for a user (background task). Returns count stored.

    Dedupes against existing memory (case-insensitive substring), respects
    ``MEMORY_MAX_PER_USER``, and never raises.
    """
    try:
        if user_id is None:
            return 0
        candidates = extract_candidate_facts(messages or [])
        if not candidates:
            return 0

        from src.config import Config
        from src.db.user_memory import add_memory, get_memories

        existing = get_memories(user_id, 200)
        existing_norm = [(e.get("content") or "").strip().lower() for e in existing]
        cap = getattr(Config, "MEMORY_MAX_PER_USER", 100)
        count = len(existing)
        stored = 0
        for content, salience, kind in candidates:
            if count >= cap:
                break
            norm = content.strip().lower()
            # skip exact dupes and near-dupes (one contained in the other)
            if any(norm == e or norm in e or e in norm for e in existing_norm):
                continue
            try:
                add_memory(user_id, content, kind=kind, salience=salience)
                existing_norm.append(norm)
                count += 1
                stored += 1
            except Exception as e:
                logger.debug("memory capture: add failed for user %s: %s", user_id, e)
        if stored:
            logger.info("Captured %d new memory item(s) for user %s", stored, user_id)
        return stored
    except Exception as e:
        logger.warning("memory capture failed (non-fatal) for user %s: %s", user_id, e)
        return 0
