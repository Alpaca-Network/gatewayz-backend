"""Tests for src.services.model_selector (gatewayz-backend#2214)."""

from unittest.mock import patch

from src.services.model_selector import (
    ModelSelection,
    _cost_score,
    _gap_fill_quality_priors,
    _hash_for_selection,
    _quality_score,
    log_shadow_selection,
    resolve_adaptive_mode,
    select_model,
)
from src.services.task_classifier import TaskClassification


def _classification(task_type="code_generation"):
    return TaskClassification(task_type=task_type, capability_names=frozenset(), confidence=0.8)


def _patched_select(candidates, classification, **kwargs):
    """select_model with pricing/quality fetches mocked to fixed dicts."""
    pricing = kwargs.pop("pricing", {})
    quality_priors = kwargs.pop("quality_priors", {})
    with (
        patch("src.services.model_selector._fetch_pricing", return_value=pricing),
        patch("src.services.model_selector._fetch_quality_priors", return_value=quality_priors),
    ):
        return select_model(candidates, classification, **kwargs)


# --------------------------------------------------------------------------- #
# Pure scoring helpers
# --------------------------------------------------------------------------- #
def test_cost_score_tiers():
    # _cost_score takes $/token and converts to $/1k internally (x1000).
    assert _cost_score(0.00000005) == 95.0  # $0.00005/1k -> top tier
    assert _cost_score(0.0000003) == 85.0  # $0.0003/1k
    assert _cost_score(0.00006) == 15.0  # $0.06/1k -> floor
    assert _cost_score(None) == 50.0  # unknown -> neutral default


def test_quality_score_does_not_raise_on_integer_model_id():
    """Regression: `get_candidate_models` candidates carry the `models` table's
    integer PK as `id` (canonical_id is unpopulated in production today), and
    `.lower()` on that int used to raise `AttributeError` for every real
    auto-routing candidate -- the classifier worked, but selection crashed
    on the very first candidate scored.
    """
    assert _quality_score(2298, None, "code_generation", {}) == 50.0


def test_quality_score_uses_canonical_id_not_display_id():
    priors = {"openai/gpt-4o-mini": {"code_generation": 80.0}}

    # display id "gpt-4o-mini" (no provider prefix) must NOT match; canonical_id must.
    assert _quality_score("gpt-4o-mini", "openai/gpt-4o-mini", "code_generation", priors) == 80.0
    assert _quality_score("gpt-4o-mini", None, "code_generation", priors) == 50.0


def test_quality_score_falls_back_to_unknown_task_then_default():
    priors_with_unknown = {"model-a": {"unknown": 70.0}}
    assert _quality_score("model-a", None, "code_generation", priors_with_unknown) == 70.0

    priors_without_task = {"model-a": {}}
    assert _quality_score("model-a", None, "code_generation", priors_without_task) == 50.0


def test_gap_fill_adds_inferred_prior_for_uncovered_candidates():
    """The actual fix: `model_quality_scores` only covers 15 legacy models, so
    every other candidate used to get a flat `_QUALITY_SCORE_DEFAULT` --
    making "quality" a constant across virtually the whole catalog and
    collapsing routing to cost-tier-only despite a correct task_type.
    `_gap_fill_quality_priors` must add a real per-task-type entry for a
    candidate with no manual prior, so `_quality_score` stops returning the
    flat default for it. A large coder model must score higher than a small
    plain-chat model on code_generation, with zero manual data.
    """
    large_coder = {"id": 1, "provider_model_id": "some-vendor/big-model-70b-coder"}
    small_plain = {"id": 2, "provider_model_id": "some-vendor/tiny-model-3b"}

    merged = _gap_fill_quality_priors([large_coder, small_plain], {})

    big_score = _quality_score("some-vendor/big-model-70b-coder", None, "code_generation", merged)
    small_score = _quality_score("some-vendor/tiny-model-3b", None, "code_generation", merged)
    assert big_score > small_score
    assert big_score != 50.0 and small_score != 50.0  # neither is the flat default anymore


def test_gap_fill_never_overwrites_a_manual_prior():
    """ "Real scores always win" -- a `model_quality_scores` hit must survive
    gap-fill even for a candidate that also has an id inference could derive
    a (different) score for."""
    priors = {"openai/gpt-4o-mini": {"code_generation": 82.0}}
    model = {"id": 1, "provider_model_id": "openai/gpt-4o-mini"}

    merged = _gap_fill_quality_priors([model], priors)

    assert merged["openai/gpt-4o-mini"]["code_generation"] == 82.0


def test_gap_fill_never_raises_on_a_malformed_row():
    """Regression (code review on gatewayz-backend#2230): `infer_quality_from_row`
    parses `.lower()` on whatever name field it finds -- an unguarded call broke
    this module's own "never raises" invariant (mirrors the try/except already
    wrapping `_fetch_pricing`/`_fetch_quality_priors`). A malformed row must not
    abort selection for every other candidate in the same request.
    """
    malformed = {"id": 1, "provider_model_id": 12345}  # non-string, would break .lower()
    well_formed = {"id": 2, "provider_model_id": "vendor/normal-model-7b"}

    merged = _gap_fill_quality_priors([malformed, well_formed], {})

    assert "vendor/normal-model-7b" in merged  # the other candidate still got gap-filled
    assert _quality_score(1, None, "code_generation", merged) == 50.0  # malformed -> flat default,
    # not a crash for the whole request


def test_quality_score_stays_a_pure_lookup_with_no_signal_for_uncovered_ids():
    """`_quality_score` itself never infers -- that's `_gap_fill_quality_priors`'s
    job, called once upfront. Without it, an uncovered id is still the flat
    default; this is what select_model would fall back to if gap-fill failed
    for a given candidate."""
    assert _quality_score("unknown-model", None, "code_generation", {}) == 50.0


def test_select_model_ranks_large_reasoning_model_above_small_plain_model_in_quality_mode():
    """End-to-end: with zero manual quality data and no canonical_id (today's
    live production shape), "quality" mode must still prefer the objectively
    stronger model for a reasoning-heavy task -- not just tie everything at
    the flat default and let cost alone decide.
    """
    candidates = [
        {"id": 1, "provider_model_id": "vendor/flagship-400b-reasoner", "is_reasoning": True},
        {"id": 2, "provider_model_id": "vendor/tiny-3b-chat", "is_reasoning": False},
    ]
    # Deliberately IDENTICAL pricing so cost cannot explain the outcome.
    pricing = {1: {"in": 0.001, "out": 0.001}, 2: {"in": 0.001, "out": 0.001}}
    classification = _classification(task_type="complex_reasoning")

    result = _patched_select(candidates, classification, mode="quality", pricing=pricing)

    assert result.model_id == "vendor/flagship-400b-reasoner"


def test_hash_for_selection_is_deterministic():
    a = _hash_for_selection("conv-123", "code_generation")
    b = _hash_for_selection("conv-123", "code_generation")
    assert a == b


def test_hash_for_selection_varies_by_task_type():
    a = _hash_for_selection("conv-123", "code_generation")
    b = _hash_for_selection("conv-123", "creative_writing")
    assert a != b


# --------------------------------------------------------------------------- #
# resolve_adaptive_mode
# --------------------------------------------------------------------------- #
def test_resolve_adaptive_mode_quality_bucket():
    for task_type in (
        "code_generation",
        "code_review",
        "complex_reasoning",
        "data_analysis",
        "math_calculation",
    ):
        assert resolve_adaptive_mode(task_type) == "quality"


def test_resolve_adaptive_mode_fast_bucket():
    for task_type in ("simple_qa", "conversation", "summarization", "translation"):
        assert resolve_adaptive_mode(task_type) == "fast"


def test_resolve_adaptive_mode_balanced_bucket():
    for task_type in ("creative_writing", "tool_use", "unknown"):
        assert resolve_adaptive_mode(task_type) == "balanced"


def test_resolve_adaptive_mode_unrecognized_task_type_falls_back_to_balanced():
    assert resolve_adaptive_mode("not_a_real_task_type") == "balanced"
    assert resolve_adaptive_mode("") == "balanced"


# --------------------------------------------------------------------------- #
# select_model
# --------------------------------------------------------------------------- #
def test_no_candidates_returns_none():
    result = select_model([], _classification())

    assert result == ModelSelection(model_id=None, reason="no_candidates", considered=0)


def test_candidates_without_id_are_skipped():
    result = _patched_select([{"model_name": "no id here"}], _classification())

    assert result.model_id is None
    assert result.reason == "no_scored_candidates"


def test_picks_top_scorer_by_default():
    candidates = [{"id": "cheap-model"}, {"id": "expensive-model"}]
    pricing = {
        "cheap-model": {"in": 0.00005, "out": 0.0001},
        "expensive-model": {"in": 0.06, "out": 0.1},
    }

    result = _patched_select(candidates, _classification(), mode="price", pricing=pricing)

    assert result.model_id == "cheap-model"
    assert result.reason == "top_scorer"
    assert result.considered == 2


def test_preferred_model_gets_boosted():
    candidates = [{"id": "model-a"}, {"id": "model-b"}]
    # Identical pricing/quality so the boost is the only differentiator.
    pricing = {"model-a": {"in": 0.001, "out": 0.001}, "model-b": {"in": 0.001, "out": 0.001}}

    result = _patched_select(
        candidates, _classification(), pricing=pricing, preferred_models=["model-b"]
    )

    assert result.model_id == "model-b"


def test_sticky_routing_is_stable_across_calls_for_same_conversation():
    candidates = [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-c"}]
    # All within the hysteresis band of each other (identical pricing/quality).
    pricing = {m["id"]: {"in": 0.001, "out": 0.001} for m in candidates}

    first = _patched_select(
        candidates, _classification(), pricing=pricing, conversation_id="conv-abc"
    )
    second = _patched_select(
        candidates, _classification(), pricing=pricing, conversation_id="conv-abc"
    )

    assert first.model_id == second.model_id
    assert first.reason == "stable_selection"


def test_sticky_routing_only_considers_near_top_scorers():
    candidates = [{"id": "top"}, {"id": "far-behind"}]
    pricing = {
        "top": {"in": 0.00000005, "out": 0.00000005},  # cost_score 95 (top tier)
        "far-behind": {"in": 0.00006, "out": 0.00006},  # cost_score 15 (floor)
    }
    # Score gap (36 vs ~25.5, mode="price") exceeds HYSTERESIS_THRESHOLD, so
    # far-behind never enters the near-top-scorer cluster.

    result = _patched_select(
        candidates, _classification(), mode="price", pricing=pricing, conversation_id="any-conv"
    )

    assert result.model_id == "top"
    assert result.reason == "top_scorer"


def test_mode_changes_which_model_wins():
    candidates = [{"id": "cheap-low-quality"}, {"id": "pricey-high-quality"}]
    pricing = {
        "cheap-low-quality": {"in": 0.00000005, "out": 0.00000005},  # cost_score 95 (top tier)
        "pricey-high-quality": {"in": 0.00006, "out": 0.00006},  # cost_score 15 (floor)
    }
    quality_priors = {
        "cheap-low-quality": {"code_generation": 40.0},
        "pricey-high-quality": {"code_generation": 95.0},
    }

    price_mode = _patched_select(
        candidates, _classification(), mode="price", pricing=pricing, quality_priors=quality_priors
    )
    quality_mode = _patched_select(
        candidates,
        _classification(),
        mode="quality",
        pricing=pricing,
        quality_priors=quality_priors,
    )

    assert price_mode.model_id == "cheap-low-quality"
    assert quality_mode.model_id == "pricey-high-quality"


# --------------------------------------------------------------------------- #
# select_model against real `get_candidate_models` row shape
# --------------------------------------------------------------------------- #
def test_select_model_on_raw_catalog_rows_returns_provider_model_id_not_db_pk():
    """`get_candidate_models` returns raw `models` table rows: `id` is that
    table's integer PK (meaningful only for the `model_pricing` join), and
    the actual model string lives in `provider_model_id`. Before this fix,
    `select_model` returned the raw int PK as `ModelSelection.model_id`,
    which becomes `req.model` in chat_routing.py -- passing an integer
    through as the model name would break every downstream provider-dispatch
    and pricing-gate call expecting a string like "openai/gpt-4o-mini".
    """
    candidates = [
        {"id": 2298, "provider_model_id": "together/Qwen3-Coder-480B", "canonical_id": None},
        {"id": 5001, "provider_model_id": "openai/gpt-4o-mini", "canonical_id": None},
    ]
    pricing = {
        2298: {"in": 0.00000005, "out": 0.00000005},  # cheapest -> wins in "price" mode
        5001: {"in": 0.06, "out": 0.06},
    }

    result = _patched_select(candidates, _classification(), mode="price", pricing=pricing)

    assert result.model_id == "together/Qwen3-Coder-480B"
    assert isinstance(result.model_id, str)


def test_select_model_final_fallback_is_stringified_even_without_provider_model_id():
    """Regression (code review on gatewayz-backend#2227): `provider_model_id`
    is NOT NULL today, so this path isn't reachable for a real catalog row,
    but a malformed row or future schema change must not be able to leak the
    raw integer PK back out as `ModelSelection.model_id` -- that's the exact
    bug class this module was just fixed for.
    """
    candidates = [{"id": 777}]
    pricing = {777: {"in": 0.001, "out": 0.001}}

    result = _patched_select(candidates, _classification(), pricing=pricing)

    assert result.model_id == "777"
    assert isinstance(result.model_id, str)


def test_select_model_on_raw_catalog_rows_prefers_canonical_id_when_present():
    candidates = [{"id": 42, "provider_model_id": "raw/slug", "canonical_id": "openai/gpt-4o-mini"}]
    pricing = {42: {"in": 0.001, "out": 0.001}}

    result = _patched_select(candidates, _classification(), pricing=pricing)

    assert result.model_id == "openai/gpt-4o-mini"


# --------------------------------------------------------------------------- #
# log_shadow_selection
# --------------------------------------------------------------------------- #
def test_log_shadow_selection_never_raises_and_logs():
    selection = ModelSelection(model_id="model-a", reason="top_scorer", score=72.5, considered=2)

    with patch("src.services.model_selector.logger") as mock_logger:
        log_shadow_selection("model-a", selection, "code_generation")

    mock_logger.info.assert_called_once()
    args = mock_logger.info.call_args.args
    assert "model_selector shadow" in args[0]
