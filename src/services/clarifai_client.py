"""
Clarifai client for LLM inference integration.

This client uses Clarifai's OpenAI-compatible API endpoint to interact with
language models available on the Clarifai platform. It supports both standard
LLM models and specialized models for reasoning and multimodal tasks.

Clarifai provides access to models like Claude, GPT-4, Llama, Mistral, and others
through a unified OpenAI-compatible API.

API Documentation: https://docs.clarifai.com/compute/inference/open-ai/

Model ID Format:
    Models should be specified using Clarifai's URL format:
    - Full URL: https://clarifai.com/{user_id}/{app_id}/models/{model_id}
    - Abbreviated: {user_id}/{app_id}/models/{model_id}
    Example: "openai/chat-completion/models/gpt-4o"
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_clarifai_pooled_client
from src.services.model_catalog_cache import cache_gateway_catalog
from src.services.pricing_lookup import enrich_model_with_pricing

# Initialize logging
logger = logging.getLogger(__name__)


def get_clarifai_client():
    """Get Clarifai client with connection pooling.

    Uses the Clarifai API gateway endpoint that is compatible with OpenAI SDK
    via the universal inference API.
    """
    try:
        # Use pooled client for ~10-20ms performance improvement per request
        return get_clarifai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Clarifai client: {e}")
        raise


def make_clarifai_request_openai(messages, model, **kwargs):
    """Make request to Clarifai using OpenAI-compatible API.

    Args:
        messages: List of message objects in OpenAI format
        model: Model ID in Clarifai format (e.g., "openai/chat-completion/models/gpt-4o")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Response object from Clarifai API (OpenAI-compatible format)
    """
    try:
        client = get_clarifai_client()

        # Log request for debugging
        logger.debug(f"Clarifai request - model: {model}, messages: {len(messages)}")

        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        return response
    except Exception as e:
        logger.error(f"Clarifai request failed: {e}")
        raise


def make_clarifai_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Clarifai using OpenAI-compatible API.

    Args:
        messages: List of message objects in OpenAI format
        model: Model ID in Clarifai format (e.g., "openai/chat-completion/models/gpt-4o")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Streaming response generator from Clarifai API
    """
    try:
        client = get_clarifai_client()

        # Log request for debugging
        logger.debug(f"Clarifai streaming request - model: {model}, messages: {len(messages)}")

        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        return stream
    except Exception as e:
        logger.error(f"Clarifai streaming request failed: {e}")
        raise


def process_clarifai_response(response):
    """Process Clarifai response to extract relevant data.

    Normalizes Clarifai response to standard format compatible with
    the gateway's response handling.

    Args:
        response: Response object from Clarifai API

    Returns:
        Dict with normalized response data
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
        logger.error(f"Failed to process Clarifai response: {e}")
        raise


def fetch_models_from_clarifai():
    """Fetch models from Clarifai API

    Clarifai provides access to multiple AI models through their OpenAI-compatible API.
    This function fetches the list of available models from their endpoint.

    Models are cached with a 1-hour TTL to reduce API calls and improve performance.
    """

    def _cache_and_return(models: list[dict]) -> list[dict]:
        """Cache models in Redis with automatic TTL and error tracking"""
        cache_gateway_catalog("clarifai", models)
        return models

    try:
        if not Config.CLARIFAI_API_KEY:
            logger.warning("Clarifai API key not configured, cannot fetch models")
            return _cache_and_return([])

        logger.info("Fetching models from Clarifai API...")

        # Use the pooled OpenAI client for consistency with the rest of the module
        client = get_clarifai_pooled_client()
        models_response = client.models.list()

        # Convert to list format
        models = list(models_response)
        logger.debug(f"Clarifai API returned {len(models)} models")

        # Transform to our standard format
        transformed_models = []
        filtered_count = 0
        for model in models:
            model_id = getattr(model, "id", "") or ""
            context_length = getattr(model, "context_length", None) or getattr(
                model, "context_window", 4096
            )
            transformed_model = {
                "id": model_id,
                "slug": f"clarifai/{model_id}",
                "canonical_slug": f"clarifai/{model_id}",
                "name": model_id or "Unknown Model",
                "description": f"Clarifai model: {model_id}",
                "context_length": context_length,
                "architecture": {
                    "modality": "text->text",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {
                    "prompt": "0",
                    "completion": "0",
                    "request": "0",
                    "image": "0",
                },
                "provider_slug": "clarifai",
                "source_gateway": "clarifai",
            }
            # Enrich with manual pricing if available; filter out models without valid pricing
            enriched_model = enrich_model_with_pricing(transformed_model, "clarifai")
            if enriched_model is None:
                logger.debug(f"Filtering out Clarifai model {model_id} - no pricing available")
                filtered_count += 1
                continue

            # Additional check: filter models that still have zero pricing after enrichment
            pricing = enriched_model.get("pricing", {})
            try:
                prompt_price = float(pricing.get("prompt", "0") or "0")
                completion_price = float(pricing.get("completion", "0") or "0")
                if prompt_price == 0 and completion_price == 0:
                    logger.debug(
                        f"Filtering out Clarifai model {model_id} - zero pricing after enrichment"
                    )
                    filtered_count += 1
                    continue
            except (ValueError, TypeError):
                pass  # Keep model if we can't parse pricing

            transformed_models.append(enriched_model)

        logger.info(
            f"Successfully fetched {len(transformed_models)} models from Clarifai ({filtered_count} filtered for missing/zero pricing)"
        )
        return _cache_and_return(transformed_models)

    except Exception as e:
        logger.error(f"Failed to fetch models from Clarifai: {type(e).__name__}: {e}")
        return _cache_and_return([])
