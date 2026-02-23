"""Audio transcription routes using Whisper API.

This module provides endpoints for audio transcription using OpenAI Whisper
or compatible services (Simplismart). Supports various audio formats and
provides options for language hints, prompt context, and output formatting.
"""

import base64
import logging
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.security.deps import get_optional_api_key
from src.services.connection_pool import get_openai_pooled_client
from src.utils.ai_tracing import AITracer, AIRequestType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

# Supported audio formats for Whisper
SUPPORTED_FORMATS = {
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/mpga": ".mpga",
    "audio/mp4": ".mp4",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
}

# Maximum file size (25MB - Whisper's limit)
MAX_FILE_SIZE = 25 * 1024 * 1024


@router.post("/transcriptions")
async def create_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form(
        default="whisper-1",
        description="Model to use for transcription (whisper-1, whisper-large-v3, etc.)",
    ),
    language: str | None = Form(
        default=None,
        description="Language of the audio in ISO-639-1 format (e.g., 'en', 'es', 'fr'). "
        "Providing this improves accuracy and speed.",
    ),
    prompt: str | None = Form(
        default=None,
        description="Optional text to guide the model's style or continue a previous segment. "
        "Useful for domain-specific vocabulary or maintaining context.",
    ),
    response_format: str = Form(
        default="json", description="Output format: 'json', 'text', 'srt', 'verbose_json', or 'vtt'"
    ),
    temperature: float = Form(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature (0-1). Lower values are more deterministic.",
    ),
    _api_key: str | None = Depends(get_optional_api_key),  # noqa: ARG001 - Used for auth side effects
):
    """
    Transcribe audio using OpenAI Whisper or compatible services.

    This endpoint accepts audio files and returns transcribed text. It supports
    various audio formats and provides options for improving transcription quality:

    - **language**: Specify the language to improve accuracy (recommended)
    - **prompt**: Provide context or domain-specific vocabulary
    - **temperature**: Control randomness (0 = deterministic, 1 = creative)

    ## Audio Optimization Tips

    For best results:
    1. Use 16kHz sample rate (Whisper's training rate)
    2. Mono audio is sufficient
    3. Apply noise reduction before upload
    4. Keep segments under 30 seconds for real-time use cases
    5. Provide language hint when known

    ## Supported Formats

    flac, m4a, mp3, mp4, mpeg, mpga, ogg, wav, webm
    """
    request_id = str(uuid.uuid4())[:8]

    # Validate content type - only accept known supported formats
    content_type = file.content_type or "application/octet-stream"
    if content_type not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format: {content_type}. "
            f"Supported formats: {', '.join(SUPPORTED_FORMATS.keys())}",
        )

    # Read file content
    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"[{request_id}] Failed to read audio file: {e}")
        raise HTTPException(status_code=400, detail="Failed to read audio file")

    # Validate file size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Audio file too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty")

    logger.info(
        f"[{request_id}] Transcription request: "
        f"model={model}, language={language}, format={response_format}, "
        f"size={len(content)} bytes, content_type={content_type}"
    )

    # Determine file extension
    extension = SUPPORTED_FORMATS.get(content_type, ".webm")

    # Create temporary file for the API
    tmp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp_file:
            tmp_file_path = tmp_file.name
            tmp_file.write(content)

        # Get OpenAI client
        try:
            client = get_openai_pooled_client()
        except Exception as e:
            logger.error(f"[{request_id}] Failed to get OpenAI client: {e}")
            raise HTTPException(
                status_code=503, detail="Transcription service temporarily unavailable"
            )

        # Build transcription parameters
        transcription_params = {
            "model": model,
            "response_format": response_format,
            "temperature": temperature,
        }

        # Add optional parameters
        if language:
            transcription_params["language"] = language
        if prompt:
            transcription_params["prompt"] = prompt

        # Call Whisper API with distributed tracing for Tempo
        async with AITracer.trace_inference(
            provider="openai",
            model=model,
            request_type=AIRequestType.AUDIO_TRANSCRIPTION,
        ) as trace_ctx:
            with open(tmp_file_path, "rb") as audio_file:
                try:
                    response = client.audio.transcriptions.create(
                        file=audio_file, **transcription_params
                    )
                except Exception as e:
                    logger.error(f"[{request_id}] Whisper API error: {e}")
                    raise HTTPException(status_code=502, detail=f"Transcription failed: {str(e)}")

            # Add tracing metadata
            trace_ctx.add_event(
                "transcription_completed",
                {
                    "file_size_bytes": len(content),
                    "language": language,
                    "response_format": response_format,
                },
            )

            logger.info(f"[{request_id}] Transcription completed successfully")

        # Return response based on format
        if response_format == "text":
            # When response_format is "text", Whisper returns a plain string
            return JSONResponse(
                content={"text": response if isinstance(response, str) else str(response)}
            )
        elif response_format in ("json", "verbose_json"):
            # OpenAI returns a Transcription object
            if hasattr(response, "text"):
                result = {"text": response.text}
                if hasattr(response, "language"):
                    result["language"] = response.language
                if hasattr(response, "duration"):
                    result["duration"] = response.duration
                if hasattr(response, "segments") and response_format == "verbose_json":
                    result["segments"] = response.segments
                if hasattr(response, "words") and response_format == "verbose_json":
                    result["words"] = response.words
                return JSONResponse(content=result)
            return JSONResponse(content={"text": str(response)})
        else:
            # SRT or VTT format - return as text
            return JSONResponse(content={"text": str(response)})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        # Clean up temp file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except OSError as cleanup_err:
                logger.warning(f"[{request_id}] Failed to clean up temp file: {cleanup_err}")


@router.post("/transcriptions/base64")
async def create_transcription_base64(
    audio_data: str = Form(..., description="Base64-encoded audio data"),
    content_type: str = Form(
        default="audio/webm", description="MIME type of the audio (e.g., 'audio/webm', 'audio/wav')"
    ),
    model: str = Form(default="whisper-1"),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0.0, ge=0.0, le=1.0),
    _api_key: str | None = Depends(get_optional_api_key),  # noqa: ARG001 - Used for auth side effects
):
    """
    Transcribe base64-encoded audio.

    This endpoint is useful for browser-based applications that capture
    audio as base64 data URLs. It accepts the raw base64 string (without
    the data URL prefix).

    ## Example

    If you have a data URL like:
    `data:audio/webm;base64,GkXfo59ChoEBQv...`

    Extract just the base64 part after the comma:
    `GkXfo59ChoEBQv...`
    """
    request_id = str(uuid.uuid4())[:8]

    # Handle data URL format
    if audio_data.startswith("data:"):
        try:
            # Parse data URL: data:audio/webm;base64,<data>
            header, encoded = audio_data.split(",", 1)
            if ";" in header:
                mime_part = header.split(";")[0]
                content_type = mime_part.replace("data:", "")
            audio_data = encoded
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid data URL format")

    # Decode base64
    try:
        content = base64.b64decode(audio_data)
    except Exception as e:
        logger.error(f"[{request_id}] Failed to decode base64 audio: {e}")
        raise HTTPException(status_code=400, detail="Invalid base64-encoded audio data")

    # Validate size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Audio data too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Audio data is empty")

    logger.info(
        f"[{request_id}] Base64 transcription request: "
        f"model={model}, language={language}, format={response_format}, "
        f"size={len(content)} bytes, content_type={content_type}"
    )

    # Determine file extension
    extension = SUPPORTED_FORMATS.get(content_type, ".webm")

    # Create temporary file and transcribe
    tmp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp_file:
            tmp_file_path = tmp_file.name
            tmp_file.write(content)

        # Get OpenAI client
        try:
            client = get_openai_pooled_client()
        except Exception as e:
            logger.error(f"[{request_id}] Failed to get OpenAI client: {e}")
            raise HTTPException(
                status_code=503, detail="Transcription service temporarily unavailable"
            )

        # Build transcription parameters
        transcription_params = {
            "model": model,
            "response_format": response_format,
            "temperature": temperature,
        }

        if language:
            transcription_params["language"] = language
        if prompt:
            transcription_params["prompt"] = prompt

        # Call Whisper API
        with open(tmp_file_path, "rb") as audio_file:
            try:
                response = client.audio.transcriptions.create(
                    file=audio_file, **transcription_params
                )
            except Exception as e:
                logger.error(f"[{request_id}] Whisper API error: {e}")
                raise HTTPException(status_code=502, detail=f"Transcription failed: {str(e)}")

        logger.info(f"[{request_id}] Base64 transcription completed successfully")

        # Return response
        if hasattr(response, "text"):
            result = {"text": response.text}
            if hasattr(response, "language"):
                result["language"] = response.language
            if hasattr(response, "duration"):
                result["duration"] = response.duration
            return JSONResponse(content=result)
        return JSONResponse(content={"text": str(response)})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        # Clean up temp file
        if tmp_file_path:
            try:
                os.unlink(tmp_file_path)
            except OSError as cleanup_err:
                logger.warning(f"[{request_id}] Failed to clean up temp file: {cleanup_err}")
