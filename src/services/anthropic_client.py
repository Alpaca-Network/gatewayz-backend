"""Anthropic API client for direct inference.

Anthropic provides the official API for Claude models including Claude 3.5 Sonnet,
Claude 3 Opus, and Claude 3 Haiku. This client routes requests directly to the
Anthropic API instead of through OpenRouter, enabling direct access to
Anthropic-specific features.

API Documentation: https://docs.anthropic.com/en/api/getting-started
"""

import logging
from datetime import datetime, timezone

import httpx

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_anthropic_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)


def get_anthropic_client():
    """Get Anthropic client with connection pooling for better performance.

    Anthropic provides an OpenAI-compatible API endpoint.
    See: https://docs.anthropic.com/en/api/openai-sdk
    """
    try:
        if not Config.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key not configured")

        # Use pooled client for better performance
        return get_anthropic_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        raise


def make_anthropic_request(messages, model, **kwargs):
    """Make request to Anthropic using the OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet-20241022', 'claude-3-opus-20240229')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Anthropic request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_anthropic_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Anthropic request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Anthropic request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Anthropic request failed (encoding error in logging)")
        raise


def make_anthropic_request_stream(messages, model, **kwargs):
    """Make streaming request to Anthropic using the OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet-20241022', 'claude-3-opus-20240229')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Anthropic streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_anthropic_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Anthropic streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Anthropic streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Anthropic streaming request failed (encoding error in logging)")
        raise


def process_anthropic_response(response):
    """Process Anthropic response to extract relevant data.

    Anthropic's OpenAI-compatible endpoint returns OpenAI-format responses,
    so we use the same processing logic.
    """
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
        logger.error(f"Failed to process Anthropic response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def normalize_anthropic_model(anthropic_model: dict) -> dict | None:
    """Normalize Anthropic model entries to resemble OpenRouter model shape

    API response format:
    {
        "id": "claude-3-5-sonnet-20241022",
        "display_name": "Claude 3.5 Sonnet",
        "created_at": "2024-10-22T00:00:00Z",
        "type": "model"
    }
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    try:
        model_id = anthropic_model.get("id")
        if not model_id:
            return None

        slug = f"anthropic/{model_id}"
        provider_slug = "anthropic"

        # Use display_name from API, fall back to formatted model_id
        raw_display_name = anthropic_model.get("display_name") or anthropic_model.get(
            "name", model_id
        )
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)
        created_at = anthropic_model.get("created_at")

        # Generate description based on model
        description = f"Anthropic {display_name} model."

        # Determine context length based on model generation
        # Claude 3.x models all have 200k context
        context_length = 200000

        # Determine max output based on model
        # Claude 3.5 models have 8192 max output, older models have 4096
        if "3-5" in model_id or "3.5" in model_id:
            max_output = 8192
        else:
            max_output = 4096

        # All Claude 3+ models support vision
        has_vision = model_id.startswith("claude-3")

        # Determine modality
        modality = "text+image->text" if has_vision else MODALITY_TEXT_TO_TEXT
        input_modalities = ["text", "image"] if has_vision else ["text"]
        output_modalities = ["text"]

        # Pricing will be enriched from manual pricing data
        pricing = {
            "prompt": None,
            "completion": None,
            "request": None,
            "image": None,
            "web_search": None,
            "internal_reasoning": None,
        }

        architecture = {
            "modality": modality,
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "tokenizer": "claude",
            "instruct_type": "chat",
            "max_output": max_output,
        }

        normalized = {
            "id": slug,
            "slug": slug,
            "canonical_slug": slug,
            "hugging_face_id": None,
            "name": display_name,
            "created": created_at,
            "description": description,
            "context_length": context_length,
            "architecture": architecture,
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "stop_sequences",
            ],
            "default_parameters": {},
            "provider_slug": provider_slug,
            "provider_site_url": "https://anthropic.com",
            "model_logo_url": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/anthropic.svg",
            "source_gateway": "anthropic",
            "raw_anthropic": anthropic_model,
        }

        return enrich_model_with_pricing(normalized, "anthropic")
    except Exception as e:
        logger.error("Failed to normalize Anthropic model: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_anthropic():
    """Fetch models from Anthropic API and normalize to the catalog schema

    Uses the Anthropic Models API: https://docs.anthropic.com/en/api/models-list
    """
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.ANTHROPIC_API_KEY:
            logger.error("Anthropic API key not configured")
            return None

        headers = {
            "x-api-key": Config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        all_models = []
        after_id = None

        # Paginate through all models
        while True:
            url = "https://api.anthropic.com/v1/models"
            params = {"limit": 100}
            if after_id:
                params["after_id"] = after_id

            response = httpx.get(
                url,
                headers=headers,
                params=params,
                timeout=20.0,
            )
            response.raise_for_status()

            payload = response.json()
            models_data = payload.get("data", [])
            all_models.extend(models_data)

            # Check if there are more pages
            if not payload.get("has_more", False):
                break
            after_id = payload.get("last_id")

        # Filter to only include Claude models (exclude any non-chat models)
        chat_models = [
            model for model in all_models if model and model.get("id", "").startswith("claude-")
        ]

        normalized_models = [
            norm_model
            for model in chat_models
            if model
            for norm_model in [normalize_anthropic_model(model)]
            if norm_model is not None
        ]

        cache_gateway_catalog("anthropic", normalized_models)

        # Clear error state on successful fetch
        clear_gateway_error("anthropic")

        logger.info(f"Fetched {len(normalized_models)} Anthropic models from API")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Anthropic HTTP error: %s", error_msg)
        set_gateway_error("anthropic", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Anthropic: %s", error_msg)
        set_gateway_error("anthropic", error_msg)
        return None
