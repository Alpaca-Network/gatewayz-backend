import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from openai import APIStatusError, AsyncOpenAI, BadRequestError

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_openrouter_pooled_client, get_pooled_async_client
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)


def _normalize_message_roles(messages: list[dict[str, Any] | Any]) -> list[dict[str, Any] | Any]:
    """Normalize message roles for OpenRouter compatibility.

    The 'developer' role is an OpenAI API feature for system-level instructions
    that many providers (including OpenRouter's underlying models) don't support.
    This function transforms 'developer' role to 'system' role for compatibility.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys

    Returns:
        List of messages with normalized roles (shallow copies to avoid mutation)
    """
    normalized = []
    for msg in messages:
        if isinstance(msg, dict):
            if msg.get("role") == "developer":
                # Transform developer role to system role for compatibility
                normalized_msg = {**msg, "role": "system"}
                normalized.append(normalized_msg)
            else:
                # Create a shallow copy to avoid mutation issues
                normalized.append({**msg})
        else:
            normalized.append(msg)
    return normalized


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


# OpenRouter provider settings to override account-level data policy restrictions
# This allows access to all model endpoints regardless of their data training policies
OPENROUTER_PROVIDER_SETTINGS = {
    "data_collection": "allow",  # Allow endpoints that may use data for training
}


def _merge_extra_body(kwargs: dict) -> dict:
    """Merge OpenRouter provider settings into extra_body parameter.

    This ensures all OpenRouter requests include the data_collection: allow setting
    to override account-level data policy restrictions that could block certain models.
    """
    existing_extra_body = kwargs.pop("extra_body", None) or {}

    # Merge provider settings with any existing provider config
    existing_provider = existing_extra_body.get("provider", {})
    merged_provider = {**OPENROUTER_PROVIDER_SETTINGS, **existing_provider}

    merged_extra_body = {
        **existing_extra_body,
        "provider": merged_provider,
    }

    return {**kwargs, "extra_body": merged_extra_body}


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
        # Normalize message roles (e.g., developer -> system) for compatibility
        normalized_messages = _normalize_message_roles(messages)
        # Merge provider settings to allow access to all model endpoints
        merged_kwargs = _merge_extra_body(kwargs)
        response = client.chat.completions.create(model=model, messages=normalized_messages, **merged_kwargs)
        return response
    except APIStatusError as e:
        # Handle 402 Payment Required errors specifically (credit exhaustion)
        if e.status_code == 402:
            error_details = _extract_error_details(e, model, kwargs)
            logger.error(
                f"OpenRouter credit exhaustion for model '{model}': {str(e)}. "
                "Provider failover will be attempted automatically."
            )

            # Check credit balance and alert if necessary
            try:
                import asyncio
                from src.services.provider_credit_monitor import check_openrouter_credits, send_low_credit_alert

                # Run async credit check in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    credit_info = loop.run_until_complete(check_openrouter_credits())
                    if credit_info.get("status") in ("critical", "warning"):
                        loop.run_until_complete(
                            send_low_credit_alert(
                                "openrouter",
                                credit_info.get("balance", 0),
                                credit_info["status"]
                            )
                        )
                finally:
                    loop.close()
            except Exception as credit_check_err:
                logger.warning(f"Failed to check OpenRouter credits after 402 error: {credit_check_err}")

            capture_provider_error(
                e,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions',
                extra_context={**error_details, "error_type": "insufficient_credits"}
            )
            raise
        # Fall through to BadRequestError handling
        if isinstance(e, BadRequestError):
            pass  # Handle in next except block
        else:
            # Other APIStatusErrors
            logger.error(f"OpenRouter request failed: {e}")
            capture_provider_error(e, provider='openrouter', model=model, endpoint='/chat/completions')
            raise
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors (helps diagnose openrouter/auto issues)
        error_details = _extract_error_details(e, model, kwargs)
        error_message = str(e)

        # Check for invalid model ID error
        if "is not a valid model ID" in error_message:
            logger.warning(
                f"User requested invalid OpenRouter model: model={model}, "
                f"error={error_message}"
            )
            # Provide user-friendly error message
            user_friendly_error = BadRequestError(
                message=(
                    f"The model '{model}' is not available on OpenRouter. "
                    "Please check https://api.gatewayz.ai/v1/models for a list of available models, "
                    "or verify the model ID at https://openrouter.ai/models"
                ),
                response=e.response,
                body=e.body
            )
            capture_provider_error(
                user_friendly_error,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions',
                extra_context={**error_details, "error_type": "invalid_model_id"}
            )
            raise user_friendly_error from e

        # Log other 400 errors with full details
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
        # Normalize message roles (e.g., developer -> system) for compatibility
        normalized_messages = _normalize_message_roles(messages)
        # Merge provider settings to allow access to all model endpoints
        merged_kwargs = _merge_extra_body(kwargs)
        stream = client.chat.completions.create(
            model=model, messages=normalized_messages, stream=True, **merged_kwargs
        )
        return stream
    except APIStatusError as e:
        # Handle 402 Payment Required errors specifically (credit exhaustion)
        if e.status_code == 402:
            error_details = _extract_error_details(e, model, kwargs)
            logger.error(
                f"OpenRouter credit exhaustion (streaming) for model '{model}': {str(e)}. "
                "Provider failover will be attempted automatically."
            )

            # Check credit balance and alert if necessary
            try:
                import asyncio
                from src.services.provider_credit_monitor import check_openrouter_credits, send_low_credit_alert

                # Run async credit check in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    credit_info = loop.run_until_complete(check_openrouter_credits())
                    if credit_info.get("status") in ("critical", "warning"):
                        loop.run_until_complete(
                            send_low_credit_alert(
                                "openrouter",
                                credit_info.get("balance", 0),
                                credit_info["status"]
                            )
                        )
                finally:
                    loop.close()
            except Exception as credit_check_err:
                logger.warning(f"Failed to check OpenRouter credits after 402 error: {credit_check_err}")

            capture_provider_error(
                e,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions (stream)',
                extra_context={**error_details, "error_type": "insufficient_credits"}
            )
            raise
        # Fall through to BadRequestError handling
        if isinstance(e, BadRequestError):
            pass  # Handle in next except block
        else:
            # Other APIStatusErrors
            logger.error(f"OpenRouter streaming request failed: {e}")
            capture_provider_error(e, provider='openrouter', model=model, endpoint='/chat/completions (stream)')
            raise
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors
        error_details = _extract_error_details(e, model, kwargs)
        error_message = str(e)

        # Check for invalid model ID error (same handling as non-streaming)
        if "is not a valid model ID" in error_message:
            logger.warning(
                f"User requested invalid OpenRouter model (streaming): model={model}, "
                f"error={error_message}"
            )
            # Provide user-friendly error message
            user_friendly_error = BadRequestError(
                message=(
                    f"The model '{model}' is not available on OpenRouter. "
                    "Please check https://api.gatewayz.ai/v1/models for a list of available models, "
                    "or verify the model ID at https://openrouter.ai/models"
                ),
                response=e.response,
                body=e.body
            )
            capture_provider_error(
                user_friendly_error,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions (stream)',
                extra_context={**error_details, "error_type": "invalid_model_id"}
            )
            raise user_friendly_error from e

        # Log other 400 errors with full details
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
                "X-Title": Config.OPENROUTER_SITE_NAME,
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
        # Normalize message roles (e.g., developer -> system) for compatibility
        normalized_messages = _normalize_message_roles(messages)
        # Merge provider settings to allow access to all model endpoints
        merged_kwargs = _merge_extra_body(kwargs)
        stream = await client.chat.completions.create(
            model=model, messages=normalized_messages, stream=True, **merged_kwargs
        )
        return stream
    except APIStatusError as e:
        # Handle 402 Payment Required errors specifically (credit exhaustion)
        if e.status_code == 402:
            error_details = _extract_error_details(e, model, kwargs)
            logger.error(
                f"OpenRouter credit exhaustion (async streaming) for model '{model}': {str(e)}. "
                "Provider failover will be attempted automatically."
            )

            # Check credit balance and alert if necessary
            try:
                from src.services.provider_credit_monitor import check_openrouter_credits, send_low_credit_alert

                credit_info = await check_openrouter_credits()
                if credit_info.get("status") in ("critical", "warning"):
                    await send_low_credit_alert(
                        "openrouter",
                        credit_info.get("balance", 0),
                        credit_info["status"]
                    )
            except Exception as credit_check_err:
                logger.warning(f"Failed to check OpenRouter credits after 402 error: {credit_check_err}")

            capture_provider_error(
                e,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions (async stream)',
                extra_context={**error_details, "error_type": "insufficient_credits"}
            )
            raise
        # Fall through to BadRequestError handling
        if isinstance(e, BadRequestError):
            pass  # Handle in next except block
        else:
            # Other APIStatusErrors
            logger.error(f"OpenRouter async streaming request failed: {e}")
            capture_provider_error(e, provider='openrouter', model=model, endpoint='/chat/completions (async stream)')
            raise
    except BadRequestError as e:
        # Log detailed error info for 400 Bad Request errors
        error_details = _extract_error_details(e, model, kwargs)
        error_message = str(e)

        # Check for invalid model ID error (same handling as non-streaming)
        if "is not a valid model ID" in error_message:
            logger.warning(
                f"User requested invalid OpenRouter model (async streaming): model={model}, "
                f"error={error_message}"
            )
            # Provide user-friendly error message
            user_friendly_error = BadRequestError(
                message=(
                    f"The model '{model}' is not available on OpenRouter. "
                    "Please check https://api.gatewayz.ai/v1/models for a list of available models, "
                    "or verify the model ID at https://openrouter.ai/models"
                ),
                response=e.response,
                body=e.body
            )
            capture_provider_error(
                user_friendly_error,
                provider='openrouter',
                model=model,
                endpoint='/chat/completions (async stream)',
                extra_context={**error_details, "error_type": "invalid_model_id"}
            )
            raise user_friendly_error from e

        # Log other 400 errors with full details
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
