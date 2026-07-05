"""Unit tests for category-based router filtering."""

from src.schemas.router import ModelCapabilities, RouterOptimization
from src.services.capability_gating import filter_by_category
from src.services.prompt_router import category_tags_for_optimization


def _cap(model_id: str, categories: tuple[str, ...]) -> ModelCapabilities:
    return ModelCapabilities(model_id=model_id, provider="x", categories=categories)


REGISTRY = {
    "a/cheap-fast": _cap("a/cheap-fast", ("cheapest", "fastest", "budget")),
    "b/smart-big": _cap("b/smart-big", ("smartest", "largest", "flagship")),
    "c/cheap-only": _cap("c/cheap-only", ("cheapest", "budget")),
    "d/untagged": _cap("d/untagged", ()),
}
CANDIDATES = list(REGISTRY.keys())


class TestFilterByCategory:
    def test_no_tags_returns_all(self):
        assert filter_by_category(CANDIDATES, REGISTRY, ()) == CANDIDATES

    def test_single_tag_filters(self):
        assert set(filter_by_category(CANDIDATES, REGISTRY, ("cheapest",))) == {
            "a/cheap-fast",
            "c/cheap-only",
        }

    def test_multiple_tags_require_all(self):
        # Only models with BOTH cheapest AND fastest.
        assert filter_by_category(CANDIDATES, REGISTRY, ("cheapest", "fastest")) == ["a/cheap-fast"]

    def test_sparse_tag_can_return_empty(self):
        # Caller is responsible for the empty-guard.
        assert filter_by_category(CANDIDATES, REGISTRY, ("vision",)) == []

    def test_untagged_and_unknown_models_excluded(self):
        out = filter_by_category(CANDIDATES, REGISTRY, ("smartest",))
        assert out == ["b/smart-big"]
        assert "d/untagged" not in out

    def test_unknown_model_id_skipped(self):
        assert filter_by_category(["ghost/model"], REGISTRY, ("cheapest",)) == []


class TestOptimizationMapping:
    def test_price_maps_cheapest(self):
        assert category_tags_for_optimization(RouterOptimization.PRICE) == ("cheapest",)

    def test_fast_maps_fastest(self):
        assert category_tags_for_optimization(RouterOptimization.FAST) == ("fastest",)

    def test_quality_maps_smartest(self):
        assert category_tags_for_optimization(RouterOptimization.QUALITY) == ("smartest",)

    def test_balanced_imposes_no_filter(self):
        assert category_tags_for_optimization(RouterOptimization.BALANCED) == ()
