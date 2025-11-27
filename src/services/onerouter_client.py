import logging

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_onerouter_pooled_client
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)


def get_onerouter_client():
    """Get OneRouter client with connection pooling for better performance

    OneRouter provides OpenAI-compatible API endpoints with automatic fallbacks,
    prompt caching (enabled by default), and multimodal support.
    """
    try:
        if not Config.ONEROUTER_API_KEY:
            raise ValueError("OneRouter API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_onerouter_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize OneRouter client: {e}")
        capture_provider_error(e, provider='onerouter', endpoint='client_init')
        raise


def make_onerouter_request_openai(messages, model, **kwargs):
    """Make request to OneRouter using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet@20240620')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_onerouter_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"OneRouter request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions'
        )
        raise


def make_onerouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to OneRouter using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_onerouter_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"OneRouter streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions (stream)'
        )
        raise


def process_onerouter_response(response):
    """Process OneRouter response to extract relevant data"""
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
        logger.error(f"Failed to process OneRouter response: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='response_processing'
        )
        raise


def fetch_models_from_onerouter():
    """Fetch models from OneRouter API

    OneRouter provides access to multiple AI models through their API.
    This function fetches the list of available models from their endpoint.
    """
    try:
        if not Config.ONEROUTER_API_KEY:
            logger.warning("OneRouter API key not configured, cannot fetch models")
            return []

        headers = {
            "Authorization": f"Bearer {Config.ONEROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        # Try the standard OpenAI-compatible models endpoint
        response = httpx.get(
            "https://llm.onerouter.pro/v1/models",
            headers=headers,
            timeout=10.0
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Transform to our standard format
        transformed_models = []
        for model in models:
            model_id = model.get("id", "")
            transformed_model = {
                "id": model_id,
                "slug": model_id,
                "canonical_slug": model_id,
                "name": model.get("id", "Unknown Model"),
                "description": f"OneRouter model: {model_id}",
                "context_length": model.get("context_window", 4096),
                "architecture": {
                    "modality": "text->text",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {
                    "prompt": "0",  # Pricing varies by model, set defaults
                    "completion": "0",
                    "request": "0",
                    "image": "0",
                },
                "provider_slug": "onerouter",
                "source_gateway": "onerouter",
            }
            transformed_models.append(transformed_model)

        logger.info(f"Successfully fetched {len(transformed_models)} models from OneRouter")
        return transformed_models

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching OneRouter models: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return []
    except Exception as e:
        logger.error(f"Failed to fetch models from OneRouter: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return []
