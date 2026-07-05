"""Tests for cost-plus markup on image/audio pricing + the get_image_cost arity fix.

Image and audio charges previously skipped PRICING_MARKUP (no margin, unlike chat).
These verify the markup is now applied and that get_image_cost returns a 4-tuple on
both its config (Tier 1) and fallback (Tier 2) paths.
"""

from unittest.mock import patch

from src.routes.audio import get_audio_cost
from src.routes.images import get_image_cost

MARKUP = 2.0  # use a distinctive factor so markup application is unambiguous


# --------------------------------------------------------------------------
# get_image_cost — markup + 4-tuple arity
# --------------------------------------------------------------------------


def test_image_config_path_applies_markup_and_returns_4_tuple():
    # Tier 1: manual_pricing.json hit. Previously returned a 3-tuple (a bug,
    # since callers unpack 4). Now must return 4 values, markup applied.
    with (
        patch("src.routes.images.Config.PRICING_MARKUP", MARKUP),
        patch("src.routes.images.get_image_pricing", return_value=(0.04, False)),
    ):
        result = get_image_cost("deepinfra", "some-model", num_images=2)
        assert len(result) == 4
        total, per_image, is_fallback, res_mult = result
        assert per_image == 0.04 * MARKUP
        assert total == 0.04 * MARKUP * 2
        assert is_fallback is False
        assert res_mult == 1.0


def test_image_fallback_path_applies_markup():
    # Tier 2+: hardcoded fallback (get_image_pricing returns None).
    with (
        patch("src.routes.images.Config.PRICING_MARKUP", MARKUP),
        patch("src.routes.images.get_image_pricing", return_value=None),
    ):
        total, per_image, is_fallback, res_mult = get_image_cost(
            "fal", "flux-pro", num_images=1
        )  # flux-pro = 0.05 in the hardcoded dict, size=None -> res mult 1.0
        assert per_image == 0.05 * 1.0 * MARKUP
        assert total == 0.05 * 1.0 * MARKUP
        assert is_fallback is False


def test_image_unknown_provider_applies_markup():
    with (
        patch("src.routes.images.Config.PRICING_MARKUP", MARKUP),
        patch("src.routes.images.get_image_pricing", return_value=None),
    ):
        total, per_image, is_fallback, _ = get_image_cost("nobody", "x", num_images=1)
        # UNKNOWN_PROVIDER_DEFAULT_COST = 0.05
        assert per_image == 0.05 * MARKUP
        assert is_fallback is True


# --------------------------------------------------------------------------
# get_audio_cost — markup
# --------------------------------------------------------------------------


def test_audio_known_model_applies_markup():
    with patch("src.routes.audio.Config.PRICING_MARKUP", MARKUP):
        total, per_min, is_fallback = get_audio_cost("whisper-1", 3.0)
        # whisper-1 = 0.006/min
        assert per_min == 0.006 * MARKUP
        assert total == 0.006 * MARKUP * 3.0
        assert is_fallback is False


def test_audio_unknown_model_applies_markup():
    with patch("src.routes.audio.Config.PRICING_MARKUP", MARKUP):
        total, per_min, is_fallback = get_audio_cost("totally-unknown-model", 2.0)
        # AUDIO_COST_PER_MINUTE has a "default" key (0.006), so an unknown model
        # uses that (the 0.01 UNKNOWN constant is only reached if no "default").
        assert per_min == 0.006 * MARKUP
        assert total == 0.006 * MARKUP * 2.0
        assert is_fallback is True
