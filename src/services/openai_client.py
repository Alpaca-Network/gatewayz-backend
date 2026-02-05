"""OpenAI API client for direct inference.

OpenAI provides the official API for GPT models including GPT-4, GPT-4 Turbo,
and GPT-3.5 Turbo. This client routes requests directly to the OpenAI API
instead of through OpenRouter, enabling direct access to OpenAI-specific
features and lower latency.

API Documentation: https://platform.openai.com/docs/api-reference
"""

import logging
from datetime import datetime, timezone

import httpx

from src.cache import _openai_models_cache
from src.config import Config
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# NOTE: extract_message_with_tools is a provider-agnostic utility for OpenAI-compatible
# responses. It lives in anthropic_transformer.py for historical reasons but is used
# across all provider clients. Consider moving to a shared module in a future refactor.
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_openai_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_openai_client():
    """Get OpenAI client with connection pooling for better performance.

    OpenAI provides the standard API that most other providers are compatible with.
    """
    try:
        if not Config.OPENAI_API_KEY:
            raise ValueError("OpenAI API key not configured")

        # Use pooled client for better performance
        return get_openai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        raise


def make_openai_request(messages, model, **kwargs):
    """Make request to OpenAI using the official client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making OpenAI request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_openai_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"OpenAI request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"OpenAI request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("OpenAI request failed (encoding error in logging)")
        raise


def make_openai_request_stream(messages, model, **kwargs):
    """Make streaming request to OpenAI using the official client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making OpenAI streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_openai_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"OpenAI streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"OpenAI streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("OpenAI streaming request failed (encoding error in logging)")
        raise


def process_openai_response(response):
    """Process OpenAI response to extract relevant data.

    OpenAI returns responses in its standard format.
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
        logger.error(f"Failed to process OpenAI response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def normalize_openai_model(openai_model: dict) -> dict | None:
    """Normalize OpenAI model entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    try:
        provider_model_id = openai_model.get("id")
        if not provider_model_id:
            return None

        slug = f"openai/{provider_model_id}"
        provider_slug = "openai"

        # Generate display name
        raw_display_name = provider_model_id.replace("-", " ").replace("_", " ").title()
        # Clean up common patterns
        raw_display_name = raw_display_name.replace("Gpt ", "GPT-")
        raw_display_name = raw_display_name.replace("O1 ", "o1-")
        raw_display_name = raw_display_name.replace("O3 ", "o3-")
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)

        description = f"OpenAI {provider_model_id} model."

        # Determine context length based on model
        # Context lengths are aligned with manual_pricing.json values
        if "gpt-3.5" in provider_model_id:
            context_length = 16385
        elif "gpt-4-32k" in provider_model_id:
            context_length = 32768
        elif "gpt-4o" in provider_model_id:
            context_length = 128000
        elif provider_model_id in ("o1", "o1-2024-12-17", "o3-mini"):
            # Latest o1 and o3-mini have 200k context
            context_length = 200000
        elif "o1" in provider_model_id or "o3" in provider_model_id:
            # o1-preview, o1-mini have 128k context
            context_length = 128000
        elif "gpt-4-turbo" in provider_model_id:
            context_length = 128000
        elif "gpt-4" in provider_model_id:
            # Base gpt-4 models have 8k context
            context_length = 8192
        else:
            # Default fallback
            context_length = 128000

        # Determine modality
        modality = MODALITY_TEXT_TO_TEXT
        input_modalities = ["text"]
        output_modalities = ["text"]
        if "vision" in provider_model_id or "gpt-4o" in provider_model_id or "gpt-4-turbo" in provider_model_id:
            modality = "text+image->text"
            input_modalities = ["text", "image"]

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
            "tokenizer": "tiktoken",
            "instruct_type": "chat",
        }

        normalized = {
            "id": slug,
            "slug": slug,
            "canonical_slug": slug,
            "hugging_face_id": None,
            "name": display_name,
            "created": openai_model.get("created"),
            "description": description,
            "context_length": context_length,
            "architecture": architecture,
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "temperature",
                "max_tokens",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "stop",
            ],
            "default_parameters": {},
            "provider_slug": provider_slug,
            "provider_site_url": "https://openai.com",
            "model_logo_url": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/openai.svg",
            "source_gateway": "openai",
            "raw_openai": openai_model,
        }

        return enrich_model_with_pricing(normalized, "openai")
    except Exception as e:
        logger.error("Failed to normalize OpenAI model: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_openai():
    """Fetch models from OpenAI API and normalize to the catalog schema"""
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.OPENAI_API_KEY:
            logger.error("OpenAI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.openai.com/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        # Filter to only include chat models (GPT models)
        chat_models = [
            model
            for model in raw_models
            if model and model.get("id", "").startswith(("gpt-", "o1-", "o3-", "chatgpt-"))
        ]

        normalized_models = [
            norm_model
            for model in chat_models
            if model
            for norm_model in [normalize_openai_model(model)]
            if norm_model is not None
        ]

        _openai_models_cache["data"] = normalized_models
        _openai_models_cache["timestamp"] = datetime.now(timezone.utc)

        # Clear error state on successful fetch
        clear_gateway_error("openai")

        logger.info(f"Fetched {len(normalized_models)} OpenAI models")
        return _openai_models_cache["data"]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("OpenAI HTTP error: %s", error_msg)
        set_gateway_error("openai", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from OpenAI: %s", error_msg)
        set_gateway_error("openai", error_msg)
        return None
