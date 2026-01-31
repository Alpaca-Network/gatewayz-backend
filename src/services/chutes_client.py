import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from src.cache import _chutes_models_cache
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


def get_chutes_client():
    """Get Chutes.ai client using OpenAI-compatible interface

    Chutes.ai provides OpenAI-compatible API endpoints for various models
    API endpoint: https://llm.chutes.ai/v1/chat/completions
    """
    try:
        if not Config.CHUTES_API_KEY:
            raise ValueError("Chutes API key not configured")

        return OpenAI(base_url="https://llm.chutes.ai/v1", api_key=Config.CHUTES_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Chutes client: {e}")
        raise


def make_chutes_request_openai(messages, model, **kwargs):
    """Make request to Chutes.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_chutes_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Chutes request failed: {e}")
        raise


def make_chutes_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Chutes.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_chutes_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Chutes streaming request failed: {e}")
        raise


def process_chutes_response(response):
    """Process Chutes response to extract relevant data"""
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
        logger.error(f"Failed to process Chutes response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_chutes_model(chutes_model: dict) -> dict:
    """Normalize Chutes catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = chutes_model.get("id", "")
    if not model_id:
        return {"source_gateway": "chutes", "raw_chutes": chutes_model or {}}

    provider_slug = chutes_model.get("provider", "chutes")
    model_type = chutes_model.get("type", "LLM")
    pricing_per_hour = chutes_model.get("pricing_per_hour", 0.0)

    # FIXED: Convert hourly pricing to per-token pricing (rough estimate)
    # Assume ~1M tokens per hour at average speed
    # pricing_per_hour / 1,000,000 = per-token price
    prompt_price = str(pricing_per_hour / 1000000) if pricing_per_hour > 0 else "0"

    raw_display_name = chutes_model.get("name", model_id.replace("-", " ").replace("_", " ").title())
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = (
        f"Chutes.ai hosted {model_type} model: {model_id}. Pricing: ${pricing_per_hour}/hr."
    )

    # Determine modality based on type
    modality_map = {
        "LLM": MODALITY_TEXT_TO_TEXT,
        "Image Generation": MODALITY_TEXT_TO_IMAGE,
        "Text to Speech": MODALITY_TEXT_TO_AUDIO,
        "Speech to Text": "audio->text",
        "Video": "text->video",
        "Music Generation": MODALITY_TEXT_TO_AUDIO,
        "Embeddings": "text->embedding",
        "Content Moderation": MODALITY_TEXT_TO_TEXT,
        "Other": "multimodal",
    }

    modality = modality_map.get(model_type, MODALITY_TEXT_TO_TEXT)

    pricing = {
        "prompt": prompt_price,
        "completion": prompt_price,
        "request": "0",
        "image": str(pricing_per_hour) if model_type == "Image Generation" else "0",
        "web_search": "0",
        "internal_reasoning": "0",
        "hourly_rate": str(pricing_per_hour),
    }

    architecture = {
        "modality": modality,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    tags = chutes_model.get("tags", [])

    normalized = {
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": None,
        "description": description,
        "context_length": 0,
        "architecture": architecture,
        "pricing": pricing,
        "top_provider": None,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": None,
        "model_logo_url": None,
        "source_gateway": "chutes",
        "model_type": model_type,
        "tags": tags,
        "raw_chutes": chutes_model,
    }

    # Enrich with manual pricing if available (overrides hourly pricing)
    return enrich_model_with_pricing(normalized, "chutes")


def fetch_models_from_chutes_api():
    """Fetch models from Chutes API (if available)"""
    try:
        if not Config.CHUTES_API_KEY:
            logger.error("Chutes API key not configured")
            return None

        # This is a placeholder for future API integration
        # For now, we're using the static catalog
        logger.warning("Chutes API integration not yet implemented, using static catalog")
        return None

    except Exception as e:
        logger.error("Failed to fetch models from Chutes API: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_chutes():
    """Fetch models from Chutes static catalog or API"""
    try:
        # First, try to load from static catalog file
        catalog_path = Path(__file__).parent.parent / "data" / "chutes_catalog.json"

        if catalog_path.exists():
            logger.info(f"Loading Chutes models from static catalog: {catalog_path}")
            with open(catalog_path) as f:
                raw_models = json.load(f)

            normalized_models = [normalize_chutes_model(model) for model in raw_models if model]

            _chutes_models_cache["data"] = normalized_models
            _chutes_models_cache["timestamp"] = datetime.now(timezone.utc)

            logger.info(f"Loaded {len(normalized_models)} models from Chutes static catalog")
            return _chutes_models_cache["data"]

        # If static catalog doesn't exist, try API (if key is configured)
        if Config.CHUTES_API_KEY:
            logger.info("Attempting to fetch Chutes models from API")
            return fetch_models_from_chutes_api()

        logger.warning("Chutes catalog file not found and no API key configured")
        return None

    except Exception as e:
        logger.error("Failed to fetch models from Chutes: %s", sanitize_for_logging(str(e)))
        return None
