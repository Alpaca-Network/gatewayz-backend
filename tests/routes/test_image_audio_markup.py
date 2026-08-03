"""Tests for cost-plus markup on audio pricing.

Audio charges previously skipped PRICING_MARKUP (no margin, unlike chat).
These verify the markup is now applied.

Note: the matching image-pricing tests were removed alongside src/routes/images.py
(MVP refactor Task 7 — image generation cut, no frontend usage).
"""

from unittest.mock import patch

from src.routes.audio import get_audio_cost

MARKUP = 2.0  # use a distinctive factor so markup application is unambiguous


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
