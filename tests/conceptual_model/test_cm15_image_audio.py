"""
CM-15 Image Generation & Audio Transcription

Tests verifying credit deduction for image generation, insufficient-credits
handling, and audio transcription response structure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# CM-15.1  Image generation deducts credits
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1501ImageGenerationDeductsCredits:
    def test_image_generation_deducts_credits(self):
        """The image generation route calculates a positive cost for images
        and the get_image_cost function returns a non-zero cost that would
        be deducted via deduct_credits."""
        from src.routes.images import get_image_cost

        # Call the actual cost function with real parameters
        total_cost, cost_per_image, *_ = get_image_cost(
            "deepinfra", "stable-diffusion-3.5-large", num_images=1, size="1024x1024"
        )
        assert total_cost > 0, "Image generation must have a positive cost for credit deduction"
        assert cost_per_image > 0, "Per-image cost must be positive"

        # Multiple images should cost more
        total_cost_2, _, *_ = get_image_cost(
            "deepinfra", "stable-diffusion-3.5-large", num_images=3, size="1024x1024"
        )
        assert total_cost_2 > total_cost, "3 images should cost more than 1"


# ---------------------------------------------------------------------------
# CM-15.2  Image generation with 0 credits returns 402
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1502ImageGenerationInsufficientCredits402:
    @pytest.mark.asyncio
    async def test_image_generation_insufficient_credits_402(self):
        """When a user has 0 credits, the image endpoint raises HTTP 402.

        Patches get_user to return a zero-credit user and verifies the
        pre-flight credit check raises a 402 HTTPException."""
        from src.routes.images import generate_images

        # Build a mock request with all required attributes
        mock_req = MagicMock()
        mock_req.prompt = "a test image"
        mock_req.model = "stable-diffusion-3.5-large"
        mock_req.n = 1
        mock_req.size = "1024x1024"
        mock_req.provider = "deepinfra"

        # User with 0 credits — should trigger 402 at pre-flight check
        zero_credit_user = {"id": 1, "credits": 0.0, "api_key": "gw_test"}

        with (
            patch("src.routes.images.get_user", return_value=zero_credit_user),
            patch(
                "src.routes.images.get_image_cost",
                return_value=(0.05, 0.05, False, 1.0),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await generate_images(mock_req, MagicMock(), api_key="gw_test_key")
            assert (
                exc_info.value.status_code == 402
            ), f"Expected 402 for insufficient credits, got {exc_info.value.status_code}"


# ---------------------------------------------------------------------------
# CM-15.3  Audio transcription returns text field
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1503AudioTranscriptionReturnsText:
    @pytest.mark.asyncio
    async def test_audio_transcription_returns_text(self):
        """The audio transcription endpoint returns a response containing
        a 'text' field with the transcribed content.

        Exercises the full create_transcription path with mocked I/O to
        verify the response structure."""
        # Use SimpleNamespace to avoid MagicMock auto-creating attributes
        # (which would cause JSON serialization errors in the response)
        from types import SimpleNamespace

        from starlette.responses import JSONResponse

        from src.routes.audio import create_transcription

        mock_whisper_response = SimpleNamespace(
            text="Hello world transcription",
            duration=5.0,
        )

        # Mock user with sufficient credits
        mock_user = {"id": 1, "credits": 100.0, "api_key": "gw_test"}

        # Mock file upload with valid content type
        mock_file = MagicMock()
        mock_file.filename = "test.mp3"
        mock_file.content_type = "audio/mpeg"
        mock_file.read = AsyncMock(return_value=b"\x00" * 1000)

        # Build mock OpenAI client with explicit chain to avoid auto-mock issues
        mock_transcriptions = MagicMock()
        mock_transcriptions.create = MagicMock(return_value=mock_whisper_response)
        mock_audio = MagicMock()
        mock_audio.transcriptions = mock_transcriptions
        mock_client = MagicMock()
        mock_client.audio = mock_audio

        # Mock the AITracer context manager
        mock_trace_ctx = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.__aenter__ = AsyncMock(return_value=mock_trace_ctx)
        mock_tracer.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.routes.audio.get_user", return_value=mock_user),
            patch(
                "src.routes.audio.get_openai_pooled_client",
                return_value=mock_client,
            ),
            patch(
                "src.routes.audio._deduct_audio_credits",
                new_callable=AsyncMock,
                return_value=99.0,
            ),
            patch("src.routes.audio.AITracer") as mock_ai_tracer,
        ):
            mock_ai_tracer.trace_inference.return_value = mock_tracer

            # Pass response_format explicitly (Form() defaults aren't resolved
            # when calling outside FastAPI's dependency injection)
            response = await create_transcription(
                file=mock_file,
                model="whisper-1",
                response_format="json",
                api_key="gw_test_key",
            )

        assert isinstance(response, JSONResponse)
        import json

        body = json.loads(response.body.decode())
        assert "text" in body, "Audio transcription response must include a 'text' field"
        assert body["text"] == "Hello world transcription"
