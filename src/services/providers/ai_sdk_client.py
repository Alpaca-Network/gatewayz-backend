"""
Vercel AI SDK client integration.

This module provides integration with the Vercel AI Gateway, which is the official
gateway for accessing AI models through Vercel's unified platform.

The Vercel AI SDK is primarily a TypeScript/JavaScript toolkit. For Python backends,
we provide AI SDK compatibility through the Vercel AI Gateway endpoint, which supports
models from OpenAI, Anthropic, Google, xAI, Meta, and other providers.

Documentation: https://ai-sdk.dev/docs
Vercel AI Gateway: https://vercel.com/ai-gateway
"""

import logging
from collections.abc import AsyncIterator

from openai import AsyncOpenAI, OpenAI

from src.config import Config
from src.services.connection_pool import get_pooled_async_client
from src.utils.provider_safety import ProviderError, safe_get_choices, safe_get_usage

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
    """Get AI SDK compatible client using Vercel AI Gateway.

    The Vercel AI SDK is a TypeScript/JavaScript toolkit for building AI applications.
    Since this is a Python backend, we provide AI SDK compatibility through the
    Vercel AI Gateway which is the official gateway for accessing all Vercel AI models.

    The gateway provides access to models from:
    - OpenAI (GPT-5, GPT-4o, etc.)
    - Anthropic (Claude Haiku, Sonnet, Opus)
    - Google (Gemini)
    - xAI (Grok)
    - Meta (Llama)
    - DeepSeek
    - Mistral
    - And many more...

    Base URL: https://ai-gateway.vercel.sh/v1
    Documentation: https://vercel.com/docs/ai-gateway

    Returns:
        OpenAI: OpenAI-compatible client for making AI SDK requests

    Raises:
        ValueError: If API key is not configured
    """
    try:
        api_key = validate_ai_sdk_api_key()

        # Use Vercel AI Gateway as the endpoint for AI SDK requests
        # The gateway provides OpenAI-compatible API for unified model access
        return OpenAI(base_url="https://ai-gateway.vercel.sh/v1", api_key=api_key)
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
        # Safely extract choices with validation
        choices = safe_get_choices(response)
        if not choices:
            raise ProviderError("AI SDK returned empty choices")

        first_choice = choices[0]

        # Safely extract usage information
        usage = safe_get_usage(response)

        return {
            "choices": [
                {
                    "message": {
                        "role": (
                            first_choice.message.role
                            if hasattr(first_choice.message, "role")
                            else "assistant"
                        ),
                        "content": (
                            first_choice.message.content
                            if hasattr(first_choice.message, "content")
                            else ""
                        ),
                    },
                    "finish_reason": (
                        first_choice.finish_reason
                        if hasattr(first_choice, "finish_reason")
                        else "stop"
                    ),
                }
            ],
            "usage": usage,
        }
    except ProviderError:
        raise
    except Exception as e:
        logger.error(f"Failed to process AI SDK response: {e}")
        raise ProviderError(f"AI SDK response processing failed: {e}")


def get_ai_sdk_async_client() -> AsyncOpenAI:
    """Get async AI SDK client with connection pooling for better performance.

    PERF: Uses AsyncOpenAI for non-blocking streaming, which prevents the
    event loop from being blocked while waiting for the first chunk from the
    AI provider. This is critical for reducing perceived TTFC.

    Returns:
        AsyncOpenAI: Async OpenAI-compatible client for making AI SDK requests

    Raises:
        ValueError: If API key is not configured
    """
    try:
        api_key = validate_ai_sdk_api_key()

        return get_pooled_async_client(
            provider="ai_sdk",
            base_url="https://ai-gateway.vercel.sh/v1",
            api_key=api_key,
        )
    except Exception as e:
        logger.error(f"Failed to initialize async AI SDK client: {e}")
        raise


async def make_ai_sdk_request_openai_stream_async(messages, model, **kwargs) -> AsyncIterator:
    """Make async streaming request to AI SDK using AsyncOpenAI client.

    PERF: This async version doesn't block the event loop while waiting for
    the AI provider to start streaming. The caller can yield control back to
    the event loop between chunks, improving overall concurrency.

    Args:
        messages: List of message objects with 'role' and 'content'
        model: Model name (e.g., "gpt-4-turbo", "claude-3-opus")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        AsyncIterator of streaming chunks

    Raises:
        ValueError: If API key is not configured
        Exception: If the API request fails
    """
    try:
        client = get_ai_sdk_async_client()
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except ValueError as e:
        logger.error(f"AI SDK configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"AI SDK async streaming request failed: {e}")
        raise
