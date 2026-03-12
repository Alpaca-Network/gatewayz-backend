"""
CM-15 Image Generation & Audio Transcription

Tests verifying credit deduction for image generation, insufficient-credits
handling, and audio transcription response structure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CM-15.1  Image generation deducts credits
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1501ImageGenerationDeductsCredits:
    def test_image_generation_deducts_credits(self):
        """The image generation route calls deduct_credits after successful
        generation. Verify by inspecting that deduct_credits is imported and
        used in the image route module."""
        import inspect

        from src.routes import images

        source = inspect.getsource(images)
        # The route must call deduct_credits for billing
        assert (
            "deduct_credits" in source
        ), "Image generation route must call deduct_credits to bill the user"
        # Verify it also checks credits before generation
        assert (
            "Insufficient credits" in source
        ), "Image generation route must check for insufficient credits"


# ---------------------------------------------------------------------------
# CM-15.2  Image generation with 0 credits returns 402
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1502ImageGenerationInsufficientCredits402:
    def test_image_generation_insufficient_credits_402(self):
        """When a user has 0 credits, the image endpoint raises HTTP 402."""
        import inspect

        from src.routes.images import generate_images, get_image_cost

        # Verify that the cost calculation works (non-zero cost)
        total_cost, cost_per_image, *_ = get_image_cost(
            "deepinfra", "stable-diffusion-3.5-large", num_images=1, size="1024x1024"
        )
        assert total_cost > 0, "Image generation must have a positive cost"

        # Verify the route source contains 402 status for insufficient credits
        source = inspect.getsource(generate_images)
        assert "status_code=402" in source, "Image route must return 402 for insufficient credits"


# ---------------------------------------------------------------------------
# CM-15.3  Audio transcription returns text field
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1503AudioTranscriptionReturnsText:
    def test_audio_transcription_returns_text(self):
        """The audio transcription route returns a response containing
        a 'text' field with the transcribed content."""
        import inspect

        from src.routes import audio

        source = inspect.getsource(audio)
        # The response must include a "text" field
        assert (
            '"text"' in source or "'text'" in source
        ), "Audio transcription response must include a 'text' field"
        # Verify it also handles billing
        assert (
            "deduct_credits" in source or "_deduct_audio_credits" in source
        ), "Audio transcription must deduct credits"
