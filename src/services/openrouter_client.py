import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter
from openai import APIStatusError, AsyncOpenAI, BadRequestError

from src.cache import _models_cache, clear_gateway_error, set_gateway_error
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.circuit_breaker import CircuitBreakerConfig, CircuitBreakerError, get_circuit_breaker
from src.services.connection_pool import get_openrouter_pooled_client, get_pooled_async_client
from src.utils.security_validators import sanitize_for_logging
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

# Circuit breaker configuration for OpenRouter
# OpenRouter is our primary provider, so we use conservative thresholds
OPENROUTER_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,  # Open after 5 consecutive failures
    success_threshold=2,  # Close after 2 consecutive successes in HALF_OPEN
    timeout_seconds=60,  # Wait 60s before retrying after opening
    failure_window_seconds=60,  # Measure failure rate over 60s window
    failure_rate_threshold=0.5,  # Open if >50% failure rate
    min_requests_for_rate=10,  # Need at least 10 requests to calculate rate
)


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


def _make_openrouter_request_openai_internal(messages, model, **kwargs):
    """Internal function to make request to OpenRouter (called by circuit breaker)"""
    client = get_openrouter_client()
    # Normalize message roles (e.g., developer -> system) for compatibility
    normalized_messages = _normalize_message_roles(messages)
    # Merge provider settings to allow access to all model endpoints
    merged_kwargs = _merge_extra_body(kwargs)
    response = client.chat.completions.create(model=model, messages=normalized_messages, **merged_kwargs)
    return response


def make_openrouter_request_openai(messages, model, **kwargs):
    """Make request to OpenRouter using OpenAI client with circuit breaker protection"""
    circuit_breaker = get_circuit_breaker("openrouter", OPENROUTER_CIRCUIT_CONFIG)

    try:
        # Wrap the actual API call with circuit breaker
        response = circuit_breaker.call(
            _make_openrouter_request_openai_internal,
            messages,
            model,
            **kwargs
        )
        return response
    except CircuitBreakerError as e:
        # Circuit breaker is open, log and re-raise for failover
        logger.warning(f"OpenRouter circuit breaker OPEN: {e.message}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions',
            extra_context={"circuit_breaker_state": e.state.value}
        )
        raise
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


def _make_openrouter_request_openai_stream_internal(messages, model, **kwargs):
    """Internal function to make streaming request to OpenRouter (called by circuit breaker)"""
    client = get_openrouter_client()
    # Normalize message roles (e.g., developer -> system) for compatibility
    normalized_messages = _normalize_message_roles(messages)
    # Merge provider settings to allow access to all model endpoints
    merged_kwargs = _merge_extra_body(kwargs)
    stream = client.chat.completions.create(
        model=model, messages=normalized_messages, stream=True, **merged_kwargs
    )
    return stream


def make_openrouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to OpenRouter using OpenAI client with circuit breaker protection"""
    circuit_breaker = get_circuit_breaker("openrouter", OPENROUTER_CIRCUIT_CONFIG)

    try:
        # Wrap the actual API call with circuit breaker
        stream = circuit_breaker.call(
            _make_openrouter_request_openai_stream_internal,
            messages,
            model,
            **kwargs
        )
        return stream
    except CircuitBreakerError as e:
        # Circuit breaker is open, log and re-raise for failover
        logger.warning(f"OpenRouter circuit breaker OPEN (streaming): {e.message}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions (stream)',
            extra_context={"circuit_breaker_state": e.state.value}
        )
        raise
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


# ============================================================================
# Model Catalog Functions
# ============================================================================


def sanitize_pricing(pricing: dict) -> dict | None:
    """
    Sanitize pricing data by handling negative values.

    OpenRouter uses -1 to indicate dynamic pricing (e.g., for auto-routing models).
    Since we can't determine the actual cost for dynamic pricing models, we return
    None to indicate this model should be filtered out.

    Args:
        pricing: Pricing dictionary from API

    Returns:
        Sanitized pricing dictionary, or None if pricing is dynamic/indeterminate
    """
    if not pricing or not isinstance(pricing, dict):
        return pricing

    sanitized = pricing.copy()
    has_dynamic_pricing = False

    for key in ["prompt", "completion", "request", "image", "web_search", "internal_reasoning"]:
        if key in sanitized:
            try:
                value = sanitized[key]
                if value is not None:
                    # Convert to float and check if negative
                    float_value = float(value)
                    if float_value < 0:
                        # Mark as dynamic pricing - we can't determine actual cost
                        has_dynamic_pricing = True
                        logger.debug(
                            "Found dynamic pricing %s=%s, model will be filtered",
                            sanitize_for_logging(key),
                            sanitize_for_logging(str(value)),
                        )
                        break
            except (ValueError, TypeError):
                # Keep the original value if conversion fails
                pass

    # If model has dynamic pricing, return None to filter it out
    if has_dynamic_pricing:
        return None

    return sanitized


def fetch_models_from_openrouter():
    """Fetch models from OpenRouter API with step-by-step logging"""
    from src.utils.step_logger import StepLogger

    step_logger = StepLogger("OpenRouter Model Fetch", total_steps=4)
    step_logger.start(provider="openrouter", endpoint="https://openrouter.ai/api/v1/models")

    try:
        # Step 1: Validate API key
        step_logger.step(1, "Validating API key", provider="openrouter")
        if not Config.OPENROUTER_API_KEY:
            step_logger.failure(Exception("API key not configured"))
            logger.error("OpenRouter API key not configured")
            return None
        step_logger.success(status="configured")

        # Step 2: Fetch models from API
        step_logger.step(2, "Fetching models from API", provider="openrouter")
        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=30.0)
        response.raise_for_status()

        try:
            models_data = response.json()
        except json.JSONDecodeError as json_err:
            step_logger.failure(json_err, status_code=response.status_code)
            logger.error(
                f"Failed to parse JSON response from OpenRouter models API: {json_err}. "
                f"Response status: {response.status_code}, Content-Type: {response.headers.get('content-type')}"
            )
            return []

        raw_models = models_data.get("data", [])
        step_logger.success(raw_count=len(raw_models), status_code=response.status_code)

        # Step 3: Process and filter models
        step_logger.step(3, "Processing and filtering models", provider="openrouter")
        filtered_models = []
        for model in raw_models:
            model.setdefault("source_gateway", "openrouter")
            # Sanitize pricing - returns None for models with dynamic pricing
            if "pricing" in model:
                sanitized_pricing = sanitize_pricing(model["pricing"])
                if sanitized_pricing is None:
                    # Filter out models with dynamic/indeterminate pricing
                    logger.debug(
                        "Filtering out model %s with dynamic pricing",
                        sanitize_for_logging(model.get("id", "unknown")),
                    )
                    continue
                model["pricing"] = sanitized_pricing

            # Mark OpenRouter free models (those with :free suffix)
            # Only OpenRouter has legitimately free models
            # Use `or ""` to handle both missing keys and null values
            provider_model_id = model.get("id") or ""
            model["is_free"] = provider_model_id.endswith(":free")

            filtered_models.append(model)

        filtered_count = len(raw_models) - len(filtered_models)
        step_logger.success(
            final_count=len(filtered_models),
            filtered_out=filtered_count,
            filter_rate=f"{(filtered_count/len(raw_models)*100):.1f}%"
        )

        # Step 4: Cache the results
        step_logger.step(4, "Caching models", provider="openrouter")
        _models_cache["data"] = filtered_models
        _models_cache["timestamp"] = datetime.now(timezone.utc)
        clear_gateway_error("openrouter")
        step_logger.success(cached_count=len(filtered_models), cache_status="updated")

        step_logger.complete(total_models=len(filtered_models), provider="openrouter")
        return _models_cache["data"]
    except httpx.TimeoutException as e:
        error_msg = f"Request timeout after 30s: {sanitize_for_logging(str(e))}"
        logger.error("OpenRouter timeout error: %s", error_msg)
        set_gateway_error("openrouter", error_msg)
        return None
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("OpenRouter HTTP error: %s", error_msg)
        set_gateway_error("openrouter", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from OpenRouter: %s", error_msg)
        set_gateway_error("openrouter", error_msg)
        return None
