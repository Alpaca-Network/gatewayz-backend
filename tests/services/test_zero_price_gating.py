"""Tests for the is_free-aware zero-price admission hardening.

Two coordinated changes are covered:
  1. model_has_pricing() treats a pricing row with both prompt and completion = 0
     as unpriced (would otherwise let a paid model be served for free).
  2. enforce_model_pricing_gate() exempts genuinely-free models (`:free` suffix or
     DB is_free flag) BEFORE the pricing check, so #1 never rejects a legit free
     model.

All lookups are mocked — no DB.
"""

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from src.security.inference_gates import enforce_model_pricing_gate
from src.services.pricing.pricing import model_has_pricing


def _pricing(prompt, completion, source="database", found=True):
    return {"prompt": prompt, "completion": completion, "source": source, "found": found}


# --------------------------------------------------------------------------
# model_has_pricing — zero-price rejection
# --------------------------------------------------------------------------

def test_rejects_zero_priced_row():
    with patch("src.services.pricing.pricing.get_model_pricing", return_value=_pricing(0.0, 0.0)):
        assert model_has_pricing("provider/paid-model") is False


def test_accepts_real_priced_row():
    with patch(
        "src.services.pricing.pricing.get_model_pricing",
        return_value=_pricing(0.000001, 0.000002),
    ):
        assert model_has_pricing("provider/paid-model") is True


def test_accepts_output_only_priced_row():
    # prompt 0 but completion > 0 is still real pricing (some models bill output only)
    with patch(
        "src.services.pricing.pricing.get_model_pricing",
        return_value=_pricing(0.0, 0.000002),
    ):
        assert model_has_pricing("provider/paid-model") is True


def test_rejects_default_source():
    with patch(
        "src.services.pricing.pricing.get_model_pricing",
        return_value=_pricing(0.00002, 0.00002, source="default"),
    ):
        assert model_has_pricing("provider/paid-model") is False


# --------------------------------------------------------------------------
# enforce_model_pricing_gate — free-model exemption
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gate_exempts_free_model_even_when_unpriced():
    # A free model (is_free=True) with NO real pricing must still be allowed
    # through — and model_has_pricing must not even be consulted.
    with patch("src.security.inference_gates.Config.REQUIRE_MODEL_PRICING", True), patch(
        "src.services.cache.model_capabilities_cache.is_free_model", return_value=True
    ), patch("src.services.pricing.model_has_pricing", return_value=False) as mhp:
        await enforce_model_pricing_gate("provider/free-model")  # no raise
        mhp.assert_not_called()


@pytest.mark.asyncio
async def test_gate_rejects_paid_unpriced_model():
    with patch("src.security.inference_gates.Config.REQUIRE_MODEL_PRICING", True), patch(
        "src.services.cache.model_capabilities_cache.is_free_model", return_value=False
    ), patch("src.services.pricing.model_has_pricing", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await enforce_model_pricing_gate("provider/paid-model")
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_gate_allows_paid_priced_model():
    with patch("src.security.inference_gates.Config.REQUIRE_MODEL_PRICING", True), patch(
        "src.services.cache.model_capabilities_cache.is_free_model", return_value=False
    ), patch("src.services.pricing.model_has_pricing", return_value=True):
        await enforce_model_pricing_gate("provider/paid-model")  # no raise


@pytest.mark.asyncio
async def test_gate_noop_when_requirement_disabled():
    with patch("src.security.inference_gates.Config.REQUIRE_MODEL_PRICING", False):
        await enforce_model_pricing_gate("anything/at-all")  # no raise
