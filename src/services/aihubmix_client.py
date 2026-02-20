import logging
from datetime import datetime, timezone

from openai import OpenAI

from src.services.model_catalog_cache import cache_gateway_catalog
from src.services.gateway_health_service import clear_gateway_error, set_gateway_error
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_aihubmix_client():
    """Get AiHubMix client using OpenAI-compatible interface

    AiHubMix is a gateway service providing access to multiple AI models
    with a unified OpenAI-compatible API.

    Base URL: https://aihubmix.com/v1
    Documentation: https://aihubmix.com
    """
    try:
        api_key = Config.AIHUBMIX_API_KEY
        if not api_key:
            raise ValueError(
                "AiHubMix API key not configured. Please set AIHUBMIX_API_KEY environment variable."
            )

        app_code = Config.AIHUBMIX_APP_CODE
        if not app_code:
            raise ValueError(
                "AiHubMix APP-Code not configured. Please set AIHUBMIX_APP_CODE environment variable."
            )

        headers = {
            "APP-Code": app_code,
            "Content-Type": "application/json",
        }

        return OpenAI(
            base_url="https://aihubmix.com/v1",
            api_key=api_key,
            default_headers=headers,
        )
    except Exception as e:
        logger.error(f"Failed to initialize AiHubMix client: {e}")
        raise


def make_aihubmix_request_openai(messages, model, **kwargs):
    """Make request to AiHubMix using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4o", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_aihubmix_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"AiHubMix request failed: {e}")
        raise


def make_aihubmix_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to AiHubMix using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4o", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_aihubmix_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"AiHubMix streaming request failed: {e}")
        raise


def process_aihubmix_response(response):
    """Process AiHubMix response to extract relevant data"""
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
        logger.error(f"Failed to process AiHubMix response: {e}")
        raise


def fetch_model_pricing_from_aihubmix(model_id: str):
    """Fetch pricing information for a specific model from AiHubMix

    AiHubMix routes requests to various providers (OpenAI, Anthropic, etc.)
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

        # AiHubMix doesn't expose a pricing API - use cross-reference with OpenRouter
        return get_provider_pricing_for_aihubmix_model(model_id)

    except Exception as e:
        logger.error(f"Failed to fetch pricing for AiHubMix model {model_id}: {e}")
        return None


def get_provider_pricing_for_aihubmix_model(model_id: str):
    """Get pricing for an AiHubMix model by looking up the underlying provider's pricing

    AiHubMix routes models to providers like OpenAI, Anthropic, etc.
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


def normalize_aihubmix_model_with_pricing(model: dict) -> dict | None:
    """Normalize AiHubMix model with pricing data from their API

    AiHubMix API returns pricing in USD per 1K tokens:
    - input: cost per 1K input tokens
    - output: cost per 1K output tokens

    We convert to per-token pricing (divide by 1000) to match the format used by
    all other gateways (OpenRouter, DeepInfra, etc.) and expected by calculate_cost().

    Example: $1.25/1K tokens -> $0.00125/token (same as OpenRouter format)

    Note: AiHubMix API may return 'id' or 'model_id' depending on the endpoint version.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    # Support both 'id' and 'model_id' field names for API compatibility
    provider_model_id = model.get("id") or model.get("model_id")
    if not provider_model_id:
        # Use debug level to avoid excessive logging during catalog refresh
        logger.debug(
            "AiHubMix model missing both 'id' and 'model_id' fields: %s",
            sanitize_for_logging(str(model)),
        )
        return None

    try:
        # Extract pricing from the API response
        # AiHubMix returns pricing per 1K tokens
        # Use pricing_normalization to convert to per-token format
        from src.services.pricing_normalization import normalize_pricing_dict, PricingFormat

        pricing_data = model.get("pricing", {})

        # Normalize pricing from per-1K to per-token format
        normalized_pricing = normalize_pricing_dict(pricing_data, PricingFormat.PER_1K_TOKENS)

        # Filter out models with zero pricing (free models can drain credits)
        if (
            float(normalized_pricing.get("prompt", 0)) == 0
            and float(normalized_pricing.get("completion", 0)) == 0
        ):
            logger.debug(f"Filtering out AiHubMix model {provider_model_id} with zero pricing")
            return None

        # Get model name, falling back to provider_model_id
        model_name = model.get("name") or provider_model_id

        # Get description from 'desc' or 'description' field
        description = model.get("description") or model.get("desc") or "Model from AiHubMix"

        # Determine input modalities from model data
        input_modalities_str = model.get("input_modalities", "")
        if input_modalities_str and "image" in input_modalities_str.lower():
            input_modalities = ["text", "image"]
        else:
            input_modalities = ["text"]

        normalized = {
            "id": provider_model_id,
            "slug": f"aihubmix/{provider_model_id}",
            "canonical_slug": f"aihubmix/{provider_model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": model.get("created_at"),
            "description": description,
            "context_length": model.get("context_length") or 4096,
            "architecture": {
                "modality": MODALITY_TEXT_TO_TEXT,
                "input_modalities": input_modalities,
                "output_modalities": ["text"],
                "instruct_type": "chat",
            },
            "pricing": normalized_pricing,
            "per_request_limits": None,
            "supported_parameters": [],
            "default_parameters": {},
            "provider_slug": "aihubmix",
            "provider_site_url": "https://aihubmix.com",
            "model_logo_url": None,
            "source_gateway": "aihubmix",
            "pricing_source": "aihubmix-api",
        }
        return normalized
    except Exception as e:
        logger.error("Failed to normalize AiHubMix model: %s", sanitize_for_logging(str(e)))
        return None


def normalize_aihubmix_model(model) -> dict | None:
    """Normalize AiHubMix model to catalog schema

    AiHubMix models use OpenAI-compatible naming conventions.
    Supports both object-style (attributes) and dict-style models.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    # Support both attribute and dict access, and both 'id' and 'model_id' field names
    if isinstance(model, dict):
        provider_model_id = model.get("id") or model.get("model_id")
        raw_model_name = model.get("name") or provider_model_id
        created_at = model.get("created_at")
        description = model.get("description") or model.get("desc") or "Model from AiHubMix"
        context_length = model.get("context_length") or 4096
    else:
        provider_model_id = getattr(model, "id", None) or getattr(model, "model_id", None)
        raw_model_name = getattr(model, "name", provider_model_id)
        created_at = getattr(model, "created_at", None)
        description = (
            getattr(model, "description", None)
            or getattr(model, "desc", None)
            or "Model from AiHubMix"
        )
        context_length = getattr(model, "context_length", 4096)

    if not provider_model_id:
        # Use debug level to avoid excessive logging during catalog refresh
        logger.debug(
            "AiHubMix model missing both 'id' and 'model_id' fields: %s",
            sanitize_for_logging(str(model)),
        )
        return None

    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": provider_model_id,
            "slug": f"aihubmix/{provider_model_id}",
            "canonical_slug": f"aihubmix/{provider_model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": created_at,
            "description": description,
            "context_length": context_length,
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
            "provider_slug": "aihubmix",
            "provider_site_url": "https://aihubmix.com",
            "model_logo_url": None,
            "source_gateway": "aihubmix",
        }
        return enrich_model_with_pricing(normalized, "aihubmix")
    except Exception as e:
        logger.error("Failed to normalize AiHubMix model: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_aihubmix():
    """Fetch models from AiHubMix via their public API

    AiHubMix provides access to models through a unified OpenAI-compatible endpoint.
    The API at https://aihubmix.com/api/v1/models includes pricing information.
    """
    try:
        import requests

        # Fetch from AiHubMix public API which includes pricing
        response = requests.get(
            "https://aihubmix.com/api/v1/models",
            timeout=30,
            headers={"Accept": "application/json"},
        )

        if response.status_code != 200:
            error_msg = f"AiHubMix API returned status {response.status_code}"
            logger.warning(error_msg)
            set_gateway_error("aihubmix", error_msg)
            return []

        data = response.json()
        models_data = data.get("data", [])

        if not models_data:
            logger.warning("No models returned from AiHubMix")
            return []

        # Normalize models and filter out None (models without valid pricing)
        normalized_models = [
            m
            for m in (
                normalize_aihubmix_model_with_pricing(model) for model in models_data if model
            )
            if m
        ]

        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("aihubmix", normalized_models)

        # Clear error state on success
        clear_gateway_error("aihubmix")

        logger.info(f"Fetched {len(normalized_models)} models from AiHubMix")
        return normalized_models
    except requests.exceptions.Timeout as e:
        error_msg = f"AiHubMix API timeout: {sanitize_for_logging(str(e))}"
        logger.error(error_msg)
        set_gateway_error("aihubmix", error_msg)
        return []
    except Exception as e:
        error_msg = f"Failed to fetch models from AiHubMix: {sanitize_for_logging(str(e))}"
        logger.error(error_msg)
        set_gateway_error("aihubmix", error_msg)
        return []
