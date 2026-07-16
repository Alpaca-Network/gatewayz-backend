"""
CM-15 Audio Transcription

Tests verifying audio transcription response structure.

Note: the matching image-generation credit-deduction tests (CM-15.1, CM-15.2)
were removed alongside src/routes/images.py (MVP refactor Task 7 — image
generation cut, no frontend usage).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
        mock_user = {
            "id": 1,
            "subscription_allowance": 0.0,
            "purchased_credits": 100.0,
            "api_key": "gw_test",
        }

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
                request=MagicMock(),
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
