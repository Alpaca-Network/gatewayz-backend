import logging
from datetime import datetime, timezone

import httpx

from src.cache import _fireworks_models_cache, clear_gateway_error, set_gateway_error
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_fireworks_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_fireworks_client():
    """Get Fireworks.ai client with connection pooling for better performance

    Fireworks.ai provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.FIREWORKS_API_KEY:
            raise ValueError("Fireworks API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_fireworks_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Fireworks client: {e}")
        raise


def make_fireworks_request_openai(messages, model, **kwargs):
    """Make request to Fireworks.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Fireworks request with model: {model}")
        # Don't log messages content as it might contain emojis that break Windows logging
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_fireworks_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Fireworks request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Fireworks request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
                # Don't log response body as it might contain problematic characters
        except UnicodeEncodeError:
            # Fallback if logging fails due to encoding issues
            logger.error("Fireworks request failed (encoding error in logging)")
        raise


def make_fireworks_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Fireworks.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Fireworks streaming request with model: {model}")
        # Don't log messages content as it might contain emojis that break Windows logging
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_fireworks_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Fireworks streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Fireworks streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
                # Don't log response body as it might contain problematic characters
        except UnicodeEncodeError:
            # Fallback if logging fails due to encoding issues
            logger.error("Fireworks streaming request failed (encoding error in logging)")
        raise


def process_fireworks_response(response):
    """Process Fireworks response to extract relevant data"""
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
        logger.error(f"Failed to process Fireworks response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_fireworks_model(fireworks_model: dict) -> dict:
    """Normalize Fireworks catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = fireworks_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "fireworks", "raw_fireworks": fireworks_model or {}}

    # Fireworks uses format like "accounts/fireworks/models/deepseek-v3p1"
    # We'll keep the full ID as-is
    slug = provider_model_id
    provider_slug = "fireworks"

    raw_display_name = (
        fireworks_model.get("display_name")
        or provider_model_id.split("/")[-1].replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = fireworks_model.get("owned_by")
    base_description = fireworks_model.get("description") or f"Fireworks hosted model {provider_model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    metadata = fireworks_model.get("metadata") or {}
    context_length = metadata.get("context_length") or fireworks_model.get("context_length") or 0

    # Extract pricing information from API response
    pricing_info = fireworks_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Fireworks may return pricing in various formats
    # Check for token-based pricing (cents per token)
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Check for direct dollar-based pricing
    elif "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])

    architecture = {
        "modality": metadata.get("modality", MODALITY_TEXT_TO_TEXT),
        "input_modalities": metadata.get("input_modalities") or ["text"],
        "output_modalities": metadata.get("output_modalities") or ["text"],
        "tokenizer": metadata.get("tokenizer"),
        "instruct_type": metadata.get("instruct_type"),
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": fireworks_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://fireworks.ai",
        "model_logo_url": None,
        "source_gateway": "fireworks",
        "raw_fireworks": fireworks_model,
    }

    return enrich_model_with_pricing(normalized, "fireworks")


def fetch_models_from_fireworks():
    """Fetch models from Fireworks API and normalize to the catalog schema"""
    try:
        if not Config.FIREWORKS_API_KEY:
            logger.error("Fireworks API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.FIREWORKS_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.fireworks.ai/inference/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])
        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_fireworks_model(model)]
            if norm_model is not None
        ]

        _fireworks_models_cache["data"] = normalized_models
        _fireworks_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("fireworks")

        logger.info(f"Fetched {len(normalized_models)} Fireworks models")
        return _fireworks_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Fireworks HTTP error: %s", error_msg)

        # Cache error state to prevent continuous retries
        set_gateway_error("fireworks", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Fireworks: %s", error_msg)

        # Cache error state to prevent continuous retries
        set_gateway_error("fireworks", error_msg)
        return None
