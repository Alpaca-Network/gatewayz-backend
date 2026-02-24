"""Audio transcription routes using Whisper API.

This module provides endpoints for audio transcription using OpenAI Whisper
or compatible services (Simplismart). Supports various audio formats and
provides options for language hints, prompt context, and output formatting.

Billing: Audio transcription is billed per minute of audio. When the actual
duration is not available from the API response, duration is estimated from
file size (approximately 1 minute per 1MB for compressed formats).
"""

import asyncio
import base64
import logging
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.config import Config
from src.db.api_keys import increment_api_key_usage
from src.db.users import deduct_credits, get_user, record_usage
from src.security.deps import get_api_key
from src.services.connection_pool import get_openai_pooled_client
from src.utils.ai_tracing import AIRequestType, AITracer

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

# Audio transcription pricing (cost per minute in USD)
# Based on OpenAI Whisper pricing as of Jan 2025
# TODO: Move to database-driven pricing for easier updates
AUDIO_COST_PER_MINUTE = {
    "whisper-1": 0.006,  # $0.006 per minute (OpenAI pricing)
    "whisper-large-v3": 0.006,
    "default": 0.006,
}

# Default fallback cost when model is unknown - set conservatively high
# to avoid revenue loss on new expensive models
UNKNOWN_MODEL_DEFAULT_COST_PER_MINUTE = 0.01

# Approximate bytes per minute for compressed audio formats.
# Conservative estimate: most compressed audio (mp3, ogg, webm, m4a) averages
# ~1MB/min at typical bitrates (128-192kbps). We use a slightly lower value
# to avoid undercharging.
BYTES_PER_MINUTE_ESTIMATE = 1_000_000  # 1MB per minute

# Uncompressed formats (WAV, FLAC) have much higher bitrates
UNCOMPRESSED_BYTES_PER_MINUTE = {
    ".wav": 10_000_000,  # ~10MB/min at 16-bit 44.1kHz stereo
    ".flac": 5_000_000,  # ~5MB/min (lossless compression varies)
}


def estimate_audio_duration_minutes(file_size_bytes: int, extension: str) -> float:
    """
    Estimate audio duration in minutes based on file size and format.

    This is used as a fallback when the API response does not include
    actual duration (e.g., when response_format is not verbose_json).

    Args:
        file_size_bytes: Size of the audio file in bytes
        extension: File extension (e.g., ".mp3", ".wav")

    Returns:
        Estimated duration in minutes (minimum 0.1 minutes / 6 seconds)
    """
    bytes_per_minute = UNCOMPRESSED_BYTES_PER_MINUTE.get(extension, BYTES_PER_MINUTE_ESTIMATE)
    estimated_minutes = file_size_bytes / bytes_per_minute
    # Minimum charge of 0.1 minutes (6 seconds) to cover very short clips
    return max(0.1, estimated_minutes)


def get_audio_cost(model: str, duration_minutes: float) -> tuple[float, float, bool]:
    """
    Calculate the cost for audio transcription.

    Args:
        model: Whisper model name (e.g., "whisper-1", "whisper-large-v3")
        duration_minutes: Duration of the audio in minutes

    Returns:
        Tuple of (total_cost, cost_per_minute, is_fallback_pricing)
        is_fallback_pricing is True when using default/unknown pricing
    """
    is_fallback = False

    if model in AUDIO_COST_PER_MINUTE:
        cost_per_minute = AUDIO_COST_PER_MINUTE[model]
    elif "default" in AUDIO_COST_PER_MINUTE:
        cost_per_minute = AUDIO_COST_PER_MINUTE["default"]
        is_fallback = True
        logger.warning(
            f"Using default pricing for unknown audio model: model={model}, "
            f"cost_per_minute={cost_per_minute}"
        )
    else:
        cost_per_minute = UNKNOWN_MODEL_DEFAULT_COST_PER_MINUTE
        is_fallback = True
        logger.warning(
            f"Using fallback pricing for unknown audio model: model={model}, "
            f"cost_per_minute={cost_per_minute}"
        )

    total_cost = cost_per_minute * duration_minutes
    return total_cost, cost_per_minute, is_fallback


async def _deduct_audio_credits(
    api_key: str,
    user: dict,
    model: str,
    total_cost: float,
    duration_minutes: float,
    elapsed_ms: int,
    request_id: str,
    endpoint: str,
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
) -> float | None:
    """
    Deduct credits for audio transcription and record usage.

    Follows the same fail-safe pattern as image generation billing:
    if credit deduction fails, the error is raised so the user does NOT
    get free transcription.

    Args:
        api_key: User's API key
        user: User dict from database
        model: Model used for transcription
        total_cost: Total cost in USD
        duration_minutes: Audio duration in minutes
        elapsed_ms: Request processing time in ms
        request_id: Request identifier for logging
        endpoint: API endpoint path
        loop: Running event loop
        executor: Thread pool executor for sync DB operations

    Returns:
        Updated user balance after deduction, or None if balance fetch failed

    Raises:
        HTTPException: 402 if insufficient credits, 500 on unexpected billing error
    """
    # Token-equivalent for rate limiting: use 100 tokens per minute as a standardized unit
    # This maintains compatibility with token-based rate limiting
    tokens_equivalent = max(1, int(100 * duration_minutes))

    actual_balance_after = None
    try:
        await loop.run_in_executor(
            executor,
            deduct_credits,
            api_key,
            total_cost,
            f"Audio transcription - {model}",
            {
                "model": model,
                "duration_minutes": round(duration_minutes, 2),
                "cost_usd": total_cost,
                "endpoint": endpoint,
            },
        )

        # Fetch fresh balance after deduction for accurate reporting
        updated_user = await loop.run_in_executor(executor, get_user, api_key)
        if updated_user:
            actual_balance_after = updated_user.get("credits")

        await loop.run_in_executor(
            executor,
            record_usage,
            user["id"],
            api_key,
            model,
            tokens_equivalent,
            total_cost,
            elapsed_ms,
        )

        # Increment API key usage count
        await loop.run_in_executor(executor, increment_api_key_usage, api_key)

    except ValueError as e:
        # Insufficient credits or daily limit exceeded - user should NOT get free transcription
        logger.error(f"[{request_id}] Credit deduction failed for audio transcription: {e}")
        raise HTTPException(
            status_code=402,
            detail=f"Payment required: {e}",
        )
    except Exception as e:
        # Unexpected error in billing - fail safe, don't give away free transcription
        logger.error(f"[{request_id}] Unexpected error in credit deduction: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Billing error occurred. Please try again or contact support.",
        )

    return actual_balance_after


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
    api_key: str = Depends(get_api_key),
):
    """
    Transcribe audio using OpenAI Whisper or compatible services.

    This endpoint accepts audio files and returns transcribed text. It supports
    various audio formats and provides options for improving transcription quality:

    - **language**: Specify the language to improve accuracy (recommended)
    - **prompt**: Provide context or domain-specific vocabulary
    - **temperature**: Control randomness (0 = deterministic, 1 = creative)

    ## Billing

    Audio transcription is billed per minute of audio at model-specific rates.
    Credits are deducted after successful transcription.

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

    # Get event loop and thread pool for async DB operations
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=4)

    # Create temporary file for the API
    tmp_file_path = None
    try:
        # --- Auth & credit pre-check ---
        user = await loop.run_in_executor(executor, get_user, api_key)
        if not user:
            if (
                Config.IS_TESTING or os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}
            ) and api_key.lower().startswith("test"):
                user = {
                    "id": 0,
                    "credits": 1_000_000.0,
                    "api_key": api_key,
                }
            else:
                raise HTTPException(status_code=401, detail="Invalid API key")

        # Estimate duration from file size for pre-check
        estimated_duration = estimate_audio_duration_minutes(len(content), extension)
        estimated_cost, cost_per_minute, _ = get_audio_cost(model, estimated_duration)

        # Pre-flight credit sufficiency check with 10% buffer for race conditions
        required_credits = estimated_cost * 1.1
        if user["credits"] < required_credits:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient credits. Audio transcription estimated cost: ${estimated_cost:.4f} "
                    f"(${cost_per_minute:.4f}/min x {estimated_duration:.1f} min estimated), "
                    f"requires ${required_credits:.4f} with safety buffer. "
                    f"Available: ${user['credits']:.4f}"
                ),
            )

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

        # Start timing inference
        start = time.monotonic()

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

            elapsed = max(0.001, time.monotonic() - start)

            # Determine actual duration: prefer API response, fall back to estimate
            actual_duration = None
            if hasattr(response, "duration") and response.duration is not None:
                actual_duration = response.duration / 60.0  # Convert seconds to minutes
            if actual_duration is None or actual_duration <= 0:
                actual_duration = estimated_duration

            # Calculate actual cost based on real/estimated duration
            total_cost, cost_per_minute, used_fallback_pricing = get_audio_cost(
                model, actual_duration
            )

            # Set tracing metadata
            trace_ctx.set_cost(total_cost)
            trace_ctx.set_user_info(user_id=str(user.get("id")))
            trace_ctx.add_event(
                "transcription_completed",
                {
                    "file_size_bytes": len(content),
                    "language": language,
                    "response_format": response_format,
                    "duration_minutes": round(actual_duration, 2),
                    "cost_usd": total_cost,
                },
            )

            logger.info(f"[{request_id}] Transcription completed successfully")

        # --- Billing: deduct credits ---
        elapsed_ms = int(elapsed * 1000)
        actual_balance_after = await _deduct_audio_credits(
            api_key=api_key,
            user=user,
            model=model,
            total_cost=total_cost,
            duration_minutes=actual_duration,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
            endpoint="/v1/audio/transcriptions",
            loop=loop,
            executor=executor,
        )

        # Build response based on format
        if response_format == "text":
            # When response_format is "text", Whisper returns a plain string
            result = {"text": response if isinstance(response, str) else str(response)}
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
            else:
                result = {"text": str(response)}
        else:
            # SRT or VTT format - return as text
            result = {"text": str(response)}

        # Add gateway usage info
        result["gateway_usage"] = {
            "cost_usd": total_cost,
            "cost_per_minute": cost_per_minute,
            "duration_minutes": round(actual_duration, 2),
            "request_ms": elapsed_ms,
            "user_balance_after": (
                actual_balance_after
                if actual_balance_after is not None
                else user["credits"] - total_cost
            ),
            "user_api_key": f"{api_key[:10]}...",
            "used_fallback_pricing": used_fallback_pricing,
        }

        return JSONResponse(content=result)

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
        # Clean up executor
        if "executor" in locals():
            executor.shutdown(wait=False)


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
    api_key: str = Depends(get_api_key),
):
    """
    Transcribe base64-encoded audio.

    This endpoint is useful for browser-based applications that capture
    audio as base64 data URLs. It accepts the raw base64 string (without
    the data URL prefix).

    ## Billing

    Audio transcription is billed per minute of audio at model-specific rates.
    Credits are deducted after successful transcription.

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

    # Get event loop and thread pool for async DB operations
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=4)

    # Create temporary file and transcribe
    tmp_file_path = None
    try:
        # --- Auth & credit pre-check ---
        user = await loop.run_in_executor(executor, get_user, api_key)
        if not user:
            if (
                Config.IS_TESTING or os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}
            ) and api_key.lower().startswith("test"):
                user = {
                    "id": 0,
                    "credits": 1_000_000.0,
                    "api_key": api_key,
                }
            else:
                raise HTTPException(status_code=401, detail="Invalid API key")

        # Estimate duration from file size for pre-check
        estimated_duration = estimate_audio_duration_minutes(len(content), extension)
        estimated_cost, cost_per_minute, _ = get_audio_cost(model, estimated_duration)

        # Pre-flight credit sufficiency check with 10% buffer for race conditions
        required_credits = estimated_cost * 1.1
        if user["credits"] < required_credits:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient credits. Audio transcription estimated cost: ${estimated_cost:.4f} "
                    f"(${cost_per_minute:.4f}/min x {estimated_duration:.1f} min estimated), "
                    f"requires ${required_credits:.4f} with safety buffer. "
                    f"Available: ${user['credits']:.4f}"
                ),
            )

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

        # Start timing inference
        start = time.monotonic()

        # Call Whisper API
        with open(tmp_file_path, "rb") as audio_file:
            try:
                response = client.audio.transcriptions.create(
                    file=audio_file, **transcription_params
                )
            except Exception as e:
                logger.error(f"[{request_id}] Whisper API error: {e}")
                raise HTTPException(status_code=502, detail=f"Transcription failed: {str(e)}")

        elapsed = max(0.001, time.monotonic() - start)

        logger.info(f"[{request_id}] Base64 transcription completed successfully")

        # Determine actual duration: prefer API response, fall back to estimate
        actual_duration = None
        if hasattr(response, "duration") and response.duration is not None:
            actual_duration = response.duration / 60.0  # Convert seconds to minutes
        if actual_duration is None or actual_duration <= 0:
            actual_duration = estimated_duration

        # Calculate actual cost based on real/estimated duration
        total_cost, cost_per_minute, used_fallback_pricing = get_audio_cost(model, actual_duration)

        # --- Billing: deduct credits ---
        elapsed_ms = int(elapsed * 1000)
        actual_balance_after = await _deduct_audio_credits(
            api_key=api_key,
            user=user,
            model=model,
            total_cost=total_cost,
            duration_minutes=actual_duration,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
            endpoint="/v1/audio/transcriptions/base64",
            loop=loop,
            executor=executor,
        )

        # Build response
        if hasattr(response, "text"):
            result = {"text": response.text}
            if hasattr(response, "language"):
                result["language"] = response.language
            if hasattr(response, "duration"):
                result["duration"] = response.duration
        else:
            result = {"text": str(response)}

        # Add gateway usage info
        result["gateway_usage"] = {
            "cost_usd": total_cost,
            "cost_per_minute": cost_per_minute,
            "duration_minutes": round(actual_duration, 2),
            "request_ms": elapsed_ms,
            "user_balance_after": (
                actual_balance_after
                if actual_balance_after is not None
                else user["credits"] - total_cost
            ),
            "user_api_key": f"{api_key[:10]}...",
            "used_fallback_pricing": used_fallback_pricing,
        }

        return JSONResponse(content=result)

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
        # Clean up executor
        if "executor" in locals():
            executor.shutdown(wait=False)
