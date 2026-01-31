import logging
from datetime import datetime, timezone

import httpx

from src.cache import _together_models_cache, clear_gateway_error, set_gateway_error
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_together_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_together_client():
    """Get Together.ai client with connection pooling for better performance

    Together.ai provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.TOGETHER_API_KEY:
            raise ValueError("Together API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_together_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Together client: {e}")
        raise


def make_together_request_openai(messages, model, **kwargs):
    """Make request to Together.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_together_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Together request failed: {e}")
        raise


def make_together_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Together.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_together_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Together streaming request failed: {e}")
        raise


def process_together_response(response):
    """Process Together response to extract relevant data"""
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
        logger.error(f"Failed to process Together response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_together_model(together_model: dict) -> dict:
    """Normalize Together catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = together_model.get("id")
    if not model_id:
        return {"source_gateway": "together", "raw_together": together_model or {}}

    slug = model_id
    provider_slug = "together"

    # Get display name from API or generate from model ID
    raw_display_name = (
        together_model.get("display_name")
        or model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove parentheses with size info, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = together_model.get("owned_by") or together_model.get("organization")
    base_description = together_model.get("description") or f"Together hosted model {model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    context_length = together_model.get("context_length", 0)

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract pricing if available
    pricing_info = together_model.get("pricing", {})
    if pricing_info:
        pricing["prompt"] = pricing_info.get("input")
        pricing["completion"] = pricing_info.get("output")

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": together_model.get("config", {}).get("tokenizer"),
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": together_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://together.ai",
        "model_logo_url": None,
        "source_gateway": "together",
        "raw_together": together_model,
    }

    return enrich_model_with_pricing(normalized, "together")


def fetch_models_from_together():
    """Fetch models from Together.ai API and normalize to the catalog schema"""
    try:
        if not Config.TOGETHER_API_KEY:
            logger.error("Together API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.TOGETHER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.together.xyz/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        # Together API returns a list directly, not wrapped in {"data": [...]}
        raw_models = payload if isinstance(payload, list) else payload.get("data", [])
        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_together_model(model)]
            if norm_model is not None
        ]

        _together_models_cache["data"] = normalized_models
        _together_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("together")

        logger.info(f"Fetched {len(normalized_models)} Together models")
        return _together_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Together HTTP error: %s", error_msg)
        set_gateway_error("together", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Together: %s", error_msg)
        set_gateway_error("together", error_msg)
        return None
