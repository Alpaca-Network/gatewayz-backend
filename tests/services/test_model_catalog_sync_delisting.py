"""Sync-time delisting: unpriced/unroutable models must not stay listed.

Prior to this change, `transform_normalized_model_to_db_schema` always set
`is_active: True` and unpriced models were only ever filtered at inference
time (the pricing gate in `src.security.inference_gates`). That means the
catalog itself carried thousands of models nobody could ever actually route
to or bill for — North Star §5: "No catalog breadth for its own sake."

These tests exercise the sync-time gate: a model is kept active only when it
has a priced offer (free-tier models are exempt) AND its serving provider is
routable (active). Also covers `sync_provider_models`' delisted-count
tracking (no silent caps — every delist is counted and logged).
"""

from __future__ import annotations

from unittest.mock import patch

from src.services.model_catalog_sync import (
    sync_provider_models,
    transform_normalized_model_to_db_schema,
)


def _transform(model, **kw):
    return transform_normalized_model_to_db_schema(
        model, provider_id=1, provider_slug="vendor", **kw
    )


# --------------------------------------------------------------------------- #
# transform_normalized_model_to_db_schema
# --------------------------------------------------------------------------- #


def test_unpriced_paid_model_is_delisted_at_sync():
    result = _transform({"id": "vendor/no-price-model", "name": "No Price", "pricing": {}})
    assert result is not None
    assert result["is_active"] is False
    assert result["metadata"]["delist_reason"] == "unpriced"


def test_priced_paid_model_stays_active():
    result = _transform(
        {
            "id": "vendor/priced-model",
            "name": "Priced",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
    )
    assert result is not None
    assert result["is_active"] is True
    assert "delist_reason" not in result["metadata"]


def test_free_model_stays_active_without_any_pricing():
    # Preserve existing behavior: :free models remain listable even though
    # they legitimately carry zero pricing.
    result = _transform(
        {
            "id": "vendor/free-model:free",
            "name": "Free Model",
            "is_free": True,
            "pricing": {},
        }
    )
    assert result is not None
    assert result["is_active"] is True
    assert "delist_reason" not in result["metadata"]


def test_model_with_inactive_provider_is_delisted_even_if_priced():
    result = _transform(
        {
            "id": "vendor/priced-but-unroutable",
            "name": "Priced Unroutable",
            "pricing": {"prompt": "0.000001"},
        },
        provider_active=False,
    )
    assert result is not None
    assert result["is_active"] is False
    assert result["metadata"]["delist_reason"] == "no_routable_provider"


def test_provider_active_defaults_true_for_backward_compat():
    # Existing call sites that don't pass provider_active must keep working
    # exactly as before (provider was already validated active by the caller).
    result = transform_normalized_model_to_db_schema(
        {
            "id": "vendor/legacy-call-site",
            "name": "Legacy",
            "pricing": {"prompt": "0.000001"},
        },
        provider_id=1,
        provider_slug="vendor",
    )
    assert result is not None
    assert result["is_active"] is True


# --------------------------------------------------------------------------- #
# sync_provider_models — delisted count is tracked and reported, never silent
# --------------------------------------------------------------------------- #


def _fake_models():
    return [
        {"id": "vendor/priced", "name": "Priced", "pricing": {"prompt": "0.000001"}},
        {"id": "vendor/unpriced", "name": "Unpriced", "pricing": {}},
        {
            "id": "vendor/free:free",
            "name": "Free",
            "is_free": True,
            "pricing": {},
        },
    ]


def test_sync_provider_models_counts_and_reports_delisted():
    with (
        patch(
            "src.services.model_catalog_sync.ensure_provider_exists",
            return_value={"id": 1, "slug": "vendor", "is_active": True},
        ),
        patch(
            "src.services.dynamic_provider_loader.get_fetch_models_function",
            return_value=_fake_models,
        ),
    ):
        result = sync_provider_models("vendor", dry_run=True)

    assert result["success"] is True
    assert result["models_transformed"] == 3
    # Only the unpriced paid model should be delisted; priced + free stay active.
    assert result["models_delisted"] == 1


# --------------------------------------------------------------------------- #
# sync_provider_models — inactive provider retroactively delists its models
# --------------------------------------------------------------------------- #


def test_sync_provider_models_inactive_provider_delists_previously_active_models():
    # End-to-end: an inactive provider must not be a pure no-op. Its
    # previously-synced, currently-active models must be bulk-deactivated
    # (the provider-level counterpart to the per-model "no_routable_provider"
    # delist, which an inactive provider never reaches since this early
    # return happens before the fetch/transform loop).
    with (
        patch(
            "src.services.model_catalog_sync.ensure_provider_exists",
            return_value={"id": 42, "slug": "vendor", "is_active": False},
        ),
        patch(
            "src.services.model_catalog_sync.deactivate_models_by_provider",
            return_value=3,
        ) as mock_deactivate,
        patch(
            "src.services.model_catalog_cache.invalidate_provider_catalog"
        ) as invalidate_provider,
        patch("src.services.model_catalog_cache.invalidate_unique_models") as invalidate_unique,
        patch("src.services.model_catalog_cache.invalidate_catalog_stats") as invalidate_stats,
        patch(
            "src.services.cache.catalog_response_cache.invalidate_catalog_cache"
        ) as invalidate_response,
    ):
        result = sync_provider_models("vendor")

    mock_deactivate.assert_called_once_with(42)
    invalidate_provider.assert_called_once_with("vendor", cascade=True)
    invalidate_unique.assert_called_once_with()
    invalidate_stats.assert_called_once_with()
    invalidate_response.assert_called_once_with("vendor")
    assert result["success"] is True
    assert result["models_delisted"] == 3
    assert result["reason"] == "provider_inactive"
    assert result["models_fetched"] == 0
    assert result["models_synced"] == 0


def test_batch_sync_invalidates_all_catalog_layers_after_delisting():
    inactive_result = {
        "success": True,
        "provider": "vendor",
        "models_fetched": 0,
        "models_transformed": 0,
        "models_skipped": 0,
        "models_synced": 0,
        "models_delisted": 3,
    }

    with (
        patch("src.utils.provider_filter.is_provider_enabled", return_value=True),
        patch(
            "src.services.model_catalog_sync.sync_provider_models",
            return_value=inactive_result,
        ),
        patch("src.services.model_catalog_cache.invalidate_full_catalog") as invalidate_full,
        patch("src.services.model_catalog_cache.invalidate_unique_models") as invalidate_unique,
        patch("src.services.model_catalog_cache.invalidate_catalog_stats") as invalidate_stats,
        patch(
            "src.services.cache.catalog_response_cache.invalidate_catalog_cache"
        ) as invalidate_response,
    ):
        from src.services.model_catalog_sync import sync_all_providers

        result = sync_all_providers(["vendor"])

    invalidate_full.assert_called_once_with()
    invalidate_unique.assert_called_once_with()
    invalidate_stats.assert_called_once_with()
    invalidate_response.assert_called_once_with()
    assert result["total_models_delisted"] == 3


def test_batch_sync_bootstraps_enabled_provider_missing_from_registry():
    synced_result = {
        "success": True,
        "provider": "moonshot",
        "models_fetched": 1,
        "models_transformed": 1,
        "models_skipped": 0,
        "models_synced": 1,
        "models_delisted": 0,
    }

    with (
        patch(
            "src.utils.provider_filter.get_enabled_providers",
            return_value=frozenset({"moonshot"}),
        ),
        patch("src.utils.provider_filter.is_provider_enabled", return_value=True),
        patch(
            "src.services.model_catalog_sync.sync_provider_models",
            return_value=synced_result,
        ) as sync_provider,
        patch("src.config.config.Config.MODEL_SYNC_SKIP_PROVIDERS", set()),
        patch("src.services.model_catalog_cache.invalidate_full_catalog"),
        patch("src.services.model_catalog_cache.invalidate_unique_models"),
        patch("src.services.model_catalog_cache.invalidate_catalog_stats"),
        patch("src.services.cache.catalog_response_cache.invalidate_catalog_cache"),
    ):
        from src.services.model_catalog_sync import sync_all_providers

        result = sync_all_providers()

    sync_provider.assert_called_once_with("moonshot", dry_run=False, batch_mode=True)
    assert result["providers_processed"] == 1
    assert result["total_models_synced"] == 1


def test_sync_provider_models_active_provider_does_not_bulk_delist():
    # Sanity check: the bulk-delist path must only fire for inactive
    # providers. An active provider's normal sync must never call it.
    with (
        patch(
            "src.services.model_catalog_sync.ensure_provider_exists",
            return_value={"id": 1, "slug": "vendor", "is_active": True},
        ),
        patch(
            "src.services.dynamic_provider_loader.get_fetch_models_function",
            return_value=_fake_models,
        ),
        patch("src.services.model_catalog_sync.deactivate_models_by_provider") as mock_deactivate,
    ):
        result = sync_provider_models("vendor", dry_run=True)

    mock_deactivate.assert_not_called()
    assert result["success"] is True
