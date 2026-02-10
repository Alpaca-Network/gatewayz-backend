import logging
from datetime import datetime, timezone

import httpx
from openai import OpenAI

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"
MODALITY_TEXT_TO_IMAGE = "text->image"
MODALITY_TEXT_TO_AUDIO = "text->audio"


def get_deepinfra_client():
    """Get DeepInfra client using OpenAI-compatible interface

    DeepInfra provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.DEEPINFRA_API_KEY:
            raise ValueError("DeepInfra API key not configured")

        return OpenAI(
            base_url="https://api.deepinfra.com/v1/openai", api_key=Config.DEEPINFRA_API_KEY
        )
    except Exception as e:
        logger.error(f"Failed to initialize DeepInfra client: {e}")
        raise


def make_deepinfra_request_openai(messages, model, **kwargs):
    """Make request to DeepInfra using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_deepinfra_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"DeepInfra request failed: {e}")
        raise


def make_deepinfra_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to DeepInfra using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_deepinfra_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"DeepInfra streaming request failed: {e}")
        raise


def process_deepinfra_response(response):
    """Process DeepInfra response to extract relevant data"""
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process DeepInfra response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_deepinfra_model(deepinfra_model: dict) -> dict:
    """Normalize DeepInfra model to our schema"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    # DeepInfra /models/list uses 'model_name' instead of 'id'
    model_id = deepinfra_model.get("model_name") or deepinfra_model.get("id", "")
    if not model_id:
        return {"source_gateway": "deepinfra", "raw_deepinfra": deepinfra_model or {}}

    provider_slug = model_id.split("/")[0] if "/" in model_id else "deepinfra"
    raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get model type to determine modality
    model_type = deepinfra_model.get("type") or deepinfra_model.get("reported_type") or "text"

    # Build description with deprecation notice if applicable
    base_description = deepinfra_model.get("description") or f"DeepInfra hosted model: {model_id}."
    if deepinfra_model.get("deprecated"):
        replaced_by = deepinfra_model.get("replaced_by")
        if replaced_by:
            base_description = f"{base_description} Note: This model is deprecated and has been replaced by {replaced_by}."
        else:
            base_description = f"{base_description} Note: This model is deprecated."
    description = f"{base_description} Pricing data may vary by region and usage."

    # Extract pricing information
    pricing_info = deepinfra_model.get("pricing", {})
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract token-based pricing (text-generation, embeddings, etc.)
    # DeepInfra returns pricing in cents per token, convert to dollars per token
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Extract image unit pricing (text-to-image models)
    elif pricing_info.get("type") == "image_units" or "cents_per_image_unit" in pricing_info:
        cents_per_image = pricing_info.get("cents_per_image_unit", 0)
        # Convert cents to dollars per image
        if cents_per_image:
            pricing["image"] = str(cents_per_image / 100)

    # If pricing is time-based (legacy image generation), convert to image pricing
    elif pricing_info.get("type") == "time" and model_type in ("text-to-image", "image"):
        cents_per_sec = pricing_info.get("cents_per_sec", 0)
        # Convert cents per second to dollars per image (assume ~5 seconds per image)
        pricing["image"] = str(cents_per_sec * 5 / 100) if cents_per_sec else None

    # Determine modality based on model type
    modality = MODALITY_TEXT_TO_TEXT
    input_modalities = ["text"]
    output_modalities = ["text"]

    if model_type in ("text-to-image", "image"):
        modality = MODALITY_TEXT_TO_IMAGE
        input_modalities = ["text"]
        output_modalities = ["image"]
    elif model_type in ("text-to-speech", "tts"):
        modality = MODALITY_TEXT_TO_AUDIO
        input_modalities = ["text"]
        output_modalities = ["audio"]
    elif model_type in ("speech-to-text", "stt"):
        modality = "audio->text"
        input_modalities = ["audio"]
        output_modalities = ["text"]
    elif model_type == "multimodal":
        modality = "multimodal"
        input_modalities = ["text", "image"]
        output_modalities = ["text"]

    architecture = {
        "modality": modality,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": deepinfra_model.get("created"),
        "description": description,
        "context_length": 0,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": None,
        "model_logo_url": None,
        "source_gateway": "deepinfra",
        "raw_deepinfra": deepinfra_model,
    }

    # Enrich with manual pricing if available
    return enrich_model_with_pricing(normalized, "deepinfra")


def fetch_models_from_deepinfra():
    """Fetch models from DeepInfra API and normalize to the catalog schema"""
    try:
        if not Config.DEEPINFRA_API_KEY:
            logger.error(
                "DeepInfra API key not configured - please set DEEPINFRA_API_KEY environment variable"
            )
            return None

        # Log that we're attempting to fetch
        api_key_preview = (
            Config.DEEPINFRA_API_KEY[:5] + "***" if Config.DEEPINFRA_API_KEY else "NONE"
        )
        logger.info(f"DeepInfra API key found (preview: {api_key_preview})")

        headers = {
            "Authorization": f"Bearer {Config.DEEPINFRA_API_KEY}",
            "Content-Type": "application/json",
        }

        # DeepInfra API - use /models/list endpoint which has better model data
        url = "https://api.deepinfra.com/models/list"
        logger.info("Fetching DeepInfra models from %s", sanitize_for_logging(str(url)))

        response = httpx.get(url, headers=headers, timeout=20.0)

        logger.info(f"DeepInfra API response status: {response.status_code}")
        response.raise_for_status()

        payload = response.json()

        # DeepInfra /models/list returns array directly, not wrapped in an object
        if isinstance(payload, list):
            raw_models = payload
        else:
            raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from DeepInfra")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_deepinfra_model(model)]
            if norm_model is not None
        ]

        cache_gateway_catalog("deepinfra", normalized_models)
        logger.info(f"Successfully cached {len(normalized_models)} DeepInfra models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("DeepInfra HTTP error: %s", error_msg)
        # Error tracking now automatic via Redis cache circuit breaker
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from DeepInfra: %s", error_msg, exc_info=True)
        # Error tracking now automatic via Redis cache circuit breaker
        return None
