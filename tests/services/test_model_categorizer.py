"""Unit tests for src/services/model_categorizer.py (pure engine)."""

from src.services.model_categorizer import (
    TIER_CATEGORIES,
    CategoryRule,
    ModelSignals,
    compute_categories,
    signals_from_model_row,
)


def _tiers(tags: list[str]) -> list[str]:
    return [t for t in tags if t in TIER_CATEGORIES]


# --------------------------------------------------------------------------- #
# Tier invariant
# --------------------------------------------------------------------------- #
def test_exactly_one_tier_always_assigned():
    for q in (None, 0, 55, 70, 89, 90, 100):
        sig = ModelSignals(quality_overall=q)
        assert len(_tiers(compute_categories(sig))) == 1, f"quality={q}"


def test_unknown_quality_defaults_to_budget():
    assert "budget" in compute_categories(ModelSignals())


def test_empty_signals_yields_only_budget():
    assert compute_categories(ModelSignals()) == ["budget"]


def test_tier_bands():
    assert "flagship" in compute_categories(ModelSignals(quality_overall=90))
    assert "flagship" in compute_categories(ModelSignals(quality_overall=99))
    assert "mid" in compute_categories(ModelSignals(quality_overall=70))
    assert "mid" in compute_categories(ModelSignals(quality_overall=89.9))
    assert "budget" in compute_categories(ModelSignals(quality_overall=69.9))


# --------------------------------------------------------------------------- #
# fastest (latency_tier <= 2)
# --------------------------------------------------------------------------- #
def test_fastest_boundary():
    assert "fastest" in compute_categories(ModelSignals(latency_tier=1))
    assert "fastest" in compute_categories(ModelSignals(latency_tier=2))
    assert "fastest" not in compute_categories(ModelSignals(latency_tier=3))
    assert "fastest" not in compute_categories(ModelSignals())  # unknown → not applied


# --------------------------------------------------------------------------- #
# largest / long-context (context_length gte 200k / 128k)
# --------------------------------------------------------------------------- #
def test_context_tags():
    small = compute_categories(ModelSignals(context_length=8192))
    assert "largest" not in small and "long-context" not in small

    mid = compute_categories(ModelSignals(context_length=128_000))
    assert "long-context" in mid and "largest" not in mid

    big = compute_categories(ModelSignals(context_length=200_000))
    assert "largest" in big and "long-context" in big  # largest ⊂ long-context


# --------------------------------------------------------------------------- #
# cheapest (blended $/1M <= 0.50)
# --------------------------------------------------------------------------- #
def test_cheapest_blended_price():
    # gpt-4o-mini-ish: $0.15 in / $0.60 out per 1M => per-token 0.15e-6 / 0.60e-6
    cheap = ModelSignals(
        input_price_per_token=0.15e-6, output_price_per_token=0.60e-6
    )
    # blended = (0.15*0.25 + 0.60*0.75) = 0.4875 $/1M <= 0.50
    assert "cheapest" in compute_categories(cheap)

    pricey = ModelSignals(
        input_price_per_token=3e-6, output_price_per_token=15e-6
    )  # blended = 11.25 $/1M
    assert "cheapest" not in compute_categories(pricey)

    assert "cheapest" not in compute_categories(ModelSignals())  # no price → skip


def test_free_model_is_cheapest_and_free():
    sig = ModelSignals(
        is_free=True, input_price_per_token=0.0, output_price_per_token=0.0
    )
    tags = compute_categories(sig)
    assert "free" in tags and "cheapest" in tags


# --------------------------------------------------------------------------- #
# capability flags
# --------------------------------------------------------------------------- #
def test_reasoning_and_free_flags():
    assert "reasoning" in compute_categories(ModelSignals(is_reasoning=True))
    assert "reasoning" not in compute_categories(ModelSignals(is_reasoning=False))
    assert "free" in compute_categories(ModelSignals(is_free=True))


def test_vision_from_modality():
    assert "vision" in compute_categories(ModelSignals(modality="text+image"))
    assert "vision" in compute_categories(ModelSignals(modality="multimodal"))
    assert "vision" not in compute_categories(ModelSignals(modality="text"))
    assert "vision" not in compute_categories(ModelSignals())


# --------------------------------------------------------------------------- #
# smartest / coding (quality thresholds)
# --------------------------------------------------------------------------- #
def test_smartest_and_coding():
    strong = ModelSignals(quality_overall=90, quality_code=88)
    tags = compute_categories(strong)
    assert "smartest" in tags and "coding" in tags and "flagship" in tags

    weak = ModelSignals(quality_overall=60, quality_code=60)
    tags = compute_categories(weak)
    assert "smartest" not in tags and "coding" not in tags


# --------------------------------------------------------------------------- #
# balanced (value_ratio = quality / blended $/1M >= 200)
# --------------------------------------------------------------------------- #
def test_balanced_value_ratio():
    # quality 85, blended ~0.4875 $/1M => ratio ~174 (< 200) → not balanced
    modest = ModelSignals(
        quality_overall=85,
        input_price_per_token=0.15e-6,
        output_price_per_token=0.60e-6,
    )
    assert "balanced" not in compute_categories(modest)

    # quality 90, blended 0.1 $/1M => ratio 900 → balanced
    great_value = ModelSignals(
        quality_overall=90,
        input_price_per_token=0.05e-6,
        output_price_per_token=0.1166667e-6,
    )
    assert "balanced" in compute_categories(great_value)

    # No price → no ratio → not balanced (never guessed)
    assert "balanced" not in compute_categories(ModelSignals(quality_overall=95))


# --------------------------------------------------------------------------- #
# rules honoring: disabled rule & custom threshold
# --------------------------------------------------------------------------- #
def test_disabled_rule_excluded():
    rules = [
        CategoryRule("fastest", "latency_tier", "lte", 2, enabled=False),
        CategoryRule("budget", "quality_band", "band", 0, 70),
    ]
    assert "fastest" not in compute_categories(ModelSignals(latency_tier=1), rules)


def test_custom_threshold_via_rules():
    rules = [
        CategoryRule("largest", "context_length", "gte", 1_000_000),
        CategoryRule("budget", "quality_band", "band", 0, 70),
    ]
    assert "largest" not in compute_categories(
        ModelSignals(context_length=200_000), rules
    )
    assert "largest" in compute_categories(
        ModelSignals(context_length=1_000_000), rules
    )


# --------------------------------------------------------------------------- #
# adapter
# --------------------------------------------------------------------------- #
def test_signals_from_model_row_reads_architecture_modality():
    row = {"context_length": 200_000, "architecture": {"modality": "text+image"}}
    sig = signals_from_model_row(row)
    assert sig.context_length == 200_000
    assert sig.modality == "text+image"
    tags = compute_categories(sig)
    assert "largest" in tags and "vision" in tags


def test_signals_from_model_row_coerces_and_defaults():
    sig = signals_from_model_row({"context_length": "8192", "is_reasoning": True})
    assert sig.context_length == 8192
    assert sig.is_reasoning is True
    assert sig.latency_tier is None
