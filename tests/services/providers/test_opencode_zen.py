"""OpenCode Zen provider: catalog filtering, routing, pricing, aggregator tie-break."""

from unittest.mock import Mock, patch

import pytest

from src.config import Config


class TestZenCatalog:
    @pytest.mark.parametrize("model_id", ["big-pickle", "beam-free", "deepseek-v4.1-flash-free"])
    def test_opencode_client_only_free_models_are_dropped(self, model_id):
        from src.services.providers.opencode_catalog import normalize_opencode_model

        assert normalize_opencode_model({"id": model_id}) is None

    def test_paid_model_is_namespaced_and_attributed(self):
        from src.services.providers.opencode_catalog import normalize_opencode_model

        with patch(
            "src.services.pricing_lookup.enrich_model_with_pricing", side_effect=lambda m, _g: m
        ):
            model = normalize_opencode_model({"id": "claude-opus-5-5", "owned_by": "anthropic"})

        assert model["id"] == "opencode/claude-opus-5-5"
        assert model["provider_slug"] == "opencode"
        assert model["source_gateway"] == "opencode"

    def test_fetch_without_key_returns_none(self, monkeypatch):
        from src.services.providers.opencode_catalog import fetch_models_from_opencode

        monkeypatch.setattr(Config, "OPENCODE_API_KEY", None, raising=False)
        assert fetch_models_from_opencode() is None

    def test_fetch_filters_and_caches(self, monkeypatch):
        from src.services.providers import opencode_catalog

        monkeypatch.setattr(Config, "OPENCODE_API_KEY", "sk-fake", raising=False)
        response = Mock()
        response.json.return_value = {"data": [{"id": "gpt-5.5"}, {"id": "big-pickle"}]}
        response.raise_for_status.return_value = None

        with (
            patch.object(opencode_catalog.httpx, "get", return_value=response) as get,
            patch.object(opencode_catalog, "cache_gateway_catalog") as cache,
            patch(
                "src.services.pricing_lookup.enrich_model_with_pricing",
                side_effect=lambda m, _g: m,
            ),
            patch("src.services.gateway_health_service.clear_gateway_error"),
        ):
            models = opencode_catalog.fetch_models_from_opencode()

        assert [m["id"] for m in models] == ["opencode/gpt-5.5"]
        assert get.call_args[1]["headers"]["Authorization"] == "Bearer sk-fake"
        cache.assert_called_once()


class TestZenRouting:
    def test_prefix_detects_opencode(self):
        from src.services.model_transformations import detect_provider_from_model_id

        assert detect_provider_from_model_id("opencode/claude-opus-5-5") == "opencode"

    def test_adapter_strips_prefix_before_upstream_call(self):
        from src.services.providers.adapter_configs import ADAPTERS

        assert ADAPTERS["opencode"]._resolve_model("opencode/gpt-5.5") == "gpt-5.5"


class TestZenPricing:
    def test_published_price_comes_from_manual_book(self):
        from src.services.pricing.pricing_lookup import get_model_pricing

        with patch("src.utils.pricing_normalization.get_provider_format", return_value="per_1m"):
            pricing = get_model_pricing("opencode", "opencode/claude-opus-5-5")

        assert pricing is not None
        assert float(pricing["prompt"]) == pytest.approx(4.00 / 1_000_000)
        assert float(pricing["completion"]) == pytest.approx(20.00 / 1_000_000)

    def test_promo_models_are_priced_at_undiscounted_list(self):
        from src.services.pricing.pricing_lookup import load_manual_pricing

        # Zen sells gemini-4-carbon at 50% off until 2026-11-30; the book keeps
        # the list price so billing never drops below cost when the promo ends.
        entry = load_manual_pricing()["opencode"]["opencode/gemini-4-carbon"]
        assert entry["prompt"] == "4.00"
        assert entry["completion"] == "20.00"

    def test_no_free_models_in_price_book(self):
        from src.services.pricing.pricing_lookup import load_manual_pricing

        book = load_manual_pricing()["opencode"]
        assert book
        assert not [k for k in book if k.endswith("-free") or k.endswith("big-pickle")]
        for model_id, entry in book.items():
            assert float(entry["prompt"]) > 0, model_id
            assert float(entry["completion"]) > 0, model_id


class TestAggregatorTieBreak:
    def _candidate(self, model_id, slug):
        return {"id": model_id, "providers": {"slug": slug}}

    @pytest.mark.parametrize("aggregator", ["openrouter", "opencode"])
    def test_direct_provider_wins_a_price_tie(self, aggregator):
        from src.db import models_catalog_db as db

        candidates = [
            self._candidate(f"{aggregator}/kimi-k3", aggregator),
            self._candidate("moonshot/kimi-k3", "moonshot"),
        ]
        same_price = {"in": 0.000003, "out": 0.000015}
        with patch.object(
            db,
            "get_model_pricing_by_ids",
            return_value={c["id"]: same_price for c in candidates},
        ):
            ranked = db._filter_and_sort_by_cost(None, candidates, cost_ceiling=1e9)

        assert [c["id"] for c in ranked] == ["moonshot/kimi-k3", f"{aggregator}/kimi-k3"]

    def test_cheaper_aggregator_still_wins_on_price(self):
        from src.db import models_catalog_db as db

        candidates = [
            self._candidate("moonshot/kimi-k3", "moonshot"),
            self._candidate("opencode/kimi-k3", "opencode"),
        ]
        prices = {
            "moonshot/kimi-k3": {"in": 0.000004, "out": 0.000016},
            "opencode/kimi-k3": {"in": 0.000003, "out": 0.000015},
        }
        with patch.object(db, "get_model_pricing_by_ids", return_value=prices):
            ranked = db._filter_and_sort_by_cost(None, candidates, cost_ceiling=1e9)

        assert ranked[0]["id"] == "opencode/kimi-k3"
