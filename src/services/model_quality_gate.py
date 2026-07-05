"""Catalog quality gate — drop low-value / junk models at ingestion.

Provider-agnostic filter applied inside the model sync loop. Open-model hosts
(Featherless, Together, DeepInfra, Novita, HuggingFace, ...) expose thousands of
quantizations, merges, and fine-tune spam that clutter the catalog, wreck search,
and inflate health-sweep cost. This gate drops the obvious junk with a
high-precision, deterministic (no network) rule set so false positives are rare.

Entry point:
    assess(model, provider_slug) -> QualityVerdict(keep: bool, reason: str)

Kept the rules conservative on purpose: only patterns that are almost never a
model you'd actually want to serve. Tune via MODEL_QUALITY_GATE_* config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# High-precision junk patterns (case-insensitive), matched against model id+name.
# Each entry: (compiled_regex, reason). Order = reporting priority.
# ---------------------------------------------------------------------------
_JUNK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # GGUF / llama.cpp quant artifacts — never something to serve via a hosted API.
    (re.compile(r"\bGGUF\b", re.I), "quant:gguf"),
    (re.compile(r"\bimatrix\b|(?<![a-z0-9])i1(?![a-z0-9])", re.I), "quant:imatrix"),
    (re.compile(r"\bQ[2-8]_[0-9K](?:_[A-Z]+)?\b", re.I), "quant:llamacpp"),
    (re.compile(r"\b\d{1,2}bpw\b", re.I), "quant:bpw"),
    # GPTQ / AWQ / EXL2 / bitsandbytes weight-format variants (dupes of a base model).
    (re.compile(r"\b(GPTQ|AWQ|EXL2|EXL3)\b", re.I), "quant:weightformat"),
    (re.compile(r"(?<![a-z0-9])(?:int4|int8|4bit|8bit|3bit|2bit)(?![a-z0-9])", re.I), "quant:bits"),
    # Frankenmerges / passthrough merges — hobbyist noise.
    (
        re.compile(
            r"(?<![a-z0-9])(?:slerp|frankenmerge|passthrough|della|dare[-_]?ties|ties[-_]?merge)(?![a-z0-9])",
            re.I,
        ),
        "merge",
    ),
    # RP / ERP / uncensored spam catalogs.
    (
        re.compile(r"(?<![a-z0-9])(?:erp|nsfw|waifu|uncensored|roleplay)(?![a-z0-9])", re.I),
        "adult-rp",
    ),
]


@dataclass(frozen=True)
class QualityVerdict:
    keep: bool
    reason: str  # "ok" when kept, else the failing rule slug


def _model_text(model: dict) -> str:
    """Best-effort id/name text to match against, across provider dict shapes."""
    parts = [
        model.get("id"),
        model.get("model_id"),
        model.get("name"),
        model.get("canonical_slug"),
        model.get("slug"),
    ]
    return " ".join(str(p) for p in parts if p)


def assess(model: dict, provider_slug: str = "") -> QualityVerdict:
    """Decide whether a normalized model should enter the catalog.

    Pure function — no I/O. Returns keep=True with reason "ok", or keep=False
    with a short reason slug identifying the rule that rejected it.
    """
    text = _model_text(model).strip()

    # 1. Basic validity — must have an identifier.
    if not text:
        return QualityVerdict(False, "invalid:no-id")

    # 2. High-precision junk patterns.
    for pattern, reason in _JUNK_PATTERNS:
        if pattern.search(text):
            return QualityVerdict(False, reason)

    return QualityVerdict(True, "ok")
