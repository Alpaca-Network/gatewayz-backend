"""Novita AI client for API integration.

This module provides integration with Novita AI:
1. LLM models (Qwen, DeepSeek, Llama, etc.) via OpenAI-compatible API
2. Image/video generation via official Novita Python SDK

Note: Novita provides two separate APIs:
- OpenAI-compatible API for LLM chat completions: https://api.novita.ai/v3/openai
- Native API for image/video generation (accessed via novita-client SDK)

The official Novita Python SDK (novita-client) is available for image generation.
For LLM models, we use OpenAI SDK to access their OpenAI-compatible endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name

# Initialize logging
logger = logging.getLogger(__name__)

# Optional: Import Novita SDK for image generation features
try:
    from novita_client import NovitaClient

    NOVITA_SDK_AVAILABLE = True
except ImportError:
    logger.debug(
        "Novita SDK (novita-client) not available. Install with: pip install novita-client"
    )
    NOVITA_SDK_AVAILABLE = False
    NovitaClient = None

DEFAULT_NOVITA_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen3-235b-thinking",
        "name": "Qwen3 235B Thinking",
        "owned_by": "alibaba",
        "context_length": 32768,
    },
    {
        "id": "qwen3-max",
        "name": "Qwen3 Max",
        "owned_by": "alibaba",
        "context_length": 32768,
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek V3",
        "owned_by": "deepseek",
        "context_length": 64000,
    },
    {
        "id": "llama-3.3-70b",
        "name": "Llama 3.3 70B",
        "owned_by": "meta",
        "context_length": 8192,
    },
    {
        "id": "mistral-nemo",
        "name": "Mistral Nemo",
        "owned_by": "mistral",
        "context_length": 8192,
    },
]


def get_novita_sdk_client():
    """Get an instance of the Novita SDK client for image/video generation.

    Returns:
        NovitaClient instance if SDK is available and API key is configured, None otherwise.

    Raises:
        ValueError: If NOVITA_API_KEY is not configured.
    """
    if not NOVITA_SDK_AVAILABLE:
        logger.warning("Novita SDK not available. Install with: pip install novita-client")
        return None

    try:
        from src.config import Config

        if not Config.NOVITA_API_KEY:
            raise ValueError("NOVITA_API_KEY not configured")

        # Initialize SDK client with API key
        client = NovitaClient(api_key=Config.NOVITA_API_KEY)
        logger.debug("Novita SDK client initialized successfully")
        return client
    except Exception as exc:
        logger.error(f"Failed to initialize Novita SDK client: {exc}")
        raise


def fetch_models_from_novita():
    """Fetch and normalize LLM models from Novita's OpenAI-compatible API.

    Novita provides an OpenAI-compatible API endpoint that lists available LLM models.
    We fetch from that endpoint and normalize the response to match our catalog format.
    Falls back to a static catalog if the live fetch fails.

    Note: This function fetches LLM models (Qwen, DeepSeek, Llama, etc.) for chat completions.
    For image generation models, use get_novita_sdk_client() and call client.models_v3().
    """

    def _cache_and_return(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("novita", models)
        return models

    try:
        from src.config import Config

        if not Config.NOVITA_API_KEY:
            logger.warning("NOVITA_API_KEY not configured, using static catalog")
            fallback = _fallback_novita_models("api_key_missing")
            return _cache_and_return(fallback) if fallback else None

    except Exception as exc:
        logger.error(f"Failed to load config for Novita: {exc}")
        fallback = _fallback_novita_models("config_load_failed")
        return _cache_and_return(fallback) if fallback else None

    try:
        from openai import OpenAI

        client = OpenAI(base_url="https://api.novita.ai/v3/openai", api_key=Config.NOVITA_API_KEY)
        response = client.models.list()
    except Exception as exc:
        logger.error(f"Novita models.list() failed: {exc}")
        fallback = _fallback_novita_models("api_error")
        return _cache_and_return(fallback) if fallback else None

    raw_models = _extract_models_from_response(response)
    normalized_models = [
        model for model in (_normalize_novita_model(entry) for entry in raw_models) if model
    ]

    if not normalized_models:
        logger.warning("Novita API returned zero models; falling back to static catalog")
        fallback = _fallback_novita_models("empty_response")
        return _cache_and_return(fallback) if fallback else None

    logger.info("Fetched %s Novita models from live API", len(normalized_models))
    return _cache_and_return(normalized_models)


def _fallback_novita_models(reason: str) -> list[dict[str, Any]] | None:
    logger.warning("Using fallback Novita model catalog (%s)", reason)

    # Try database fallback first (dynamic, from last successful sync)
    try:
        from src.services.models import get_fallback_models_from_db

        db_fallback = get_fallback_models_from_db("novita")
        if db_fallback:
            normalized = [
                model
                for model in (_normalize_novita_model(entry) for entry in db_fallback)
                if model
            ]
            if normalized:
                logger.info(f"Using {len(normalized)} Novita models from database fallback")
                return normalized
    except Exception as e:
        logger.warning(f"Failed to get database fallback for Novita: {e}")

    # Static fallback as last resort
    logger.warning("Database fallback empty, using static fallback for Novita")
    normalized = [
        model
        for model in (_normalize_novita_model(entry) for entry in DEFAULT_NOVITA_MODELS)
        if model
    ]
    return normalized or None


def _extract_models_from_response(response: Any) -> list[Any]:
    """
    Coerce whatever the OpenAI SDK returns (SyncPage, list, dict, etc.)
    into a plain list of model payloads.
    """

    if response is None:
        return []

    for attr in ("data", "models", "items"):
        if hasattr(response, attr):
            data = getattr(response, attr)
            return _coerce_sequence(data)

    if isinstance(response, dict):
        for key in ("data", "models", "items"):
            if key in response:
                return _coerce_sequence(response[key])

    return _coerce_sequence(response)


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _coerce_model_to_dict(model: Any) -> dict[str, Any]:
    if isinstance(model, dict):
        return dict(model)

    for attr in ("model_dump", "dict"):
        if hasattr(model, attr):
            try:
                payload = getattr(model, attr)()
                if isinstance(payload, dict):
                    return payload
            except Exception:  # pragma: no cover - defensive
                continue

    data: dict[str, Any] = {}
    for attr in (
        "id",
        "object",
        "created",
        "owned_by",
        "name",
        "display_name",
        "description",
        "context_length",
        "max_context_length",
        "max_sequence_length",
        "pricing",
        "capabilities",
        "metadata",
        "default_parameters",
        "provider",
        "provider_slug",
        "limits",
        "rate_limits",
        "tags",
    ):
        if hasattr(model, attr):
            data[attr] = getattr(model, attr)

    if not data:
        data["value"] = str(model)

    return data


def _normalize_novita_model(model: Any) -> dict[str, Any] | None:
    payload = _coerce_model_to_dict(model)
    raw_id = payload.get("id") or payload.get("name")
    if not raw_id:
        return None

    provider_model_id = _cleanup_model_id(str(raw_id))
    provider_slug = (
        payload.get("provider_slug")
        or payload.get("provider")
        or payload.get("owned_by")
        or (provider_model_id.split("/")[0] if "/" in provider_model_id else "novita")
    )
    # Inline normalization mirrors normalize_provider_slug() in src/services/models.py.
    # A module-level import is avoided here to prevent a circular import (models.py
    # imports novita_client at module level).
    provider_slug = str(provider_slug).lstrip("@").lower() if provider_slug else "novita"

    # Get and clean display name (remove colons, parentheses, etc.)
    raw_display_name = payload.get("display_name") or payload.get("name") or provider_model_id
    display_name = clean_model_name(raw_display_name)
    description = payload.get("description") or f"Novita hosted model '{display_name}'."
    context_length = (
        payload.get("context_length")
        or payload.get("max_context_length")
        or payload.get("max_sequence_length")
        or 0
    )

    architecture = (
        payload.get("architecture") if isinstance(payload.get("architecture"), dict) else {}
    )
    normalized_architecture = {
        "modality": architecture.get("modality") or "text->text",
        "input_modalities": architecture.get("input_modalities") or ["text"],
        "output_modalities": architecture.get("output_modalities") or ["text"],
        "tokenizer": architecture.get("tokenizer"),
        "instruct_type": architecture.get("instruct_type") or "chat",
    }

    normalized = {
        "id": provider_model_id,
        "slug": provider_model_id,
        "canonical_slug": provider_model_id,
        "hugging_face_id": payload.get("hugging_face_id"),
        "name": display_name,
        "created": payload.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": normalized_architecture,
        "pricing": _normalize_pricing(payload.get("pricing")),
        "per_request_limits": payload.get("limits") or payload.get("rate_limits"),
        "supported_parameters": _extract_supported_parameters(payload),
        "default_parameters": payload.get("default_parameters") or {},
        "provider_slug": provider_slug,
        "provider_site_url": payload.get("provider_site_url") or "https://www.novita.ai/",
        "model_logo_url": payload.get("model_logo_url"),
        "source_gateway": "novita",
        "tags": payload.get("tags") or [],
        "raw_novita": payload,
    }

    return normalized


def _normalize_pricing(pricing: dict[str, Any] | None) -> dict[str, str | None]:
    pricing = pricing or {}

    def _stringify(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return str(value)
        except Exception:  # pragma: no cover - defensive
            return None

    prompt = (
        pricing.get("prompt")
        or pricing.get("input")
        or pricing.get("prompt_price")
        or pricing.get("prompt_tokens")
    )
    completion = (
        pricing.get("completion")
        or pricing.get("output")
        or pricing.get("completion_price")
        or pricing.get("completion_tokens")
    )

    return {
        "prompt": _stringify(prompt),
        "completion": _stringify(completion),
        "request": _stringify(pricing.get("request")),
        "image": _stringify(pricing.get("image")),
        "web_search": _stringify(pricing.get("web_search")),
        "internal_reasoning": _stringify(pricing.get("internal_reasoning")),
    }


def _extract_supported_parameters(payload: dict[str, Any]) -> list[str]:
    supported = payload.get("supported_parameters")
    capabilities = payload.get("capabilities")

    params: list[str] = []

    if isinstance(supported, list | tuple | set):
        params.extend(str(item) for item in supported if item)

    if isinstance(capabilities, dict):
        for key, value in capabilities.items():
            if key:
                params.append(str(key))
            if isinstance(value, dict):
                params.extend(str(inner) for inner in value.keys() if inner)
            elif isinstance(value, list | tuple | set):
                params.extend(str(inner) for inner in value if inner)

    if not params:
        params = ["max_tokens", "temperature", "top_p", "top_k", "stream"]

    # Deduplicate while preserving order
    seen = set()
    deduped: list[str] = []
    for item in params:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _cleanup_model_id(model_id: str) -> str:
    cleaned = model_id.strip()
    # Remove leading @ symbols and @novita/ prefix
    if cleaned.startswith("@"):
        cleaned = cleaned.lstrip("@")
    # Remove common prefixes
    for prefix in ("novita/", "models/", "api/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned.split(prefix, 1)[1]
            break
    return cleaned


# Image Generation Functions (using Novita SDK)
def fetch_image_models_from_novita_sdk():
    """Fetch image generation models using the official Novita SDK.

    This fetches models for image generation (checkpoints, LoRAs, VAE, ControlNet, etc.)
    using the novita-client SDK. These are different from the LLM models.

    Returns:
        List of image generation models, or None if SDK is unavailable or fetch fails.
    """
    if not NOVITA_SDK_AVAILABLE:
        logger.warning("Cannot fetch image models: Novita SDK not installed")
        return None

    try:
        client = get_novita_sdk_client()
        if not client:
            return None

        # Fetch all image models using SDK
        model_list = client.models_v3(refresh=True)
        logger.info(f"Fetched {len(model_list.models)} image models from Novita SDK")

        # Return the raw model list for now
        # Can be normalized to match our catalog format if needed
        return model_list.models
    except Exception as exc:
        logger.error(f"Failed to fetch image models from Novita SDK: {exc}")
        return None


def generate_image_with_novita_sdk(
    prompt: str, model_name: str = "dreamshaper_8_93211.safetensors", **kwargs
):
    """Generate an image using the Novita SDK.

    Args:
        prompt: Text description of the image to generate
        model_name: Name of the model to use for generation
        **kwargs: Additional parameters (width, height, steps, etc.)

    Returns:
        Generated image response from Novita SDK

    Example:
        response = generate_image_with_novita_sdk(
            prompt="a cute dog",
            model_name="dreamshaper_8_93211.safetensors",
            width=512,
            height=512,
            steps=30
        )
    """
    if not NOVITA_SDK_AVAILABLE:
        raise ImportError("Novita SDK not installed. Install with: pip install novita-client")

    try:
        client = get_novita_sdk_client()
        if not client:
            raise ValueError("Failed to initialize Novita SDK client")

        # Generate image using SDK's txt2img_v3 method
        response = client.txt2img_v3(
            model_name=model_name,
            prompt=prompt,
            image_num=kwargs.get("image_num", 1),
            width=kwargs.get("width", 512),
            height=kwargs.get("height", 512),
            steps=kwargs.get("steps", 30),
            guidance_scale=kwargs.get("guidance_scale", 7.5),
            seed=kwargs.get("seed", -1),
            negative_prompt=kwargs.get("negative_prompt"),
            sampler_name=kwargs.get("sampler_name"),
            download_images=kwargs.get("download_images", True),
        )

        logger.info(f"Successfully generated image with Novita SDK for prompt: {prompt[:50]}...")
        return response
    except Exception as exc:
        logger.error(f"Failed to generate image with Novita SDK: {exc}")
        raise
