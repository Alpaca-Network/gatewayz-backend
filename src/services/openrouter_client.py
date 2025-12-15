import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from openai import AsyncOpenAI, BadRequestError, APIStatusError

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_openrouter_pooled_client, get_pooled_async_client
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)


def _extract_error_details(e: Exception, model: str, kwargs: dict) -> dict:
    """Extract detailed error information from OpenAI/OpenRouter exceptions.

    This helps diagnose 400 Bad Request errors by capturing the full error response.
    """
    error_details = {
        "model": model,
        "error_type": type(e).__name__,
        "error_message": str(e),
    }

    # Extract response details from APIStatusError (parent of BadRequestError)
    if isinstance(e, APIStatusError):
        error_details["status_code"] = e.status_code
        if hasattr(e, "response") and e.response:
            try:
                error_details["response_text"] = e.response.text[:500] if e.response.text else None
            except Exception:
                # Response text extraction may fail if response is malformed; ignore as this is diagnostic only
                pass
        if hasattr(e, "body") and e.body:
            error_details["error_body"] = str(e.body)[:500]

    # Log request parameters (excluding sensitive data like messages content)
    error_details["request_params"] = {
        k: v for k, v in kwargs.items()
        if k not in ("messages",) and v is not None
    }

    return error_details

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
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors (helps diagnose openrouter/auto issues)
        error_details = _extract_error_details(e, model, kwargs)
        logger.error(
            f"OpenRouter request failed with 400 Bad Request: model={model}, "
            f"status={error_details.get('status_code')}, "
            f"body={error_details.get('error_body', 'N/A')}"
        )
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions',
            extra_context=error_details
        )
        raise
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
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors
        error_details = _extract_error_details(e, model, kwargs)
        logger.error(
            f"OpenRouter streaming request failed with 400 Bad Request: model={model}, "
            f"status={error_details.get('status_code')}, "
            f"body={error_details.get('error_body', 'N/A')}"
        )
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (stream)',
            extra_context=error_details
        )
        raise
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
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors
        error_details = _extract_error_details(e, model, kwargs)
        logger.error(
            f"OpenRouter async streaming request failed with 400 Bad Request: model={model}, "
            f"status={error_details.get('status_code')}, "
            f"body={error_details.get('error_body', 'N/A')}"
        )
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (async stream)',
            extra_context=error_details
        )
        raise
    except Exception as e:
        logger.error(f"OpenRouter async streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (async stream)'
        )
        raise
