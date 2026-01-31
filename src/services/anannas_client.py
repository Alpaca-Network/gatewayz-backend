import logging
from datetime import datetime, timezone

from openai import OpenAI

from src.cache import _anannas_models_cache
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_anannas_client():
    """Get Anannas client using OpenAI-compatible interface

    Anannas provides OpenAI-compatible API endpoints for accessing various models
    """
    try:
        if not Config.ANANNAS_API_KEY:
            raise ValueError("Anannas API key not configured")

        return OpenAI(base_url="https://api.anannas.ai/v1", api_key=Config.ANANNAS_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Anannas client: {e}")
        raise


def make_anannas_request_openai(messages, model, **kwargs):
    """Make request to Anannas using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_anannas_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Anannas request failed: {e}")
        raise


def make_anannas_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Anannas using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_anannas_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Anannas streaming request failed: {e}")
        raise


def process_anannas_response(response):
    """Process Anannas response to extract relevant data"""
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
        logger.error(f"Failed to process Anannas response: {e}")
        raise


def fetch_model_pricing_from_anannas(model_id: str):
    """Fetch pricing information for a specific model from Anannas

    Anannas routes requests to various providers (OpenAI, Anthropic, etc.)
    This function attempts to determine the pricing by cross-referencing
    with known provider pricing from OpenRouter's catalog.

    Args:
        model_id: Model identifier (e.g., "gpt-4o", "claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens, or None if not available
    """
    try:
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return None to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Skipping pricing fetch for {model_id} (catalog building in progress)")
            return None

        # Anannas doesn't expose a pricing API - use cross-reference with OpenRouter
        return get_provider_pricing_for_anannas_model(model_id)

    except Exception as e:
        logger.error(f"Failed to fetch pricing for Anannas model {model_id}: {e}")
        return None


def get_provider_pricing_for_anannas_model(model_id: str):
    """Get pricing for an Anannas model by looking up the underlying provider's pricing

    Anannas routes models to providers like OpenAI, Anthropic, etc.
    We can determine pricing by cross-referencing with OpenRouter's catalog.

    Args:
        model_id: Model identifier (e.g., "gpt-4o", "claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens
    """
    try:
        from src.services.models import _is_building_catalog
        from src.services.pricing import get_model_pricing

        # If we're building the catalog, return None to avoid circular dependency
        if _is_building_catalog():
            logger.debug(
                f"Skipping provider pricing lookup for {model_id} (catalog building in progress)"
            )
            return None

        # Try the full model ID first
        pricing = get_model_pricing(model_id)
        if pricing and pricing.get("found"):
            return {
                "prompt": pricing.get("prompt", "0"),
                "completion": pricing.get("completion", "0"),
            }

        # Try without the provider prefix
        model_name_only = model_id.split("/")[-1] if "/" in model_id else model_id
        pricing = get_model_pricing(model_name_only)
        if pricing and pricing.get("found"):
            return {
                "prompt": pricing.get("prompt", "0"),
                "completion": pricing.get("completion", "0"),
            }

        return None

    except ImportError:
        logger.debug("pricing module not available for cross-reference")
        return None
    except Exception as e:
        logger.debug(f"Failed to get provider pricing for {model_id}: {e}")
        return None


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_anannas_model(model) -> dict | None:
    """Normalize Anannas model to catalog schema

    Anannas models use OpenAI-compatible naming conventions.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Anannas model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    raw_model_name = getattr(model, "name", model_id)
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": model_id,
            "slug": f"anannas/{model_id}",
            "canonical_slug": f"anannas/{model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": getattr(model, "created_at", None),
            "description": getattr(model, "description", "Model from Anannas"),
            "context_length": getattr(model, "context_length", 4096),
            "architecture": {
                "modality": MODALITY_TEXT_TO_TEXT,
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "instruct_type": "chat",
            },
            "pricing": {
                "prompt": "0",
                "completion": "0",
                "request": "0",
                "image": "0",
            },
            "per_request_limits": None,
            "supported_parameters": [],
            "default_parameters": {},
            "provider_slug": "anannas",
            "provider_site_url": "https://api.anannas.ai",
            "model_logo_url": None,
            "source_gateway": "anannas",
        }
        return enrich_model_with_pricing(normalized, "anannas")
    except Exception as e:
        logger.error("Failed to normalize Anannas model: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_anannas():
    """Fetch models from Anannas via OpenAI-compatible API

    Anannas provides access to various models through a unified OpenAI-compatible endpoint.
    """
    try:
        # Check if API key is configured
        if not Config.ANANNAS_API_KEY:
            logger.warning("Anannas API key not configured - skipping model fetch")
            return []

        client = get_anannas_client()
        response = client.models.list()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Anannas")
            return []

        # Normalize models and filter out None (models without pricing)
        normalized_models = [
            m for m in (normalize_anannas_model(model) for model in response.data if model) if m
        ]

        _anannas_models_cache["data"] = normalized_models
        _anannas_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} models from Anannas")
        return _anannas_models_cache["data"]
    except Exception as e:
        logger.error("Failed to fetch models from Anannas: %s", sanitize_for_logging(str(e)))
        return []
