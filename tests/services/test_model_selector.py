"""Tests for src.services.model_selector (gatewayz-backend#2214)."""

from unittest.mock import patch

from src.services.model_selector import (
    ModelSelection,
    _cost_score,
    _hash_for_selection,
    _quality_score,
    log_shadow_selection,
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


def test_hash_for_selection_is_deterministic():
    a = _hash_for_selection("conv-123", "code_generation")
    b = _hash_for_selection("conv-123", "code_generation")
    assert a == b


def test_hash_for_selection_varies_by_task_type():
    a = _hash_for_selection("conv-123", "code_generation")
    b = _hash_for_selection("conv-123", "creative_writing")
    assert a != b


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
# log_shadow_selection
# --------------------------------------------------------------------------- #
def test_log_shadow_selection_never_raises_and_logs():
    selection = ModelSelection(model_id="model-a", reason="top_scorer", score=72.5, considered=2)

    with patch("src.services.model_selector.logger") as mock_logger:
        log_shadow_selection("model-a", selection, "code_generation")

    mock_logger.info.assert_called_once()
    args = mock_logger.info.call_args.args
    assert "model_selector shadow" in args[0]
