"""
Model Categorizer.

Assigns derived, multi-label category tags to every model in the catalog from
data we already pull from providers (price, latency, context size, quality,
capabilities). Tags are used by the catalog/UI for grouping and become the
candidate-filter primitive for routing (`categories @> '{fastest}'`).

Design: docs/superpowers/specs/2026-07-04-model-categorization-design.md

Key properties:
  * `compute_categories()` is PURE and deterministic — no I/O, fully unit-testable.
  * Thresholds live in the `category_rules` DB table (tunable without a deploy).
    `DEFAULT_RULES` below is the in-code fallback used when the DB is unreachable
    or a category has no row, so categorization never hard-fails.
  * Missing source data never guesses a tag — the tag is simply not applied.
    The one exception is the quality *tier* (flagship|mid|budget): every model
    gets exactly one, defaulting to `budget` when quality is unknown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Tier tags are mutually exclusive — a model gets exactly one of these.
TIER_CATEGORIES = ("flagship", "mid", "budget")


@dataclass(frozen=True)
class CategoryRule:
    """One tunable rule, mirroring a row of the category_rules table."""

    category: str
    dimension: str  # blended_price | latency_tier | context_length | quality
    # | quality_code | is_reasoning | modality | is_free
    # | value_ratio | quality_band
    operator: str  # lte | gte | eq | contains | band
    threshold: float | None = None
    threshold2: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


# In-code fallback rules — MUST stay in sync with the seed block of
# supabase/migrations/20260704000000_add_model_categories.sql.
DEFAULT_RULES: list[CategoryRule] = [
    CategoryRule(
        "cheapest",
        "blended_price",
        "lte",
        0.50,
        params={"weight_input": 0.25, "weight_output": 0.75},
    ),
    CategoryRule("fastest", "latency_tier", "lte", 2),
    CategoryRule("largest", "context_length", "gte", 200_000),
    CategoryRule("smartest", "quality", "gte", 85),
    CategoryRule("long-context", "context_length", "gte", 128_000),
    CategoryRule("coding", "quality_code", "gte", 85),
    CategoryRule("reasoning", "is_reasoning", "eq"),
    CategoryRule(
        "vision", "modality", "contains", params={"needles": ["image", "vision", "multimodal"]}
    ),
    CategoryRule("free", "is_free", "eq"),
    CategoryRule("balanced", "value_ratio", "gte", 200),
    CategoryRule("flagship", "quality_band", "band", 90),
    CategoryRule("mid", "quality_band", "band", 70, 90),
    CategoryRule("budget", "quality_band", "band", 0, 70),
]


@dataclass(frozen=True)
class ModelSignals:
    """Normalized inputs for one model. Any field may be None if unknown."""

    context_length: int | None = None
    latency_tier: int | None = None
    is_reasoning: bool = False
    is_free: bool = False
    modality: str | None = None
    input_price_per_token: float | None = None  # normalized $/token
    output_price_per_token: float | None = None  # normalized $/token
    quality_overall: float | None = None  # 0..100
    quality_code: float | None = None  # 0..100


# --------------------------------------------------------------------------- #
# Derived scalars
# --------------------------------------------------------------------------- #
def _blended_price_per_1m(sig: ModelSignals, rule: CategoryRule) -> float | None:
    """Completion-weighted blended price in $ per 1M tokens, or None if no price."""
    inp = sig.input_price_per_token
    out = sig.output_price_per_token
    if inp is None and out is None:
        return None
    inp = inp or 0.0
    out = out or 0.0
    w_in = float(rule.params.get("weight_input", 0.25))
    w_out = float(rule.params.get("weight_output", 0.75))
    return (inp * w_in + out * w_out) * 1_000_000


def _value_ratio(sig: ModelSignals, rule: CategoryRule) -> float | None:
    """Quality per $/1M tokens. None if quality or price unknown."""
    if sig.quality_overall is None:
        return None
    price = _blended_price_per_1m(sig, rule)
    if price is None or price <= 0:
        return None
    return sig.quality_overall / price


def _dimension_value(dimension: str, sig: ModelSignals, rule: CategoryRule) -> Any:
    """Resolve the raw comparable value for a dimension (may be None => skip)."""
    if dimension == "blended_price":
        return _blended_price_per_1m(sig, rule)
    if dimension == "value_ratio":
        return _value_ratio(sig, rule)
    if dimension == "latency_tier":
        return sig.latency_tier
    if dimension == "context_length":
        return sig.context_length
    if dimension == "quality":
        return sig.quality_overall
    if dimension == "quality_code":
        return sig.quality_code
    if dimension == "is_reasoning":
        return sig.is_reasoning
    if dimension == "is_free":
        return sig.is_free
    if dimension == "modality":
        return sig.modality
    logger.debug("Unknown categorizer dimension %r — rule ignored", dimension)
    return None


def _matches(rule: CategoryRule, sig: ModelSignals) -> bool:
    """Evaluate a single non-tier rule against a model. Tier rules handled separately."""
    value = _dimension_value(rule.dimension, sig, rule)

    if rule.operator == "eq":
        # Boolean capability flags: apply the tag only when true.
        return bool(value)

    if rule.operator == "contains":
        if not value:
            return False
        haystack = str(value).lower()
        needles = rule.params.get("needles") or []
        return any(str(n).lower() in haystack for n in needles)

    # Numeric comparisons require a known value and threshold.
    if value is None or rule.threshold is None:
        return False
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False

    if rule.operator == "lte":
        return num <= float(rule.threshold)
    if rule.operator == "gte":
        return num >= float(rule.threshold)
    return False


def _resolve_tier(sig: ModelSignals, rules: list[CategoryRule]) -> str:
    """Pick exactly one tier tag from quality bands. Unknown quality => budget."""
    band_rules = [r for r in rules if r.dimension == "quality_band" and r.enabled]
    quality = sig.quality_overall
    if quality is not None:
        for r in band_rules:
            lo = float(r.threshold) if r.threshold is not None else 0.0
            hi = float(r.threshold2) if r.threshold2 is not None else float("inf")
            # Band is [lo, hi): flagship uses lo=90, hi=inf; mid [70,90); budget [0,70).
            if lo <= quality < hi or (hi == float("inf") and quality >= lo):
                return r.category
    # Fail-safe: default to the lowest enabled tier, else literal 'budget'.
    if band_rules:
        return min(
            band_rules,
            key=lambda r: float(r.threshold) if r.threshold is not None else 0.0,
        ).category
    return "budget"


def compute_categories(
    sig: ModelSignals,
    rules: list[CategoryRule] | None = None,
) -> list[str]:
    """
    Return the sorted list of category tags for one model.

    Pure and deterministic. `rules` defaults to DEFAULT_RULES; pass DB-loaded
    rules to honor tunable thresholds. Exactly one tier tag is always included.
    """
    rules = rules if rules is not None else DEFAULT_RULES
    tags: set[str] = set()

    for rule in rules:
        if not rule.enabled or rule.dimension == "quality_band":
            continue  # tiers resolved separately
        if _matches(rule, sig):
            tags.add(rule.category)

    tags.add(_resolve_tier(sig, rules))
    return sorted(tags)


# --------------------------------------------------------------------------- #
# Adapters: build ModelSignals from a raw `models` row + side data
# --------------------------------------------------------------------------- #
def signals_from_model_row(
    model: dict[str, Any],
    input_price_per_token: float | None = None,
    output_price_per_token: float | None = None,
    quality_overall: float | None = None,
    quality_code: float | None = None,
) -> ModelSignals:
    """Adapt a `models` table row (+ optional pricing/quality) into ModelSignals."""
    modality = model.get("modality")
    if not modality:
        # Some rows carry modality under architecture.modality.
        arch = model.get("architecture")
        if isinstance(arch, dict):
            modality = arch.get("modality")

    return ModelSignals(
        context_length=_as_int(model.get("context_length")),
        latency_tier=_as_int(model.get("latency_tier")),
        is_reasoning=bool(model.get("is_reasoning")),
        is_free=bool(model.get("is_free")),
        modality=modality,
        input_price_per_token=input_price_per_token,
        output_price_per_token=output_price_per_token,
        quality_overall=quality_overall,
        quality_code=quality_code,
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reduce_quality_scores(scores: dict[str, float]) -> tuple[float | None, float | None]:
    """
    Reduce a {task_type: score} map to (overall, code) priors.

    overall = the 'unknown' catch-all score if present (matches how model_selector
    treats 'unknown'), else the mean of all task scores. code = 'code_generation'.
    Returns (None, None) when no scores are known.
    """
    if not scores:
        return None, None
    overall = scores.get("unknown")
    if overall is None:
        vals = [v for v in scores.values() if v is not None]
        overall = sum(vals) / len(vals) if vals else None
    code = scores.get("code_generation")
    return overall, code


# --------------------------------------------------------------------------- #
# Rules loader (DB-backed, TTL-cached; falls back to DEFAULT_RULES)
# --------------------------------------------------------------------------- #
_RULES_CACHE_TTL_SECONDS = 15 * 60
_rules_cache: list[CategoryRule] | None = None
_rules_cache_expiry: float = 0.0


def load_rules(supabase: Any = None, force_refresh: bool = False) -> list[CategoryRule]:
    """
    Load category rules from the category_rules table, cached for 15 min.

    Falls back to DEFAULT_RULES if the table is empty/unreachable so
    categorization never hard-fails. Pass a supabase client to avoid re-importing.
    """
    global _rules_cache, _rules_cache_expiry
    import time

    now = time.monotonic()
    if not force_refresh and _rules_cache is not None and now < _rules_cache_expiry:
        return _rules_cache

    rules: list[CategoryRule] = []
    try:
        if supabase is None:
            from src.config.supabase_config import get_supabase_client

            supabase = get_supabase_client()
        resp = supabase.table("category_rules").select("*").execute()
        for r in resp.data or []:
            rules.append(
                CategoryRule(
                    category=r["category"],
                    dimension=r["dimension"],
                    operator=r["operator"],
                    threshold=(float(r["threshold"]) if r.get("threshold") is not None else None),
                    threshold2=(
                        float(r["threshold2"]) if r.get("threshold2") is not None else None
                    ),
                    params=r.get("params") or {},
                    enabled=bool(r.get("enabled", True)),
                )
            )
    except Exception as e:  # noqa: BLE001 — resilient by design
        logger.warning("category_rules load failed (%s); using DEFAULT_RULES", e)

    if not rules:
        rules = list(DEFAULT_RULES)

    _rules_cache = rules
    _rules_cache_expiry = now + _RULES_CACHE_TTL_SECONDS
    return rules
