"""
Unit tests for the lightweight price-only refresh (src/services/price_refresh.py).

All provider fetches and DB read/update helpers are mocked — these tests touch
neither the network nor the database. They verify:

  (a) a changed price triggers a pricing-only update,
  (b) an unchanged price triggers NO write,
  (c) a model not present in the DB is NOT inserted,
  (d) one failing provider does not abort the others (error isolation),
  (e) warm_caches_after_sync / full-catalog rebuild is NEVER called,
  (f) dry_run writes nothing.
"""

from unittest.mock import MagicMock

import pytest

from src.services import price_refresh


def _patch_common(monkeypatch, fetch_functions, update_mock):
    """
    Patch the symbols that _refresh_provider_prices imports lazily.

    - PROVIDER_FETCH_FUNCTIONS / ensure_provider_exists live in model_catalog_sync
    - get_models_pricing_by_provider / update_model_pricing_only live in
      models_catalog_db

    Because the service does function-local imports, patching the source modules
    is sufficient. We also patch get_provider_format to a stable per-token value
    so normalization is deterministic in tests.
    """
    import src.services.model_catalog_sync as mcs
    import src.db.models_catalog_db as mdb

    monkeypatch.setattr(mcs, "PROVIDER_FETCH_FUNCTIONS", fetch_functions, raising=True)
    monkeypatch.setattr(
        mcs,
        "ensure_provider_exists",
        lambda slug: {"id": 1, "slug": slug, "is_active": True},
        raising=True,
    )
    # Treat all providers as already per-token so fetched prices map 1:1 to stored.
    monkeypatch.setattr(
        "src.utils.pricing_normalization.get_provider_format",
        lambda slug: "per_token",
        raising=True,
    )
    monkeypatch.setattr(mdb, "update_model_pricing_only", update_mock, raising=True)


@pytest.mark.unit
class TestRefreshAllPrices:
    def test_changed_price_updates_pricing_only(self, monkeypatch):
        """A fetched price that differs from the stored price triggers an update
        through the pricing-only helper (which only writes pricing columns)."""
        import src.db.models_catalog_db as mdb

        fetch = MagicMock(
            return_value=[
                {"id": "vendor/model-a", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
            ]
        )
        # Stored price differs (prompt was 0.000001)
        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [
                {
                    "id": 101,
                    "provider_model_id": "vendor/model-a",
                    "metadata": {"pricing_raw": {"prompt": "0.000001", "completion": "0.000004"}},
                }
            ],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(monkeypatch, {"vendorx": fetch}, update_mock)

        result = price_refresh.refresh_all_prices(dry_run=False)

        assert result["success"] is True
        assert result["prices_updated"] == 1
        assert result["prices_unchanged"] == 0
        assert result["providers_failed"] == 0

        update_mock.assert_called_once()
        kwargs = update_mock.call_args.kwargs
        assert kwargs["model_id"] == 101
        # New per-token pricing is what gets written
        assert kwargs["pricing_raw"]["prompt"] == "0.000002"
        # existing metadata is passed through so non-pricing keys are preserved
        assert "pricing_raw" in kwargs["existing_metadata"]

    def test_unchanged_price_no_write(self, monkeypatch):
        """Identical fetched price (even with different string formatting) writes nothing."""
        import src.db.models_catalog_db as mdb

        fetch = MagicMock(
            return_value=[
                {"id": "vendor/model-a", "pricing": {"prompt": "0.0000010", "completion": "0.000004"}}
            ]
        )
        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [
                {
                    "id": 101,
                    "provider_model_id": "vendor/model-a",
                    "metadata": {"pricing_raw": {"prompt": "0.000001", "completion": "0.000004"}},
                }
            ],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(monkeypatch, {"vendorx": fetch}, update_mock)

        result = price_refresh.refresh_all_prices(dry_run=False)

        assert result["prices_updated"] == 0
        assert result["prices_unchanged"] == 1
        update_mock.assert_not_called()

    def test_model_not_in_db_not_inserted(self, monkeypatch):
        """A fetched model with no DB row is skipped, never inserted/updated."""
        import src.db.models_catalog_db as mdb

        fetch = MagicMock(
            return_value=[
                {"id": "vendor/brand-new", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
            ]
        )
        # DB has no matching model
        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(monkeypatch, {"vendorx": fetch}, update_mock)

        result = price_refresh.refresh_all_prices(dry_run=False)

        assert result["prices_updated"] == 0
        assert result["prices_unchanged"] == 0
        update_mock.assert_not_called()

    def test_one_provider_failure_isolated(self, monkeypatch):
        """A provider whose fetch raises is recorded as failed; others still run."""
        import src.db.models_catalog_db as mdb

        good_fetch = MagicMock(
            return_value=[
                {"id": "vendor/model-a", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
            ]
        )

        def bad_fetch():
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [
                {
                    "id": 101,
                    "provider_model_id": "vendor/model-a",
                    "metadata": {"pricing_raw": {"prompt": "0.000001"}},
                }
            ],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(
            monkeypatch,
            {"bad": bad_fetch, "good": good_fetch},
            update_mock,
        )

        result = price_refresh.refresh_all_prices(dry_run=False)

        # The bad provider failed but the good one was still processed and updated.
        assert result["success"] is False
        assert result["providers_failed"] == 1
        assert result["providers_checked"] == 1
        assert result["prices_updated"] == 1
        assert any(e["provider"] == "bad" for e in result["errors"])
        update_mock.assert_called_once()

    def test_does_not_warm_or_rebuild_catalog(self, monkeypatch):
        """The price refresh must NEVER warm caches or rebuild the full catalog.

        We deliberately do NOT import scheduled_sync here (it pulls in apscheduler);
        the unit under test is price_refresh, which never references the cache-warming
        code. We spy on the full-catalog rebuild and cache functions to prove they
        stay untouched.
        """
        import src.db.models_catalog_db as mdb

        fetch = MagicMock(
            return_value=[
                {"id": "vendor/model-a", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
            ]
        )
        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [
                {
                    "id": 101,
                    "provider_model_id": "vendor/model-a",
                    "metadata": {"pricing_raw": {"prompt": "0.000001"}},
                }
            ],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(monkeypatch, {"vendorx": fetch}, update_mock)

        # Spy on the full-catalog rebuild path — it must not be invoked.
        rebuild_spy = MagicMock(return_value=[])
        monkeypatch.setattr(mdb, "get_all_models_for_catalog", rebuild_spy, raising=True)

        # Spy on full-catalog cache invalidation — also must not be invoked.
        import src.services.cache.model_catalog_cache as catalog_cache

        invalidate_spy = MagicMock()
        monkeypatch.setattr(
            catalog_cache, "invalidate_full_catalog", invalidate_spy, raising=True
        )

        result = price_refresh.refresh_all_prices(dry_run=False)

        assert result["prices_updated"] == 1
        rebuild_spy.assert_not_called()
        invalidate_spy.assert_not_called()

    def test_dry_run_writes_nothing(self, monkeypatch):
        """dry_run computes what WOULD change but performs no write."""
        import src.db.models_catalog_db as mdb

        fetch = MagicMock(
            return_value=[
                {"id": "vendor/model-a", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
            ]
        )
        monkeypatch.setattr(
            mdb,
            "get_models_pricing_by_provider",
            lambda provider_id: [
                {
                    "id": 101,
                    "provider_model_id": "vendor/model-a",
                    "metadata": {"pricing_raw": {"prompt": "0.000001"}},
                }
            ],
            raising=True,
        )
        update_mock = MagicMock(return_value=True)
        _patch_common(monkeypatch, {"vendorx": fetch}, update_mock)

        result = price_refresh.refresh_all_prices(dry_run=True)

        assert result["dry_run"] is True
        # It still reports the would-be change as an update count...
        assert result["prices_updated"] == 1
        # ...but never actually writes.
        update_mock.assert_not_called()
