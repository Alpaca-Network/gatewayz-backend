import logging
from typing import AsyncIterator

from fastapi import APIRouter
from openai import AsyncOpenAI, OpenAI

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_openrouter_pooled_client, get_pooled_async_client
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


def get_openrouter_client():
    """Get OpenRouter client with connection pooling for better performance"""
    try:
        if not Config.OPENROUTER_API_KEY:
            raise ValueError("OpenRouter API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_openrouter_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize OpenRouter client: {e}")
        capture_provider_error(e, provider='openrouter', endpoint='client_init')
        raise


def make_openrouter_request_openai(messages, model, **kwargs):
    """Make request to OpenRouter using OpenAI client"""
    try:
        client = get_openrouter_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"OpenRouter request failed: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions'
        )
        raise


def process_openrouter_response(response):
    """Process OpenRouter response to extract relevant data"""
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
        logger.error(f"Failed to process OpenRouter response: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            endpoint='response_processing'
        )
        raise


def make_openrouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to OpenRouter using OpenAI client"""
    try:
        client = get_openrouter_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"OpenRouter streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (stream)'
        )
        raise


def get_openrouter_async_client() -> AsyncOpenAI:
    """Get async OpenRouter client with connection pooling for better performance.

    PERF: Uses AsyncOpenAI for non-blocking streaming, which prevents the
    event loop from being blocked while waiting for the first chunk from the
    AI provider. This is critical for reducing perceived TTFC.
    """
    try:
        if not Config.OPENROUTER_API_KEY:
            raise ValueError("OpenRouter API key not configured")

        return get_pooled_async_client(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=Config.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": Config.OPENROUTER_SITE_URL,
                "X-TitleSection": Config.OPENROUTER_SITE_NAME,
            },
        )
    except Exception as e:
        logger.error(f"Failed to initialize async OpenRouter client: {e}")
        capture_provider_error(e, provider='openrouter', endpoint='async_client_init')
        raise


async def make_openrouter_request_openai_stream_async(messages, model, **kwargs) -> AsyncIterator:
    """Make async streaming request to OpenRouter using AsyncOpenAI client.

    PERF: This async version doesn't block the event loop while waiting for
    the AI provider to start streaming. The caller can yield control back to
    the event loop between chunks, improving overall concurrency.

    Returns:
        AsyncIterator of streaming chunks
    """
    try:
        client = get_openrouter_async_client()
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"OpenRouter async streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (async stream)'
        )
        raise
