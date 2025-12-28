import logging

from openai import OpenAI

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools

# Initialize logging
logger = logging.getLogger(__name__)


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
