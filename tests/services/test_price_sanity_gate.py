"""Refuse to store an implausible price.

A unit error is silent — nothing throws, no request fails, the gateway simply
bills a millionth of cost. It also self-perpetuates: the database is tier 1 of
the pricing lookup, so once a corrupt value lands it gets read straight back and
no later sync can heal it. claude-opus-5 sat at 5E-12 for exactly that reason.

North Star §4.2 asks for the ingested price to be sanity-gated. This is that
gate, and the numbers below are the point of the test.
"""

from decimal import Decimal

import pytest

from src.services.model_catalog_sync import transform_normalized_model_to_db_schema


def _model(prompt: str, completion: str = "0.00001", **kw):
    base = {
        "id": "anthropic/claude-opus-5",
        "name": "Claude Opus 5",
        "pricing": {"prompt": prompt, "completion": completion, "image": "0", "request": "0"},
        # Marks the price as already per-token so the transform does not
        # normalise it again; the gate is what we are exercising here.
        "pricing_source": "database",
        "source_gateway": "anthropic",
        "context_length": 200000,
    }
    base.update(kw)
    return base


def _stored(model):
    db = transform_normalized_model_to_db_schema(model, 1, "anthropic", provider_active=True)
    return (db.get("metadata") or {}).get("pricing_raw") or {}


class TestImplausiblePricesAreRejected:
    @pytest.mark.parametrize("bad", ["5e-12", "5e-18", "0.000000000001"])
    def test_conversion_artefacts_are_not_stored(self, bad):
        """5e-12 is what a $5/Mtok model becomes after one stray ÷1e6."""
        stored = _stored(_model(bad))

        assert stored.get("prompt") in (None, "0", "0.0", ""), stored

    def test_a_real_price_passes_untouched(self):
        """$5 per 1M tokens = 5e-6 per token — the correct value for Opus 5."""
        stored = _stored(_model("0.000005", "0.000025"))

        assert Decimal(stored["prompt"]) == Decimal("0.000005")
        assert Decimal(stored["completion"]) == Decimal("0.000025")

    def test_the_cheapest_plausible_models_still_pass(self):
        """Guard against a floor set so high it rejects genuinely cheap models.

        $0.01 per 1M tokens is 1e-8 per token — cheaper than anything on the
        market and still comfortably above the floor.
        """
        stored = _stored(_model("0.00000001"))

        assert Decimal(stored["prompt"]) == Decimal("0.00000001")

    def test_zero_is_left_alone(self):
        """Free models legitimately price at zero (§4.3 free-tier routing)."""
        stored = _stored(_model("0", "0"))

        assert Decimal(stored.get("prompt", "0")) == Decimal("0")

    def test_one_bad_field_does_not_discard_the_others(self):
        stored = _stored(_model("5e-12", "0.000025"))

        assert Decimal(stored["completion"]) == Decimal("0.000025")
