"""
Model Selector.

Picks/ranks the actual model to route to, given a candidate shortlist
(Phase 1's `get_candidate_models`) and a task classification (Phase 2's
`classify_task`). Revives the scoring formula and MD5-hash sticky/hysteresis
design from the deleted `src/services/model_selector.py` (commit `e94e095c`),
but sources quality from the real DB-backed
`model_capabilities_cache.get_quality_priors()` (manual/benchmark rows),
gap-filled by `quality_inference.infer_quality_from_row` for every model that
table doesn't cover, instead of a hardcoded table -- and cost from
`get_model_pricing_by_ids()` instead of a `ModelCapabilities`
schema object (that schema, `schemas/router.py`, was deleted separately and
is not revived here — see gatewayz-backend#2214).

Live since gatewayz-backend#2223 (`chat_routing.resolve_auto_routed_model`
calls `select_model()` directly and assigns its result to `req.model`) --
despite this module's original "shadow-mode only" design, it is on the
request path whenever `Config.AUTO_ROUTING_ENABLED` is true.
`log_shadow_selection()` remains for callers that want to log what this
engine would have chosen without acting on it.

Not to be confused with `Config.SMART_ROUTER_POLICY` (`src/config/config.py`),
which governs PROVIDER choice for an already-chosen model, one layer below
this. The optimization modes here (price/quality/fast/balanced) are a
separate, model-choice-level concept that happens to share the same four names.

Key properties:
  * `select_model()` is deterministic given the same inputs + conversation_id.
  * Sticky routing: the same (conversation_id, task_type) always resolves to
    the same model among near-top-scorers (within `HYSTERESIS_THRESHOLD`
    points), so a multi-turn conversation doesn't flip models on score noise.
  * The deleted formula's realtime retry/format-failure penalty is
    deliberately OMITTED — no data source for it exists anywhere in this
    codebase (it was in-memory-only in the deleted version, and there is no
    live traffic through this engine yet to observe outcomes from). A later
    phase that wires live traffic is the natural place to add it.
  * Never raises: pricing/quality lookup failures degrade to neutral default
    scores rather than blocking selection.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from src.config.supabase_config import get_client_for_query
from src.db.models_catalog_db import get_model_pricing_by_ids
from src.services.cache.model_capabilities_cache import get_quality_priors
from src.services.quality_inference import infer_quality_from_row
from src.services.task_classifier import TaskClassification

logger = logging.getLogger(__name__)

OptimizationMode = Literal["price", "quality", "fast", "balanced"]

# Any candidate scoring within this many points of the top score is treated
# as a near-tie; sticky routing picks deterministically among that cluster
# rather than always taking the single top scorer.
HYSTERESIS_THRESHOLD = 5.0

# Cost-tier lookup: $/1k input tokens -> score. Mirrors the deleted formula.
_COST_TIER_THRESHOLDS: tuple[tuple[float, float], ...] = (
    (0.0001, 95.0),
    (0.0005, 85.0),
    (0.001, 75.0),
    (0.005, 60.0),
    (0.01, 45.0),
    (0.05, 30.0),
)
_COST_SCORE_FLOOR = 15.0
_COST_SCORE_DEFAULT = 50.0
_QUALITY_SCORE_DEFAULT = 50.0
_PREFERRED_MODEL_BOOST = 3.0

_WEIGHTS: dict[OptimizationMode, tuple[float, float]] = {
    # (quality_weight, cost_weight)
    "price": (0.3, 0.7),
    "quality": (0.8, 0.2),
    "fast": (0.4, 0.6),
    "balanced": (0.5, 0.5),
}


@dataclass(frozen=True)
class ModelSelection:
    """The chosen model + why, for one selection call."""

    model_id: str | None
    reason: str
    score: float | None = None
    considered: int = 0


def _cost_score(price_per_input_token: float | None) -> float:
    """$/1k-input-token tiered score. Unknown price -> neutral default."""
    if price_per_input_token is None:
        return _COST_SCORE_DEFAULT
    price_per_1k = price_per_input_token * 1000
    for threshold, score in _COST_TIER_THRESHOLDS:
        if price_per_1k <= threshold:
            return score
    return _COST_SCORE_FLOOR


def _quality_score(
    model_id: Any,
    canonical_id: str | None,
    task_type: str,
    quality_priors: dict[str, dict[str, float]],
    fallback_model: dict[str, Any] | None = None,
) -> float:
    """Task-specific quality prior.

    `get_quality_priors()` is keyed by `canonical_id.lower()` (per
    `model_quality_scores.model_id`, which stores canonical ids), NOT by the
    row's display `id` — using the wrong key silently returns no signal.
    `model_id` is str()'d defensively: candidates from `get_candidate_models`
    carry the `models` table's integer PK as `id`, not a string, and this used
    to raise unconditionally for every real candidate (`.lower()` on an int).

    `model_quality_scores` only has 15 legacy models (seeded pre-MVP-refactor,
    never updated for the current catalog) -- for every other model this used
    to silently fall back to a flat `_QUALITY_SCORE_DEFAULT`, which made
    "quality" a constant across virtually the whole catalog and collapsed
    routing to cost-tier-only despite the classifier correctly identifying
    the task. When `fallback_model` (the raw candidate row) is provided and
    no manual/benchmark row matches, derive a real per-task-type prior from
    `infer_quality_from_row` (parameter count, reasoning/coder flags) instead
    -- same "real scores always win, inferred gap-fills the rest" precedent
    already used for the catalog's smartest/coding/tier tags.
    """
    key = str(canonical_id or model_id or "").lower()
    priors = quality_priors.get(key)
    if priors:
        return priors.get(task_type, priors.get("unknown", _QUALITY_SCORE_DEFAULT))
    if fallback_model is not None:
        inferred = infer_quality_from_row(fallback_model)
        return inferred.get(task_type, inferred.get("unknown", _QUALITY_SCORE_DEFAULT))
    return _QUALITY_SCORE_DEFAULT


def _display_model_id(model: dict[str, Any]) -> Any:
    """The request-facing model identifier for a candidate row.

    `get_candidate_models` returns raw `models` table rows: `id` is that
    table's integer primary key (only meaningful for the `model_pricing`
    join), while `provider_model_id` is the actual model string (what
    `req.model`, `get_model_pricing()`, and `model_quality_scores.model_id`
    all mean by "model id" elsewhere in the codebase). Prefer `canonical_id`
    when populated (the intended long-term key), else `provider_model_id`,
    and only fall back to the raw `id` for candidate shapes (tests) that
    already use a string there directly. `provider_model_id` is a NOT NULL
    column today so the final fallback shouldn't be reachable for a real
    catalog row, but str() it anyway -- this exact "int PK leaks out as the
    model id" shape is the bug this module was just fixed for; a malformed
    row or a future schema change must not be able to reintroduce it here.
    """
    display_id = model.get("canonical_id") or model.get("provider_model_id") or model.get("id")
    return str(display_id) if display_id is not None else None


def _blended_score(quality: float, cost: float, mode: OptimizationMode) -> float:
    quality_weight, cost_weight = _WEIGHTS.get(mode, _WEIGHTS["balanced"])
    return quality * quality_weight + cost * cost_weight


def _hash_for_selection(conversation_id: str, task_type: str) -> int:
    """Deterministic index seed for sticky routing among near-top-scorers."""
    data = f"{conversation_id}:{task_type}"
    hash_bytes = hashlib.md5(data.encode(), usedforsecurity=False).digest()
    return int.from_bytes(hash_bytes[:4], byteorder="big")


def _fetch_pricing(ids: list[Any]) -> dict[Any, dict[str, float | None]]:
    if not ids:
        return {}
    try:
        supabase = get_client_for_query(read_only=True)
        return get_model_pricing_by_ids(supabase, ids)
    except Exception as e:
        logger.warning(f"model_selector: pricing fetch failed, scoring without cost signal: {e}")
        return {}


def _fetch_quality_priors() -> dict[str, dict[str, float]]:
    try:
        return get_quality_priors()
    except Exception as e:
        logger.warning(f"model_selector: quality priors fetch failed, using defaults: {e}")
        return {}


def select_model(
    candidates: list[dict[str, Any]],
    classification: TaskClassification,
    mode: OptimizationMode = "balanced",
    conversation_id: str | None = None,
    preferred_models: list[str] | None = None,
) -> ModelSelection:
    """
    Pick/rank a model from `candidates` for the given task classification.

    Args:
        candidates: model rows from `get_candidate_models` (must carry "id",
            the `models` table's integer PK, used for the pricing join;
            the returned `ModelSelection.model_id` prefers "canonical_id"
            then "provider_model_id" -- see `_display_model_id`).
        classification: Phase 2's `TaskClassification` — `task_type` drives
            the quality-prior lookup.
        mode: "price" / "quality" / "fast" / "balanced" — a model-choice-level
            concept, distinct from `Config.SMART_ROUTER_POLICY` (provider
            choice for an already-chosen model).
        conversation_id: when provided, enables sticky routing so repeat
            calls for the same conversation + task_type land on the same
            model among near-top-scorers.
        preferred_models: model ids that get a small score boost.

    Returns:
        A `ModelSelection`. `model_id` is None when `candidates` is empty or
        none of them carry an "id".
    """
    if not candidates:
        return ModelSelection(model_id=None, reason="no_candidates", considered=0)

    ids = [c["id"] for c in candidates if c.get("id")]
    pricing_by_id = _fetch_pricing(ids)
    quality_priors = _fetch_quality_priors()
    preferred = set(preferred_models or [])

    scored: list[tuple[float, dict[str, Any]]] = []
    for model in candidates:
        model_id = model.get("id")
        if not model_id:
            continue
        display_id = _display_model_id(model)
        quality = _quality_score(
            display_id,
            model.get("canonical_id"),
            classification.task_type,
            quality_priors,
            fallback_model=model,
        )
        price = pricing_by_id.get(model_id, {})
        cost = _cost_score(price.get("in"))
        score = _blended_score(quality, cost, mode)
        if display_id in preferred:
            score += _PREFERRED_MODEL_BOOST
        scored.append((score, model))

    if not scored:
        return ModelSelection(
            model_id=None, reason="no_scored_candidates", considered=len(candidates)
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top_model = scored[0]

    if conversation_id and len(scored) > 1:
        similar = [(s, m) for s, m in scored if top_score - s < HYSTERESIS_THRESHOLD]
        if len(similar) > 1:
            hash_val = _hash_for_selection(conversation_id, classification.task_type)
            idx = hash_val % len(similar)
            selected_score, selected_model = similar[idx]
            return ModelSelection(
                model_id=_display_model_id(selected_model),
                reason="stable_selection",
                score=selected_score,
                considered=len(scored),
            )

    return ModelSelection(
        model_id=_display_model_id(top_model),
        reason="top_scorer",
        score=top_score,
        considered=len(scored),
    )


def log_shadow_selection(
    actual_model: str | None, selection: ModelSelection, task_type: str
) -> None:
    """Record what this engine would have chosen vs. what was actually used.

    Logging only — no live behavior change, no new DB table (persisted
    shadow-comparison analytics is a later rollout-safety phase's job).
    """
    logger.info(
        "model_selector shadow: task_type=%s actual=%s would_select=%s reason=%s "
        "score=%s considered=%d match=%s",
        task_type,
        actual_model,
        selection.model_id,
        selection.reason,
        selection.score,
        selection.considered,
        actual_model == selection.model_id,
    )
