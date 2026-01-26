"""Z.AI API client for GLM models.

Z.AI (Zhipu AI) provides the GLM family of models with an OpenAI-compatible API.
This client routes requests directly to the Z.AI API for access to GLM models
including GLM-4.7 for coding/reasoning tasks.

API Documentation: https://docs.z.ai
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_zai_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_zai_client():
    """Get Z.AI client with connection pooling for better performance.

    Z.AI provides OpenAI-compatible API endpoints for the GLM model family.
    """
    try:
        if not Config.ZAI_API_KEY:
            raise ValueError("Z.AI API key not configured")

        # Use pooled client for better performance
        return get_zai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Z.AI client: {e}")
        raise


def make_zai_request_openai(messages, model, **kwargs):
    """Make request to Z.AI using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'glm-4.7', 'glm-4.5-air')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Z.AI request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_zai_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Z.AI request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Z.AI request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Z.AI request failed (encoding error in logging)")
        raise


def make_zai_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Z.AI using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'glm-4.7', 'glm-4.5-air')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Z.AI streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_zai_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Z.AI streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Z.AI streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Z.AI streaming request failed (encoding error in logging)")
        raise


def process_zai_response(response):
    """Process Z.AI response to extract relevant data.

    Z.AI returns OpenAI-compatible responses, so we use the same
    processing logic as other OpenAI-compatible providers.
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
        logger.error(f"Failed to process Z.AI response: {e}")
        raise
