"""Anthropic API client for direct inference.

Anthropic provides the official API for Claude models including Claude 3.5 Sonnet,
Claude 3 Opus, and Claude 3 Haiku. This client routes requests directly to the
Anthropic API instead of through OpenRouter, enabling direct access to
Anthropic-specific features.

API Documentation: https://docs.anthropic.com/en/api/getting-started
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_anthropic_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_anthropic_client():
    """Get Anthropic client with connection pooling for better performance.

    Anthropic provides an OpenAI-compatible API endpoint.
    See: https://docs.anthropic.com/en/api/openai-sdk
    """
    try:
        if not Config.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key not configured")

        # Use pooled client for better performance
        return get_anthropic_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        raise


def make_anthropic_request(messages, model, **kwargs):
    """Make request to Anthropic using the OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet-20241022', 'claude-3-opus-20240229')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Anthropic request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_anthropic_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Anthropic request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Anthropic request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Anthropic request failed (encoding error in logging)")
        raise


def make_anthropic_request_stream(messages, model, **kwargs):
    """Make streaming request to Anthropic using the OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet-20241022', 'claude-3-opus-20240229')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Anthropic streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_anthropic_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Anthropic streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Anthropic streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Anthropic streaming request failed (encoding error in logging)")
        raise


def process_anthropic_response(response):
    """Process Anthropic response to extract relevant data.

    Anthropic's OpenAI-compatible endpoint returns OpenAI-format responses,
    so we use the same processing logic.
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
        logger.error(f"Failed to process Anthropic response: {e}")
        raise
