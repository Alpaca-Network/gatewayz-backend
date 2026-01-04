"""OpenAI API client for direct inference.

OpenAI provides the official API for GPT models including GPT-4, GPT-4 Turbo,
and GPT-3.5 Turbo. This client routes requests directly to the OpenAI API
instead of through OpenRouter, enabling direct access to OpenAI-specific
features and lower latency.

API Documentation: https://platform.openai.com/docs/api-reference
"""

import logging

from src.config import Config
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
