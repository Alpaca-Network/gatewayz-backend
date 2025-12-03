"""
Akash ML client for OpenAI-compatible chat completions.

Akash ML provides an OpenAI-compatible API for various AI models.
API Base: https://api.akashml.com/v1
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_akash_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_akash_client():
    """Get Akash ML client with connection pooling for better performance

    Akash ML provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.AKASH_API_KEY:
            raise ValueError("Akash API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_akash_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Akash client: {e}")
        raise


def make_akash_request_openai(messages, model, **kwargs):
    """Make request to Akash ML using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Akash request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_akash_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Akash request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Akash request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Akash request failed (encoding error in logging)")
        raise


def make_akash_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Akash ML using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Akash streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_akash_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Akash streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Akash streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Akash streaming request failed (encoding error in logging)")
        raise


def process_akash_response(response):
    """Process Akash response to extract relevant data"""
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
        logger.error(f"Failed to process Akash response: {e}")
        raise
