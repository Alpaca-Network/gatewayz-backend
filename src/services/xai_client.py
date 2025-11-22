import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_xai_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_xai_client():
    """Get xAI client with connection pooling for better performance

    xAI provides Grok models through their official SDK.
    Falls back to OpenAI SDK with custom base URL if official SDK is not available.
    Base URL (for OpenAI SDK fallback): https://api.x.ai/v1
    """
    try:
        if not Config.XAI_API_KEY:
            raise ValueError("xAI API key not configured")

        # Try using the official xAI SDK first
        try:
            from xai_sdk import Client

            return Client(api_key=Config.XAI_API_KEY)
        except ImportError:
            # Fallback to pooled OpenAI client with xAI base URL for better performance
            logger.info("xAI SDK not available, using pooled OpenAI SDK with xAI base URL")
            return get_xai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize xAI client: {e}")
        raise


def make_xai_request_openai(messages, model, **kwargs):
    """Make request to xAI using official SDK or OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name (e.g., "grok-4", "grok-3", "grok-2", "grok-beta")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_xai_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"xAI request failed: {e}")
        raise


def make_xai_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to xAI using official SDK or OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name (e.g., "grok-4", "grok-3", "grok-2", "grok-beta")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_xai_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"xAI streaming request failed: {e}")
        raise


def process_xai_response(response):
    """Process xAI response to extract relevant data"""
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
        logger.error(f"Failed to process xAI response: {e}")
        raise


def fetch_models_from_xai():
    """Fetch models from xAI API

    xAI does not provide a public API to list available models.
    Returns a hardcoded list of known xAI Grok models instead.
    """
    logger.info("xAI does not provide a public model listing API, returning known Grok models")

    # Hardcoded list of known xAI Grok models
    # These are the models available through xAI's API
    return [
        {
            "id": "grok-beta",
            "slug": "grok-beta",
            "canonical_slug": "grok-beta",
            "name": "Grok Beta",
            "description": "xAI's Grok model (beta version) - A conversational AI assistant",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-2",
            "slug": "grok-2",
            "canonical_slug": "grok-2",
            "name": "Grok 2",
            "description": "xAI's Grok 2 model - Advanced conversational AI",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-2-1212",
            "slug": "grok-2-1212",
            "canonical_slug": "grok-2-1212",
            "name": "Grok 2 (December 2024)",
            "description": "xAI's Grok 2 model from December 2024",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-vision-beta",
            "slug": "grok-vision-beta",
            "canonical_slug": "grok-vision-beta",
            "name": "Grok Vision Beta",
            "description": "xAI's Grok model with vision capabilities (beta)",
            "context_length": 8192,
            "architecture": {
                "modality": "text+image->text",
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
    ]
