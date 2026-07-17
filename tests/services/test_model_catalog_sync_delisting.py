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
    result = _transform(
        {"id": "vendor/no-price-model", "name": "No Price", "pricing": {}}
    )
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
    ):
        result = sync_provider_models("vendor")

    mock_deactivate.assert_called_once_with(42)
    assert result["success"] is True
    assert result["models_delisted"] == 3
    assert result["reason"] == "provider_inactive"
    assert result["models_fetched"] == 0
    assert result["models_synced"] == 0


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
        patch(
            "src.services.model_catalog_sync.deactivate_models_by_provider"
        ) as mock_deactivate,
    ):
        result = sync_provider_models("vendor", dry_run=True)

    mock_deactivate.assert_not_called()
    assert result["success"] is True
