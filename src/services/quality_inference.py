"""
Quality Inference.

Derives a deterministic 0-100 quality prior per task-type for EVERY model from
structural signals we already sync from providers — no hand-curated per-model
list, so it scales to any new provider/model with zero manual work.

This is the `source='inferred'` path for `model_quality_scores`. It never claims
to be a benchmark: priors come from parsed parameter count (including MoE),
whether the model is a reasoning model, and whether it is a coder variant. When
no size can be parsed (brand-only names like "glm-5.1"), a conservative neutral
prior is used and adjusted only by the reasoning/coder flags.

Key properties:
  * `infer_quality()` is PURE and deterministic — no I/O, fully unit-testable.
  * Returns the full 12-task-type map so the former `model_selector` (removed in
    the MVP refactor) was quality-aware for every category, and
    `reduce_quality_scores` gets a real `unknown`/code prior for the
    categorizer's smartest/coding/tier tags.
  * Missing size never fabricates a large-model score — the neutral base is
    intentionally mid-pack so the `smartest` tag (quality>=85) only fires for
    genuinely large, parseable models.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# The 12 task types the former model_selector.py (removed in the MVP refactor)
# scored against (mirrors ClassificationCategory).
TASK_TYPES: tuple[str, ...] = (
    "simple_qa",
    "complex_reasoning",
    "code_generation",
    "code_review",
    "creative_writing",
    "summarization",
    "translation",
    "math_calculation",
    "data_analysis",
    "conversation",
    "tool_use",
    "unknown",
)

# Neutral base used when parameter count cannot be parsed from the name.
# Deliberately mid-pack: unknown-size models should not earn `smartest`.
NEUTRAL_BASE = 62.0

# Base-quality clamp. The size curve saturates here so no derived score
# masquerades as a top-tier benchmark result.
MIN_BASE = 45.0
MAX_BASE = 88.0

# Per-task additive offsets applied to the base. Positive where a task is
# generally easier / where models tend to score higher, negative where harder.
_TASK_OFFSETS: dict[str, float] = {
    "simple_qa": 4.0,
    "complex_reasoning": -4.0,
    "code_generation": 0.0,
    "code_review": -2.0,
    "creative_writing": 2.0,
    "summarization": 4.0,
    "translation": 0.0,
    "math_calculation": -4.0,
    "data_analysis": -2.0,
    "conversation": 3.0,
    "tool_use": -3.0,
    "unknown": 0.0,
}

# Bonus for reasoning models on reasoning-heavy tasks.
_REASONING_BONUS: dict[str, float] = {
    "complex_reasoning": 8.0,
    "math_calculation": 8.0,
    "data_analysis": 4.0,
    "code_generation": 3.0,
    "code_review": 3.0,
}

# Bonus for coder-variant models on code tasks.
_CODER_BONUS: dict[str, float] = {
    "code_generation": 8.0,
    "code_review": 8.0,
    "math_calculation": 3.0,
}

# Marker substrings that identify a coder-variant model by name.
_CODER_MARKERS = ("coder", "-code", "code-", "codestral", "starcoder", "deepseek-coder")


@dataclass(frozen=True)
class QualitySignals:
    """Normalized inputs for one model. Any field may be None/False if unknown."""

    name: str | None = None  # display name or id — parsed for size/variant
    is_reasoning: bool = False
    context_length: int | None = None  # small nudge; not a primary driver


# --------------------------------------------------------------------------- #
# Parameter-count parsing (dense + MoE)
# --------------------------------------------------------------------------- #
# Matches "70b", "7B", "405b", "1.5b". Word-boundary guarded so "gpt-4" or a
# bare "3" in "gemma-3" is not mistaken for a parameter count.
_DENSE_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
# Matches MoE "8x22b" (experts x per-expert size).
_MOE_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
# Matches active-param suffix "a17b", "-a3b" used by some MoE models.
_ACTIVE_RE = re.compile(r"(?<![a-z0-9.])a(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def parse_param_billions(name: str | None) -> float | None:
    """
    Parse an effective parameter count (in billions) from a model name.

    Returns the *effective* size used for quality:
      * MoE "AxxB" active-param suffix wins when present (what actually runs).
      * MoE "NxMB" → geometric mean of total and per-expert size (between the
        two, since active compute is one expert but routing adds capacity).
      * dense "NB" → N.
    Returns None when no size token is present (brand-only names).
    """
    if not name:
        return None
    text = name.lower()

    active = _ACTIVE_RE.search(text)
    if active:
        try:
            return float(active.group(1))
        except ValueError:
            pass

    moe = _MOE_RE.search(text)
    if moe:
        try:
            experts = float(moe.group(1))
            per = float(moe.group(2))
            total = experts * per
            return math.sqrt(total * per)  # between per-expert and total
        except ValueError:
            pass

    dense = _DENSE_RE.search(text)
    if dense:
        try:
            return float(dense.group(1))
        except ValueError:
            pass
    return None


def is_coder_variant(name: str | None) -> bool:
    """True when the name marks a code-specialized model."""
    if not name:
        return False
    text = name.lower()
    return any(m in text for m in _CODER_MARKERS)


def _base_from_params(params_b: float | None) -> float:
    """
    Map effective parameter count → base quality on a saturating log curve.

    Anchors (approx): 8B→~59, 30B→~69, 70B→~75, 200B→~82, 400B→~87 (near cap).
    Only genuinely large models (~350B+ dense, or high-active MoE) can clear the
    smartest threshold (>=85). Unknown size → NEUTRAL_BASE. Never exceeds MAX_BASE.
    """
    if params_b is None or params_b <= 0:
        return NEUTRAL_BASE
    base = 45.0 + 16.0 * math.log10(params_b)
    return max(MIN_BASE, min(MAX_BASE, base))


def _context_nudge(context_length: int | None) -> float:
    """Small bonus for very long context windows (capability signal, not quality)."""
    if not context_length:
        return 0.0
    if context_length >= 200_000:
        return 2.0
    if context_length >= 128_000:
        return 1.0
    return 0.0


def infer_quality(sig: QualitySignals) -> dict[str, float]:
    """
    Return the full {task_type: score} map for one model. Pure/deterministic.

    Every score is clamped to [0, 100] and rounded to one decimal.
    """
    params_b = parse_param_billions(sig.name)
    base = _base_from_params(params_b) + _context_nudge(sig.context_length)
    coder = is_coder_variant(sig.name)

    scores: dict[str, float] = {}
    for task in TASK_TYPES:
        value = base + _TASK_OFFSETS.get(task, 0.0)
        if sig.is_reasoning:
            value += _REASONING_BONUS.get(task, 0.0)
        if coder:
            value += _CODER_BONUS.get(task, 0.0)
        scores[task] = round(max(0.0, min(100.0, value)), 1)
    return scores


def infer_quality_from_row(model: dict) -> dict[str, float]:
    """Adapt a `models` table row into QualitySignals and infer scores.

    Parses size/variant from the id fields (canonical_id/provider_model_id) since
    those carry the "70B"/"Coder" tokens; the human `model_name` usually does not.
    """
    name = (
        model.get("canonical_id")
        or model.get("provider_model_id")
        or model.get("model_name")
        or model.get("name")
    )
    ctx = model.get("context_length")
    try:
        ctx = int(ctx) if ctx is not None else None
    except (TypeError, ValueError):
        ctx = None
    return infer_quality(
        QualitySignals(
            name=name,
            is_reasoning=bool(model.get("is_reasoning")),
            context_length=ctx,
        )
    )
