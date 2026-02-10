"""
Morpheus AI Gateway client for chat completions.

Morpheus provides a decentralized AI gateway with OpenAI-compatible API endpoints.
Base URL: https://api.mor.org/api/v1
Documentation: https://apidocs.mor.org/
"""

import logging
from datetime import datetime, timezone

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_morpheus_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)

# Morpheus API base URL
MORPHEUS_BASE_URL = "https://api.mor.org/api/v1"


def get_morpheus_client():
    """Get Morpheus client with connection pooling for better performance.

    Morpheus provides OpenAI-compatible API endpoints for various AI models.
    Uses pooled connections for improved request performance.
    """
    try:
        if not Config.MORPHEUS_API_KEY:
            raise ValueError("Morpheus API key not configured")

        return get_morpheus_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Morpheus client: {e}")
        raise


def make_morpheus_request_openai(messages, model, **kwargs):
    """Make request to Morpheus using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_morpheus_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Morpheus request failed: {e}")
        raise


def make_morpheus_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Morpheus using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_morpheus_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Morpheus streaming request failed: {e}")
        raise


def process_morpheus_response(response):
    """Process Morpheus response to extract relevant data."""
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
        logger.error(f"Failed to process Morpheus response: {e}")
        raise


def fetch_models_from_morpheus():
    """Fetch models from Morpheus API.

    Morpheus provides a /models endpoint that returns available models
    in OpenAI-compatible format.

    Updates the cache after fetching to prevent repeated API calls on failures.
    """
    from src.services.model_catalog_cache import cache_gateway_catalog

    def _cache_and_return(models: list) -> list:
        """Update cache with models and timestamp, then return models."""
        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("morpheus", models)
        return models

    try:
        if not Config.MORPHEUS_API_KEY:
            logger.warning("Morpheus API key not configured, returning empty model list")
            return _cache_and_return([])

        headers = {
            "Authorization": f"Bearer {Config.MORPHEUS_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(f"{MORPHEUS_BASE_URL}/models", headers=headers, timeout=10.0)
        response.raise_for_status()

        data = response.json()
        models = data.get("data", [])

        # Transform to our standard model format
        transformed_models = []
        for model in models:
            model_id = model.get("id", "")
            if not model_id:
                continue  # Skip models with empty or missing IDs
            transformed_models.append(
                {
                    "id": f"morpheus/{model_id}",
                    "slug": f"morpheus/{model_id}",
                    "canonical_slug": f"morpheus/{model_id}",
                    "name": model.get("id", model_id),
                    "description": f"Morpheus AI Gateway model: {model_id}",
                    "context_length": model.get("context_length", 4096),
                    "architecture": {
                        "modality": "text->text",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                    "pricing": {
                        "prompt": "0",  # Morpheus pricing is via MOR tokens
                        "completion": "0",
                        "request": "0",
                        "image": "0",
                    },
                    "provider_slug": "morpheus",
                    "source_gateway": "morpheus",
                }
            )

        logger.info(f"Fetched {len(transformed_models)} models from Morpheus")
        return _cache_and_return(transformed_models)

    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch models from Morpheus: {e}")
        return _cache_and_return([])
    except Exception as e:
        logger.error(f"Unexpected error fetching Morpheus models: {e}")
        return _cache_and_return([])
