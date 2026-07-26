"""A delisted aggregator must still work as a price book.

Models whose own provider publishes no pricing — OpenAI, Anthropic and xAI all
return catalogs without prices — can only be priced by cross-referencing an
aggregator. Delisting OpenRouter as *supply* also emptied that reference, so
Claude Opus 5 (released 2026-07-24) had no price anywhere and was filtered out
of everything we sell. Being unsellable supply and being a useful price book
are different jobs.
"""

import pytest

from src.services.pricing import pricing_lookup


@pytest.fixture(autouse=True)
def _reset_state():
    pricing_lookup._openrouter_pricing_index = None
    pricing_lookup.clear_unpriced_models()
    yield
    pricing_lookup._openrouter_pricing_index = None
    pricing_lookup.clear_unpriced_models()


class TestPriceReferenceCatalog:
    def test_reads_models_regardless_of_listing_status(self, monkeypatch):
        """The whole point: delisted rows still carry usable prices."""
        captured = {}

        def _fake(gateway_slug, include_inactive=False):
            captured["gateway_slug"] = gateway_slug
            captured["include_inactive"] = include_inactive
            return [
                {
                    "provider_model_id": "anthropic/claude-opus-5",
                    "metadata": {"pricing_raw": {"prompt": "0.000015"}},
                }
            ]

        monkeypatch.setattr("src.db.models_catalog_db.get_models_by_gateway_for_catalog", _fake)

        catalog = pricing_lookup._load_price_reference_catalog()

        assert captured["include_inactive"] is True, "delisted rows must still be read"
        assert catalog == [{"id": "anthropic/claude-opus-5", "pricing": {"prompt": "0.000015"}}]

    def test_models_without_pricing_are_skipped(self, monkeypatch):
        monkeypatch.setattr(
            "src.db.models_catalog_db.get_models_by_gateway_for_catalog",
            lambda **_k: [
                {"provider_model_id": "a/b", "metadata": {}},
                {"provider_model_id": "c/d", "metadata": {"pricing_raw": {}}},
                {"provider_model_id": "e/f", "metadata": {"pricing_raw": {"prompt": "1"}}},
            ],
        )

        assert [m["id"] for m in pricing_lookup._load_price_reference_catalog()] == ["e/f"]

    def test_fails_soft_when_the_database_is_unavailable(self, monkeypatch):
        def _boom(**_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("src.db.models_catalog_db.get_models_by_gateway_for_catalog", _boom)

        assert pricing_lookup._load_price_reference_catalog() == []

    def test_index_keys_both_full_and_base_ids(self, monkeypatch):
        monkeypatch.setattr(
            pricing_lookup,
            "_load_price_reference_catalog",
            lambda: [{"id": "anthropic/claude-opus-5", "pricing": {"prompt": "0.000015"}}],
        )
        monkeypatch.setattr(pricing_lookup, "_is_building_catalog", lambda: False)

        index = pricing_lookup._build_openrouter_pricing_index()

        assert index["anthropic/claude-opus-5"] == {"prompt": "0.000015"}
        assert index["claude-opus-5"] == {"prompt": "0.000015"}


class TestUnpricedVisibility:
    def test_dropped_models_are_recorded(self):
        pricing_lookup.record_unpriced_model("anthropic/claude-opus-5")
        pricing_lookup.record_unpriced_model("openai/gpt-9")
        pricing_lookup.record_unpriced_model("anthropic/claude-opus-5")

        assert pricing_lookup.get_unpriced_models() == [
            "anthropic/claude-opus-5",
            "openai/gpt-9",
        ]

    def test_blank_ids_are_ignored(self):
        pricing_lookup.record_unpriced_model("")
        pricing_lookup.record_unpriced_model(None)

        assert pricing_lookup.get_unpriced_models() == []

    def test_clear_resets_between_syncs(self):
        pricing_lookup.record_unpriced_model("x/y")
        pricing_lookup.clear_unpriced_models()

        assert pricing_lookup.get_unpriced_models() == []


class TestPriceReferenceProvidersConfig:
    def test_openrouter_is_a_price_reference_by_default(self, monkeypatch):
        import importlib

        from src.config import config

        monkeypatch.delenv("PRICE_REFERENCE_PROVIDERS", raising=False)
        importlib.reload(config)

        assert "openrouter" in config.Config.PRICE_REFERENCE_PROVIDERS

    def test_can_be_disabled_entirely(self, monkeypatch):
        import importlib

        from src.config import config

        monkeypatch.setenv("PRICE_REFERENCE_PROVIDERS", "")
        importlib.reload(config)

        assert config.Config.PRICE_REFERENCE_PROVIDERS == frozenset()


class TestIndexInvalidation:
    """A refreshed price book must not be shadowed by a cached index.

    The index has no TTL, so a stale copy survives until something clears it.
    Syncing the book and then a provider in the same process priced the provider
    from the old copy — claude-opus-5 came back at 0.0 that way, moments after
    its units were corrected.
    """

    def test_invalidation_clears_the_cached_index(self, monkeypatch):
        monkeypatch.setattr(
            pricing_lookup,
            "_load_price_reference_catalog",
            lambda: [{"id": "a/b", "pricing": {"prompt": "0.000005"}}],
        )
        monkeypatch.setattr(pricing_lookup, "_is_building_catalog", lambda: False)

        first = pricing_lookup._build_openrouter_pricing_index()
        assert first["a/b"] == {"prompt": "0.000005"}

        # New book, same process.
        monkeypatch.setattr(
            pricing_lookup,
            "_load_price_reference_catalog",
            lambda: [{"id": "a/b", "pricing": {"prompt": "0.000009"}}],
        )

        # Without invalidation the old value persists...
        assert pricing_lookup._build_openrouter_pricing_index()["a/b"] == {"prompt": "0.000005"}

        pricing_lookup.invalidate_openrouter_pricing_index()

        # ...and after it, the refreshed book is what gets used.
        assert pricing_lookup._build_openrouter_pricing_index()["a/b"] == {"prompt": "0.000009"}
