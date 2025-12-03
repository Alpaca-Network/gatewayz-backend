"""Groq API client for direct inference.

Groq provides extremely fast inference with their LPU (Language Processing Unit)
hardware. This client routes requests directly to the Groq API instead of
through OpenRouter, enabling lower latency and direct access to Groq-specific
features.

API Documentation: https://console.groq.com/docs/api-reference
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_groq_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_groq_client():
    """Get Groq client with connection pooling for better performance.

    Groq provides OpenAI-compatible API endpoints with ultra-fast inference
    powered by their custom LPU hardware.
    """
    try:
        if not Config.GROQ_API_KEY:
            raise ValueError("Groq API key not configured")

        # Use pooled client for better performance
        return get_groq_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        raise


def make_groq_request_openai(messages, model, **kwargs):
    """Make request to Groq using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'llama-3.3-70b-versatile', 'mixtral-8x7b-32768')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Groq request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_groq_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Groq request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Groq request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Groq request failed (encoding error in logging)")
        raise


def make_groq_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Groq using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'llama-3.3-70b-versatile', 'mixtral-8x7b-32768')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Groq streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_groq_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Groq streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Groq streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Groq streaming request failed (encoding error in logging)")
        raise


def process_groq_response(response):
    """Process Groq response to extract relevant data.

    Groq returns OpenAI-compatible responses, so we use the same
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
        logger.error(f"Failed to process Groq response: {e}")
        raise
