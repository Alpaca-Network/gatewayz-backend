import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.cache import _fal_models_cache
from src.config import Config
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_IMAGE = "text->image"
MODALITY_TEXT_TO_AUDIO = "text->audio"

# Cache for Fal.ai models catalog (for static JSON)
_fal_static_catalog_cache: list[dict[str, Any]] | None = None

# Fal.ai API configuration
FAL_API_BASE = "https://fal.run"
FAL_QUEUE_API_BASE = "https://queue.fal.run"
FAL_MODELS_API_BASE = "https://api.fal.ai/v1/models"
FAL_REQUEST_TIMEOUT = 120.0  # seconds
FAL_QUEUE_POLL_INTERVAL = 1.0  # seconds
FAL_QUEUE_MAX_WAIT = 300.0  # 5 minutes

# Cache for API-fetched models
_fal_api_models_cache: list[dict[str, Any]] | None = None


def fetch_fal_models_from_api() -> list[dict[str, Any]]:
    """Fetch all available Fal.ai models from the REST API

    Uses pagination to fetch all models from https://api.fal.ai/v1/models

    Returns:
        List of Fal.ai model definitions with full metadata
    """
    global _fal_api_models_cache

    if _fal_api_models_cache is not None:
        return _fal_api_models_cache

    if not Config.FAL_API_KEY:
        logger.warning("FAL_API_KEY not configured, falling back to static catalog")
        return []

    all_models = []
    page = 1
    per_page = 100  # Fetch 100 models per page

    try:
        with httpx.Client(timeout=30.0) as client:
            while True:
                response = client.get(
                    FAL_MODELS_API_BASE,
                    params={"page": page, "per_page": per_page},
                    headers={"Authorization": f"Key {Config.FAL_API_KEY}"},
                )

                if response.status_code != 200:
                    logger.error(
                        f"Fal.ai API returned status {response.status_code}: {response.text[:200]}"
                    )
                    break

                data = response.json()

                # Handle both list response and paginated response formats
                if isinstance(data, list):
                    models = data
                elif isinstance(data, dict):
                    models = data.get("models", data.get("data", data.get("items", [])))
                else:
                    models = []

                if not models:
                    break

                all_models.extend(models)
                logger.debug(f"Fetched page {page} with {len(models)} models")

                # Check if we've fetched all models
                if len(models) < per_page:
                    break

                page += 1

                # Safety limit to prevent infinite loops
                if page > 50:
                    logger.warning("Reached maximum page limit for Fal.ai API")
                    break

        if all_models:
            _fal_api_models_cache = all_models
            logger.info(f"Fetched {len(all_models)} Fal.ai models from API")
        else:
            logger.warning("No models returned from Fal.ai API")

        return all_models

    except httpx.TimeoutException:
        logger.error("Timeout fetching Fal.ai models from API")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch Fal.ai models from API: {e}")
        return []


def load_fal_models_catalog() -> list[dict[str, Any]]:
    """Load Fal.ai models catalog from the static JSON file

    Returns:
        List of Fal.ai model definitions with metadata
    """
    global _fal_static_catalog_cache

    if _fal_static_catalog_cache is not None:
        return _fal_static_catalog_cache

    try:
        catalog_path = Path(__file__).parent.parent / "data" / "fal_catalog.json"

        if catalog_path.exists():
            logger.info(f"Loading Fal.ai models from catalog: {catalog_path}")
            with open(catalog_path) as f:
                raw_data = json.load(f)

            # Filter out metadata objects and only keep actual model objects
            # Model objects must have an "id" field
            _fal_static_catalog_cache = [
                item for item in raw_data if isinstance(item, dict) and "id" in item
            ]

            logger.info(f"Loaded {len(_fal_static_catalog_cache)} Fal.ai models from catalog")
            return _fal_static_catalog_cache
        else:
            logger.warning(f"Fal.ai catalog not found at {catalog_path}")
            return []
    except Exception as e:
        logger.error(f"Failed to load Fal.ai models catalog: {e}")
        return []


def get_fal_models() -> list[dict[str, Any]]:
    """Get list of all available Fal.ai models

    Tries to fetch from the Fal.ai API first, falls back to static catalog if API fails.

    Returns:
        List of model dictionaries with id, name, type, and description
    """
    # Try API first for most complete/up-to-date model list
    api_models = fetch_fal_models_from_api()
    if api_models:
        return api_models

    # Fall back to static catalog
    logger.info("Falling back to static Fal.ai catalog")
    return load_fal_models_catalog()


def get_fal_models_by_type(model_type: str) -> list[dict[str, Any]]:
    """Get Fal.ai models filtered by type

    Args:
        model_type: Type of model (e.g., "text-to-image", "image-to-video", "text-to-video")

    Returns:
        List of models matching the specified type
    """
    all_models = get_fal_models()
    # API uses "category", static catalog uses "type"
    return [
        model for model in all_models
        if model.get("type") == model_type or model.get("category") == model_type
    ]


def validate_fal_model(model_id: str) -> bool:
    """Check if a model ID is valid in the Fal.ai models list

    Checks both API-fetched models and static catalog.

    Args:
        model_id: Model identifier to validate

    Returns:
        True if model exists, False otherwise
    """
    all_models = get_fal_models()
    # API uses "endpoint_id", static catalog uses "id"
    return any(
        model.get("id") == model_id or model.get("endpoint_id") == model_id
        for model in all_models
    )


def _parse_image_size(size: str) -> tuple[int, int]:
    """Parse size string to width and height

    Args:
        size: Size string in format "WIDTHxHEIGHT"

    Returns:
        Tuple of (width, height)
    """
    try:
        width, height = map(int, size.lower().split("x"))
        return width, height
    except (ValueError, AttributeError):
        return 1024, 1024  # Default size


def _get_fal_image_size_param(size: str) -> dict | str:
    """Convert standard size to Fal.ai image_size parameter

    Fal.ai supports both named presets and custom dimensions

    Args:
        size: Size string in format "WIDTHxHEIGHT"

    Returns:
        Either a preset name (str) or custom dimensions dict
    """
    # Map standard sizes to Fal.ai preset names
    size_mapping = {
        "512x512": "square",
        "1024x1024": "square_hd",
        "768x1024": "portrait_4_3",
        "576x1024": "portrait_16_9",
        "1024x768": "landscape_4_3",
        "1024x576": "landscape_16_9",
    }

    if size in size_mapping:
        return size_mapping[size]

    # For custom sizes, return width and height dict
    width, height = _parse_image_size(size)
    return {"width": width, "height": height}


def _build_fal_payload(
    prompt: str,
    size: str,
    n: int,
    **kwargs,
) -> dict[str, Any]:
    """Build Fal.ai request payload

    Args:
        prompt: Text description
        size: Image size
        n: Number of images
        **kwargs: Additional parameters

    Returns:
        Request payload for Fal.ai API
    """
    payload = {
        "prompt": prompt,
        "num_images": n,
        "image_size": _get_fal_image_size_param(size),
    }

    # Fal.ai model-specific parameters that can be passed through
    fal_params = [
        "negative_prompt",
        "num_inference_steps",
        "seed",
        "guidance_scale",
        "sync_mode",
        "enable_safety_checker",
        "expand_prompt",
        "format",
    ]

    for param in fal_params:
        if param in kwargs:
            payload[param] = kwargs[param]

    return payload


def _extract_images_from_response(fal_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract images from Fal.ai response in OpenAI format

    Fal.ai responses vary by model type. This function handles:
    - Direct image URLs in "images" field
    - Other response formats common to video/3D models

    Args:
        fal_response: Raw response from Fal.ai API

    Returns:
        List of image objects in OpenAI format
    """
    data = []

    if "images" in fal_response:
        # Standard image generation response
        for img in fal_response["images"]:
            if isinstance(img, dict):
                data.append({
                    "url": img.get("url"),
                    "b64_json": None,
                })
            elif isinstance(img, str):
                # Sometimes Fal returns just URLs
                data.append({
                    "url": img,
                    "b64_json": None,
                })
    elif "image" in fal_response:
        # Single image response
        img = fal_response["image"]
        if isinstance(img, dict):
            data.append({
                "url": img.get("url"),
                "b64_json": None,
            })
        elif isinstance(img, str):
            data.append({
                "url": img,
                "b64_json": None,
            })
    elif "url" in fal_response:
        # Direct URL response
        data.append({
            "url": fal_response["url"],
            "b64_json": None,
        })

    return data


def make_fal_image_request(
    prompt: str,
    model: str = "fal-ai/stable-diffusion-v15",
    size: str = "1024x1024",
    n: int = 1,
    **kwargs,
) -> dict[str, Any]:
    """Make image generation request to Fal.ai

    This endpoint supports ALL 839+ models available on Fal.ai!

    You can use ANY model from https://fal.ai/models by passing its model ID.
    The catalog includes popular models plus hundreds more across all categories.

    POPULAR MODELS:
    Text-to-Image:
      - fal-ai/flux-pro/v1.1-ultra - Highest quality FLUX model
      - fal-ai/flux/dev - Fast, high-quality generation
      - fal-ai/flux/schnell - Ultra-fast generation (1-4 steps)
      - fal-ai/imagen4/preview - Google's Imagen 4
      - fal-ai/recraft/v3/text-to-image - Recraft v3
      - fal-ai/stable-diffusion-v15 - Classic default
      - fal-ai/aura-flow - High-quality generation
      - fal-ai/omnigen-v1 - Versatile generation

    Text-to-Video:
      - fal-ai/veo3.1 - Google Veo 3.1 (latest)
      - fal-ai/sora-2/text-to-video - OpenAI Sora 2
      - fal-ai/sora-2/text-to-video/pro - Sora 2 Pro
      - fal-ai/kling-video/v2.5-turbo/pro/text-to-video - Kling Turbo
      - fal-ai/minimax/video-01 - MiniMax Video
      - fal-ai/wan-25-preview/text-to-video - WAN 2.5

    Image-to-Video:
      - fal-ai/veo3.1/image-to-video
      - fal-ai/sora-2/image-to-video
      - fal-ai/kling-video/v2.5-turbo/pro/image-to-video
      - fal-ai/wan-25-preview/image-to-video

    Plus 800+ more models for:
      - Image editing, upscaling, background removal
      - Video-to-video, lipsync, effects
      - Text-to-speech, audio generation
      - 3D generation, LoRA training
      - And much more!

    USAGE:
    Browse all models at https://fal.ai/models and use the model ID directly.
    Example: model="fal-ai/flux/dev" or model="bria/fibo/generate"

    Args:
        prompt: Text description of the content to generate
        model: Model ID from https://fal.ai/models (default: "fal-ai/stable-diffusion-v15")
               Supports ALL 839+ Fal.ai models - just pass the model ID!
        size: Image dimensions (e.g., "512x512", "1024x1024")
        n: Number of images to generate
        **kwargs: Model-specific parameters (negative_prompt, guidance_scale, etc.)

    Returns:
        Dict containing generated content in OpenAI-compatible format

    Raises:
        ValueError: If API key is not configured
        httpx.HTTPStatusError: If API request fails
    """
    if not Config.FAL_API_KEY:
        logger.error("FAL_API_KEY not configured")
        raise ValueError(
            "Fal.ai API key not configured. Please set FAL_API_KEY environment variable"
        )

    api_url = f"{FAL_API_BASE}/{model}"
    headers = {
        "Authorization": f"Key {Config.FAL_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        logger.info(f"Making Fal.ai request to {api_url} for model {model}")
        logger.debug(f"Request parameters: prompt={prompt[:50]}..., size={size}, n={n}")

        # Build request payload
        payload = _build_fal_payload(prompt, size, n, **kwargs)

        # Submit request using context manager for proper resource cleanup
        with httpx.Client(timeout=FAL_REQUEST_TIMEOUT) as client:
            response = client.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
            fal_response = response.json()

        logger.info(f"Fal.ai request completed successfully for model {model}")

        # Extract images from response
        data = _extract_images_from_response(fal_response)

        if not data:
            logger.warning(f"Fal.ai response contained no images: {fal_response}")

        return {
            "created": int(time.time()),
            "data": data,
            "provider": "fal",
            "model": model,
        }

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Fal.ai HTTP {e.response.status_code} error for model {model}: {e.response.text}"
        )
        raise
    except httpx.RequestError as e:
        logger.error(f"Fal.ai request error for model {model}: {e}")
        raise
    except Exception as e:
        logger.error(f"Fal.ai request failed for model {model}: {e}", exc_info=True)
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_fal_model(fal_model: dict) -> dict | None:
    """Normalize Fal.ai catalog entries to resemble OpenRouter model shape

    Fal.ai features:
    - 839+ models across text-to-image, text-to-video, image-to-video, etc.
    - Models include FLUX, Stable Diffusion, Veo, Sora, and many more
    - Supports image, video, audio, and 3D generation

    Handles both static catalog format (uses "id") and API format (uses "endpoint_id")
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    # API returns "endpoint_id", static catalog uses "id"
    model_id = fal_model.get("endpoint_id") or fal_model.get("id")
    if not model_id:
        logger.warning("Fal.ai model missing 'id'/'endpoint_id' field: %s", sanitize_for_logging(str(fal_model)))
        return None

    # Extract provider from model ID (e.g., "fal-ai/flux-pro" -> "fal-ai")
    provider_slug = model_id.split("/")[0] if "/" in model_id else "fal-ai"

    # Use title (API) or name (catalog) or derive from ID
    raw_display_name = fal_model.get("title") or fal_model.get("name") or model_id.split("/")[-1]
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get description
    description = fal_model.get("description", f"Fal.ai {display_name} model")

    # Determine modality based on type or category (API uses "category")
    model_type = fal_model.get("type") or fal_model.get("category", "text-to-image")
    modality_map = {
        "text-to-image": MODALITY_TEXT_TO_IMAGE,
        "text-to-video": "text->video",
        "image-to-image": "image->image",
        "image-to-video": "image->video",
        "video-to-video": "video->video",
        "text-to-audio": MODALITY_TEXT_TO_AUDIO,
        "text-to-speech": MODALITY_TEXT_TO_AUDIO,
        "audio-to-audio": "audio->audio",
        "image-to-3d": "image->3d",
        "vision": "image->text",
    }
    modality = modality_map.get(model_type, MODALITY_TEXT_TO_IMAGE)

    # Parse input/output modalities
    input_mod, output_mod = modality.split("->") if "->" in modality else ("text", "image")

    architecture = {
        "modality": modality,
        "input_modalities": [input_mod],
        "output_modalities": [output_mod],
        "model_type": model_type,
        "tags": fal_model.get("tags", []),
    }

    # Fal.ai doesn't expose pricing in catalog, set to null
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
    }

    slug = model_id
    canonical_slug = model_id

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": canonical_slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": None,
        "description": description,
        "context_length": None,  # Not applicable for image/video models
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://fal.ai",
        "model_logo_url": None,
        "source_gateway": "fal",
        "raw_fal": fal_model,
    }

    return enrich_model_with_pricing(normalized, "fal")


def fetch_models_from_fal():
    """Fetch models from Fal.ai catalog

    Loads models from the static Fal.ai catalog JSON file which contains
    curated models from the 839+ available on fal.ai
    """
    try:
        # Get models from catalog
        raw_models = get_fal_models()

        if not raw_models:
            logger.warning("No Fal.ai models found in catalog")
            return []

        # Normalize models
        normalized_models = [normalize_fal_model(model) for model in raw_models if model]

        _fal_models_cache["data"] = normalized_models
        _fal_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} Fal.ai models from catalog")
        return _fal_models_cache["data"]
    except Exception as e:
        logger.error(f"Failed to fetch models from Fal.ai catalog: {e}")
        return []
