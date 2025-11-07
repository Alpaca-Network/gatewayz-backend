"""
Vercel AI SDK client integration.

This module provides integration with the Vercel AI SDK via the openrouter provider,
which serves as a compatible endpoint for AI SDK requests.

The Vercel AI SDK is primarily a TypeScript/JavaScript toolkit. For Python backends,
we route AI SDK requests through our OpenRouter integration which provides the same
model catalog and features.

Documentation: https://ai-sdk.dev/docs
OpenRouter Integration: https://openrouter.ai/docs
"""

import logging
from openai import OpenAI
from src.config import Config

# Initialize logging
logger = logging.getLogger(__name__)


def validate_ai_sdk_api_key() -> str:
    """Validate that AI SDK API key is configured.

    Returns:
        str: The validated API key

    Raises:
        ValueError: If API key is not configured
    """
    api_key = Config.AI_SDK_API_KEY
    if not api_key:
        logger.error("AI_SDK_API_KEY is not configured")
        raise ValueError("AI_SDK_API_KEY not configured")
    return api_key


def get_ai_sdk_client():
    """Get AI SDK compatible client using OpenRouter.

    The Vercel AI SDK is a TypeScript/JavaScript toolkit for building AI applications.
    Since this is a Python backend, we provide AI SDK compatibility through our
    OpenRouter integration which offers the same model catalog and features.

    Returns:
        OpenAI: OpenAI-compatible client for making AI SDK requests

    Raises:
        ValueError: If API key is not configured
    """
    try:
        api_key = validate_ai_sdk_api_key()

        # Use OpenRouter as the compatible endpoint for AI SDK requests
        # OpenRouter provides access to 100+ models from multiple providers
        return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize AI SDK client: {e}")
        raise


def make_ai_sdk_request_openai(messages, model, **kwargs):
    """Make request to AI SDK compatible endpoint using OpenAI client.

    Args:
        messages: List of message objects with 'role' and 'content'
        model: Model name (e.g., "gpt-4-turbo", "claude-3-opus")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Response object from OpenAI client

    Raises:
        ValueError: If API key is not configured
        Exception: If the API request fails
    """
    try:
        client = get_ai_sdk_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except ValueError as e:
        logger.error(f"AI SDK configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"AI SDK request failed: {e}")
        raise


def make_ai_sdk_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to AI SDK compatible endpoint using OpenAI client.

    Args:
        messages: List of message objects with 'role' and 'content'
        model: Model name (e.g., "gpt-4-turbo", "claude-3-opus")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Yields:
        Chunks from the streaming response

    Raises:
        ValueError: If API key is not configured
        Exception: If the API request fails
    """
    try:
        client = get_ai_sdk_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except ValueError as e:
        logger.error(f"AI SDK configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"AI SDK streaming request failed: {e}")
        raise


def process_ai_sdk_response(response):
    """Process response from AI SDK endpoint.

    Args:
        response: Response object from OpenAI client

    Returns:
        dict: Processed response with 'choices' and 'usage' keys
    """
    try:
        return {
            "choices": [
                {
                    "message": {
                        "role": response.choices[0].message.role,
                        "content": response.choices[0].message.content,
                    },
                    "finish_reason": response.choices[0].finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        }
    except Exception as e:
        logger.error(f"Failed to process AI SDK response: {e}")
        raise
