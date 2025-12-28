"""
Text-to-Speech Tool using Chatterbox TTS.

This tool converts text to natural-sounding speech using Resemble AI's
Chatterbox TTS model family.

Features:
- Multiple models: Turbo (fast), Multilingual (23+ languages), Original (creative control)
- Zero-shot voice cloning from audio reference
- Paralinguistic tags ([laugh], [cough], [chuckle], etc.) in Turbo model
- Built-in watermarking for AI-generated audio detection
"""

import logging

from src.services.chatterbox_tts_client import (
    CHATTERBOX_MODELS,
    LANGUAGE_NAMES,
    generate_speech,
)
from src.services.tools.base import BaseTool, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class TextToSpeechTool(BaseTool):
    """Tool for converting text to speech using Chatterbox TTS.

    This tool generates natural-sounding speech from text input. It supports
    multiple models for different use cases:

    - chatterbox-turbo: Fast, low-latency, English-only with paralinguistic tags
    - chatterbox-multilingual: 23+ languages with voice cloning
    - chatterbox: English with creative control parameters

    The generated audio is returned as a base64-encoded WAV file or URL.
    """

    @classmethod
    def get_definition(cls) -> ToolDefinition:
        """Get the OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": "text_to_speech",
                "description": (
                    "Convert text to natural-sounding speech audio. "
                    "Supports multiple languages, voice cloning from audio reference, "
                    "and paralinguistic expressions like [laugh], [cough], [chuckle]. "
                    "Returns audio that can be played back to the user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "The text to convert to speech. Maximum 5000 characters. "
                                "For the turbo model, you can include paralinguistic tags "
                                "like [laugh], [cough], [chuckle], [sigh], [gasp] to add "
                                "natural expressions."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "enum": list(CHATTERBOX_MODELS.keys()),
                            "default": "chatterbox-turbo",
                            "description": (
                                "TTS model to use: "
                                "'chatterbox-turbo' (fast, English, paralinguistic tags), "
                                "'chatterbox-multilingual' (23+ languages), "
                                "'chatterbox' (English with creative control)"
                            ),
                        },
                        "language": {
                            "type": "string",
                            "default": "en",
                            "description": (
                                "Language code for multilingual model. "
                                f"Supported: {', '.join(f'{code} ({name})' for code, name in list(LANGUAGE_NAMES.items())[:10])}... "
                                "Only used with chatterbox-multilingual model."
                            ),
                        },
                        "voice_reference_url": {
                            "type": "string",
                            "description": (
                                "URL to an audio file (WAV/MP3) for zero-shot voice cloning. "
                                "The generated speech will mimic the voice in this reference. "
                                "Optional - if not provided, uses default voice."
                            ),
                        },
                        "exaggeration": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 2.0,
                            "default": 1.0,
                            "description": (
                                "Exaggeration level for creative control (0.0-2.0). "
                                "Higher values create more expressive speech. "
                                "Only used with 'chatterbox' model."
                            ),
                        },
                        "cfg_weight": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.5,
                            "description": (
                                "CFG (Classifier-Free Guidance) weight (0.0-1.0). "
                                "Controls how closely the output follows the prompt. "
                                "Only used with 'chatterbox' model."
                            ),
                        },
                    },
                    "required": ["text"],
                },
            },
        }

    async def execute(
        self,
        text: str,
        model: str = "chatterbox-turbo",
        language: str = "en",
        voice_reference_url: str | None = None,
        exaggeration: float = 1.0,
        cfg_weight: float = 0.5,
    ) -> ToolResult:
        """Execute the text-to-speech conversion.

        Args:
            text: Text to convert to speech
            model: TTS model to use
            language: Language code for multilingual model
            voice_reference_url: URL to audio file for voice cloning
            exaggeration: Exaggeration level (0.0-2.0)
            cfg_weight: CFG weight (0.0-1.0)

        Returns:
            ToolResult with audio data or error
        """
        try:
            logger.info(
                f"Generating TTS: model={model}, language={language}, "
                f"text_length={len(text)}, has_voice_ref={bool(voice_reference_url)}"
            )

            result = await generate_speech(
                text=text,
                model=model,
                voice_reference_url=voice_reference_url,
                language=language,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
            )

            return self._success(
                result={
                    "audio_url": result.get("audio_url"),
                    "audio_base64": result.get("audio_base64"),
                    "duration": result.get("duration"),
                    "format": result.get("format", "wav"),
                    "model": model,
                    "language": language if model == "chatterbox-multilingual" else "en",
                },
                text_length=len(text),
                model=model,
            )

        except ValueError as e:
            logger.warning(f"TTS validation error: {e}")
            return self._error(str(e), error_type="validation")

        except RuntimeError as e:
            logger.error(f"TTS generation error: {e}")
            return self._error(str(e), error_type="generation")

        except Exception as e:
            logger.exception(f"Unexpected TTS error: {e}")
            return self._error(
                "An unexpected error occurred during speech generation",
                error_type="unexpected",
            )
