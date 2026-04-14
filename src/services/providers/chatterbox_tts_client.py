"""
Chatterbox TTS Client for text-to-speech generation.

This module provides integration with Resemble AI's Chatterbox TTS model,
an open-source text-to-speech system with zero-shot voice cloning capabilities.

Models available:
- Chatterbox-Turbo (350M): Fast, low-latency, English-only, supports paralinguistic tags
- Chatterbox-Multilingual (500M): 23+ languages, zero-shot voice cloning
- Chatterbox (500M): English with creative control (CFG weighting, exaggeration)

Reference: https://github.com/resemble-ai/chatterbox
"""

import asyncio
import base64
import io
import ipaddress
import logging
import os
import tempfile
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from src.config import Config

logger = logging.getLogger(__name__)

# Chatterbox configuration
CHATTERBOX_TIMEOUT = 60.0  # seconds - TTS can take time for longer texts
CHATTERBOX_MAX_TEXT_LENGTH = 5000  # characters
CHATTERBOX_MAX_VOICE_REF_SIZE = 10 * 1024 * 1024  # 10 MB max for voice reference files

# Blocked IP ranges for SSRF protection
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),  # Private
    ipaddress.ip_network("172.16.0.0/12"),  # Private
    ipaddress.ip_network("192.168.0.0/16"),  # Private
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),  # Current network
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 private
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

# Supported models
CHATTERBOX_MODELS = {
    "chatterbox-turbo": {
        "name": "Chatterbox Turbo",
        "description": "Fast, low-latency English TTS with paralinguistic tags ([laugh], [cough], etc.)",
        "parameters": 350_000_000,
        "languages": ["en"],
        "features": ["paralinguistic_tags", "voice_cloning"],
    },
    "chatterbox-multilingual": {
        "name": "Chatterbox Multilingual",
        "description": "Multi-language TTS supporting 23+ languages with zero-shot voice cloning",
        "parameters": 500_000_000,
        "languages": [
            "ar",
            "zh",
            "cs",
            "nl",
            "en",
            "fr",
            "de",
            "hi",
            "hu",
            "id",
            "it",
            "ja",
            "ko",
            "pl",
            "pt",
            "ro",
            "ru",
            "es",
            "th",
            "tr",
            "uk",
            "vi",
        ],
        "features": ["multilingual", "voice_cloning"],
    },
    "chatterbox": {
        "name": "Chatterbox",
        "description": "English TTS with creative control parameters (CFG weighting, exaggeration)",
        "parameters": 500_000_000,
        "languages": ["en"],
        "features": ["creative_control", "voice_cloning"],
    },
}

# Language code to name mapping for multilingual model
LANGUAGE_NAMES = {
    "ar": "Arabic",
    "zh": "Chinese",
    "cs": "Czech",
    "nl": "Dutch",
    "en": "English",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "es": "Spanish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}


def _resolve_and_validate_url(url: str) -> tuple[bool, str | None]:
    """Resolve URL and validate for SSRF protection.

    This function resolves the hostname to an IP address and validates it,
    then returns a modified URL that uses the IP directly to prevent DNS rebinding.

    Args:
        url: URL to validate

    Returns:
        Tuple of (is_safe, safe_url_or_none)
        - (True, url_with_ip) if URL is safe
        - (False, None) if URL is unsafe or invalid
    """
    import socket

    try:
        parsed = urlparse(url)

        # Only allow http/https
        if parsed.scheme not in ("http", "https"):
            return False, None

        # Check hostname
        hostname = parsed.hostname
        if not hostname:
            return False, None

        # Block localhost variants
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False, None

        # Resolve and check IP
        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)

            for blocked_range in BLOCKED_IP_RANGES:
                if ip in blocked_range:
                    logger.warning(f"Blocked SSRF attempt to {hostname} ({ip_str})")
                    return False, None
        except (socket.gaierror, ValueError):
            # Can't resolve - could be internal DNS, block it
            logger.warning(f"Could not resolve hostname: {hostname}")
            return False, None

        # Build URL with IP to prevent DNS rebinding
        # Keep original Host header by using the IP in URL but hostname in headers
        port = f":{parsed.port}" if parsed.port else ""
        safe_url = f"{parsed.scheme}://{ip_str}{port}{parsed.path}"
        if parsed.query:
            safe_url += f"?{parsed.query}"

        return True, (safe_url, hostname)

    except Exception as e:
        logger.warning(f"URL validation failed: {e}")
        return False, None


def _is_safe_url(url: str) -> bool:
    """Check if a URL is safe to fetch (SSRF protection).

    Args:
        url: URL to validate

    Returns:
        True if URL is safe, False if it points to internal/private resources
    """
    is_safe, _ = _resolve_and_validate_url(url)
    return is_safe


def get_chatterbox_models() -> list[dict[str, Any]]:
    """Get list of available Chatterbox TTS models.

    Returns:
        List of model dictionaries with id, name, description, and features
    """
    return [
        {
            "id": model_id,
            "name": info["name"],
            "description": info["description"],
            "parameters": info["parameters"],
            "languages": info["languages"],
            "features": info["features"],
        }
        for model_id, info in CHATTERBOX_MODELS.items()
    ]


def validate_chatterbox_model(model_id: str) -> bool:
    """Check if a model ID is valid.

    Args:
        model_id: Model identifier to validate

    Returns:
        True if model exists, False otherwise
    """
    return model_id in CHATTERBOX_MODELS


def validate_language(model_id: str, language: str) -> bool:
    """Check if a language is supported by the model.

    Args:
        model_id: Model identifier
        language: Language code (e.g., 'en', 'fr')

    Returns:
        True if language is supported, False otherwise
    """
    if model_id not in CHATTERBOX_MODELS:
        return False
    return language in CHATTERBOX_MODELS[model_id]["languages"]


async def generate_speech(
    text: str,
    model: str = "chatterbox-turbo",
    voice_reference_url: str | None = None,
    language: str = "en",
    exaggeration: float = 1.0,
    cfg_weight: float = 0.5,
) -> dict[str, Any]:
    """Generate speech from text using Chatterbox TTS.

    This function uses the Chatterbox TTS API (if configured) or falls back
    to local inference using the chatterbox-tts Python package.

    Args:
        text: Text to convert to speech (max 5000 characters)
        model: Model to use (chatterbox-turbo, chatterbox-multilingual, chatterbox)
        voice_reference_url: URL to audio file for voice cloning (optional)
        language: Language code for multilingual model (default: 'en')
        exaggeration: Exaggeration level for creative control (0.0-2.0, default: 1.0)
        cfg_weight: CFG weight for creative control (0.0-1.0, default: 0.5)

    Returns:
        Dict containing:
        - audio_url: URL to the generated audio file (if using API)
        - audio_base64: Base64 encoded audio data (if using local inference)
        - duration: Audio duration in seconds
        - model: Model used for generation
        - format: Audio format (wav)

    Raises:
        ValueError: If parameters are invalid
        RuntimeError: If TTS generation fails
    """
    # Validate inputs
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")

    if len(text) > CHATTERBOX_MAX_TEXT_LENGTH:
        raise ValueError(
            f"Text too long: {len(text)} characters (max: {CHATTERBOX_MAX_TEXT_LENGTH})"
        )

    if not validate_chatterbox_model(model):
        raise ValueError(f"Invalid model: {model}. Available: {list(CHATTERBOX_MODELS.keys())}")

    if model == "chatterbox-multilingual" and not validate_language(model, language):
        raise ValueError(
            f"Language '{language}' not supported by {model}. "
            f"Supported: {CHATTERBOX_MODELS[model]['languages']}"
        )

    # Validate voice reference URL for SSRF
    if voice_reference_url and not _is_safe_url(voice_reference_url):
        raise ValueError("Invalid voice reference URL: must be a public HTTP/HTTPS URL")

    # Check for API key (Resemble AI hosted API)
    resemble_api_key = getattr(Config, "RESEMBLE_API_KEY", None)

    if resemble_api_key:
        # Use Resemble AI hosted API
        return await _generate_speech_api(
            text=text,
            model=model,
            voice_reference_url=voice_reference_url,
            language=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            api_key=resemble_api_key,
        )
    else:
        # Use local inference with chatterbox-tts package
        return await _generate_speech_local(
            text=text,
            model=model,
            voice_reference_url=voice_reference_url,
            language=language,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )


async def _generate_speech_api(
    text: str,
    model: str,
    voice_reference_url: str | None,
    language: str,
    exaggeration: float,
    cfg_weight: float,
    api_key: str,
) -> dict[str, Any]:
    """Generate speech using Resemble AI hosted API.

    This uses the commercial Resemble AI API for production deployments.
    """
    api_url = "https://api.resemble.ai/v2/synthesize"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "model": model,
        "language": language,
    }

    if voice_reference_url:
        payload["voice_reference_url"] = voice_reference_url

    if model == "chatterbox":
        payload["exaggeration"] = exaggeration
        payload["cfg_weight"] = cfg_weight

    try:
        logger.info(f"Making Resemble AI TTS request with model {model}")
        start_time = time.time()

        async with httpx.AsyncClient(timeout=CHATTERBOX_TIMEOUT) as client:
            response = await client.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()

        duration = time.time() - start_time
        logger.info(f"Resemble AI TTS completed in {duration:.2f}s")

        return {
            "audio_url": result.get("audio_url"),
            "audio_base64": result.get("audio_base64"),
            "duration": result.get("duration", 0),
            "model": model,
            "format": "wav",
            "provider": "resemble_api",
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"Resemble AI API error: {e.response.status_code} - {e.response.text}")
        raise RuntimeError(f"TTS API error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Resemble AI TTS failed: {e}", exc_info=True)
        raise RuntimeError(f"TTS generation failed: {str(e)}")


def _run_tts_inference(
    text: str,
    model: str,
    audio_prompt_path: str | None,
    language: str,
    exaggeration: float,
    cfg_weight: float,
) -> tuple[Any, int]:
    """Run TTS inference synchronously (to be called in thread pool).

    Args:
        text: Text to synthesize
        model: Model name
        audio_prompt_path: Path to voice reference audio file
        language: Language code
        exaggeration: Exaggeration level
        cfg_weight: CFG weight

    Returns:
        Tuple of (wav tensor, sample_rate)
    """
    import torch
    import torchaudio

    # Select device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Using device: {device} for Chatterbox TTS")

    # Generate based on model type
    if model == "chatterbox-turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        tts_model = ChatterboxTurboTTS.from_pretrained(device=device)
        wav = tts_model.generate(text, audio_prompt_path=audio_prompt_path)

    elif model == "chatterbox-multilingual":
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        tts_model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        wav = tts_model.generate(
            text,
            language_id=language,
            audio_prompt_path=audio_prompt_path,
        )

    else:  # chatterbox (original)
        from chatterbox.tts import ChatterboxTTS

        tts_model = ChatterboxTTS.from_pretrained(device=device)
        wav = tts_model.generate(
            text,
            audio_prompt_path=audio_prompt_path,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )

    sample_rate = tts_model.sr if hasattr(tts_model, "sr") else 24000

    # Convert tensor to WAV bytes
    audio_buffer = io.BytesIO()
    torchaudio.save(audio_buffer, wav.cpu(), sample_rate, format="wav")
    audio_buffer.seek(0)

    # Encode as base64
    audio_base64 = base64.b64encode(audio_buffer.read()).decode("utf-8")

    # Calculate duration
    audio_duration = wav.shape[-1] / sample_rate

    return audio_base64, audio_duration, sample_rate


async def _generate_speech_local(
    text: str,
    model: str,
    voice_reference_url: str | None,
    language: str,
    exaggeration: float,
    cfg_weight: float,
) -> dict[str, Any]:
    """Generate speech using local Chatterbox TTS inference.

    This uses the open-source chatterbox-tts Python package for local inference.
    Requires: pip install chatterbox-tts

    Note: Blocking operations are offloaded to a thread pool to avoid blocking
    the event loop.
    """
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "Local TTS requires torch and torchaudio. " "Install with: pip install torch torchaudio"
        )

    audio_prompt_path = None
    start_time = time.time()

    try:
        # Download voice reference if provided
        if voice_reference_url:
            # Resolve URL to IP to prevent DNS rebinding attacks
            is_safe, url_info = _resolve_and_validate_url(voice_reference_url)
            if not is_safe or not url_info:
                raise ValueError("Invalid voice reference URL")

            safe_url, original_hostname = url_info
            headers = {"Host": original_hostname}  # Preserve original Host header

            async with httpx.AsyncClient(timeout=CHATTERBOX_TIMEOUT) as client:
                # Use streaming to limit download size
                async with client.stream("GET", safe_url, headers=headers) as response:
                    response.raise_for_status()

                    # Check Content-Length header if available
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > CHATTERBOX_MAX_VOICE_REF_SIZE:
                        raise ValueError(
                            f"Voice reference file too large: {int(content_length)} bytes "
                            f"(max: {CHATTERBOX_MAX_VOICE_REF_SIZE} bytes)"
                        )

                    # Stream to temp file with size limit
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        total_size = 0
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            total_size += len(chunk)
                            if total_size > CHATTERBOX_MAX_VOICE_REF_SIZE:
                                raise ValueError(
                                    f"Voice reference file too large (max: {CHATTERBOX_MAX_VOICE_REF_SIZE} bytes)"
                                )
                            f.write(chunk)
                        audio_prompt_path = f.name

        # Run TTS inference in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        audio_base64, audio_duration, sample_rate = await loop.run_in_executor(
            None,  # Use default thread pool
            _run_tts_inference,
            text,
            model,
            audio_prompt_path,
            language,
            exaggeration,
            cfg_weight,
        )

        generation_time = time.time() - start_time
        logger.info(
            f"Chatterbox TTS generated {audio_duration:.2f}s audio in {generation_time:.2f}s"
        )

        return {
            "audio_url": None,
            "audio_base64": f"data:audio/wav;base64,{audio_base64}",
            "duration": audio_duration,
            "model": model,
            "format": "wav",
            "sample_rate": sample_rate,
            "provider": "local",
        }

    except ImportError as e:
        logger.error(f"Chatterbox TTS import failed: {e}")
        raise RuntimeError("Chatterbox TTS not installed. Install with: pip install chatterbox-tts")
    except Exception as e:
        logger.error(f"Chatterbox TTS generation failed: {e}", exc_info=True)
        raise RuntimeError(f"TTS generation failed: {str(e)}")
    finally:
        # Always clean up temp file
        if audio_prompt_path:
            try:
                os.unlink(audio_prompt_path)
            except OSError as e:
                logger.warning(f"Failed to clean up temp file {audio_prompt_path}: {e}")
