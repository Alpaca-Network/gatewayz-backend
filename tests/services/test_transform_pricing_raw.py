"""Regression tests for the pricing_raw persistence fix.

transform_normalized_model_to_db_schema previously computed normalized per-token
pricing and then discarded it (it was never written to metadata), so the full
sync never populated model_pricing / metadata.pricing_raw and billing fell back
to manual_pricing.json. These verify the computed pricing now lands in
metadata.pricing_raw in the per-token shape the billing read path expects.

get_provider_format is patched to PER_TOKEN so the input values pass through
unnormalized and the assertions are deterministic (no DB/registry dependency).
"""

from decimal import Decimal
from unittest.mock import patch

from src.services.model_catalog_sync import transform_normalized_model_to_db_schema
from src.utils.pricing_normalization import PricingFormat


def _transform(model):
    with patch(
        "src.services.model_catalog_sync.get_provider_format",
        return_value=PricingFormat.PER_TOKEN,
    ):
        return transform_normalized_model_to_db_schema(model, provider_id=1, provider_slug="vendor")


def test_pricing_raw_is_persisted():
    result = _transform(
        {
            "id": "vendor/model-x",
            "name": "Model X",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
    )
    assert result is not None
    pricing_raw = result["metadata"]["pricing_raw"]
    assert Decimal(pricing_raw["prompt"]) == Decimal("0.000001")
    assert Decimal(pricing_raw["completion"]) == Decimal("0.000002")


def test_unknown_pricing_fields_are_omitted():
    # Only prompt provided -> completion/image/request (None) must NOT appear,
    # so a known price is never overwritten with a guessed zero downstream.
    result = _transform({"id": "vendor/model-y", "name": "Y", "pricing": {"prompt": "0.000001"}})
    pricing_raw = result["metadata"]["pricing_raw"]
    assert "prompt" in pricing_raw
    assert "completion" not in pricing_raw
    assert "image" not in pricing_raw
    assert "request" not in pricing_raw


def test_free_model_zeroes_pricing_raw():
    result = _transform(
        {
            "id": "vendor/z:free",
            "name": "Z",
            "is_free": True,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
    )
    pricing_raw = result["metadata"]["pricing_raw"]
    assert Decimal(pricing_raw["prompt"]) == Decimal("0")
    assert Decimal(pricing_raw["completion"]) == Decimal("0")


def test_no_pricing_means_no_pricing_raw_key():
    # A model with no pricing at all must not get an empty pricing_raw (which would
    # be meaningless) — the key is simply absent.
    result = _transform({"id": "vendor/model-nop", "name": "NoPrice", "pricing": {}})
    assert "pricing_raw" not in result["metadata"]
