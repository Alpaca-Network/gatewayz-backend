import logging

from openai import OpenAI

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools

# Initialize logging
logger = logging.getLogger(__name__)


def get_alpaca_network_client():
    """Get Alpaca Network client using OpenAI-compatible interface

    Alpaca Network provides access to DeepSeek and other models via Anyscale infrastructure
    """
    try:
        if not Config.ALPACA_NETWORK_API_KEY:
            raise ValueError("Alpaca Network API key not configured")

        return OpenAI(
            base_url="https://deepseek-v3-1-b18ty.cld-kvytpjjrw13e2gvq.s.anyscaleuserdata.com",
            api_key=Config.ALPACA_NETWORK_API_KEY,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Alpaca Network client: {e}")
        raise


def make_alpaca_network_request_openai(messages, model, **kwargs):
    """Make request to Alpaca Network using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_alpaca_network_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Alpaca Network request failed: {e}")
        raise


def make_alpaca_network_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Alpaca Network using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_alpaca_network_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Alpaca Network streaming request failed: {e}")
        raise


def process_alpaca_network_response(response):
    """Process Alpaca Network response to extract relevant data"""
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
        logger.error(f"Failed to process Alpaca Network response: {e}")
        raise


def fetch_models_from_alpaca_network():
    """Fetch models from Alpaca Network API

    Alpaca Network provides access to DeepSeek models via Anyscale infrastructure.
    Returns a hardcoded list of known available models.
    """
    logger.info("Returning known Alpaca Network models (DeepSeek via Anyscale)")

    # Hardcoded list of known Alpaca Network models
    # Alpaca Network uses Anyscale infrastructure with DeepSeek models
    return [
        {
            "id": "deepseek-v3",
            "slug": "deepseek-v3",
            "canonical_slug": "deepseek-v3",
            "name": "DeepSeek V3",
            "description": "DeepSeek V3 model available through Alpaca Network's Anyscale infrastructure. A powerful language model with strong reasoning and coding capabilities.",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.14",
                "completion": "0.28",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "alpaca",
            "source_gateway": "alpaca",
        },
        {
            "id": "deepseek-r1",
            "slug": "deepseek-r1",
            "canonical_slug": "deepseek-r1",
            "name": "DeepSeek R1",
            "description": "DeepSeek R1 reasoning model available through Alpaca Network. Features chain-of-thought reasoning capabilities.",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.55",
                "completion": "2.19",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "alpaca",
            "source_gateway": "alpaca",
        },
    ]
