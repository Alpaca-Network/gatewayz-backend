from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)
# OpenAI Python SDK raises its own exception hierarchy which we need to
# translate into HTTP responses. Make these imports optional so the module
# still loads if the dependency is absent (e.g. in minimal test environments).
try:  # pragma: no cover - import guard
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        OpenAIError,
        PermissionDeniedError,
        RateLimitError,
    )
except ImportError:  # pragma: no cover - handled gracefully below
    APIConnectionError = APITimeoutError = APIStatusError = AuthenticationError = None
    BadRequestError = NotFoundError = OpenAIError = PermissionDeniedError = RateLimitError = None

# Cerebras SDK has its own exception hierarchy separate from OpenAI's.
# Import these to properly handle Cerebras-specific errors.
try:  # pragma: no cover - import guard
    from cerebras.cloud.sdk import APIConnectionError as CerebrasAPIConnectionError
    from cerebras.cloud.sdk import APIStatusError as CerebrasAPIStatusError
    from cerebras.cloud.sdk import AuthenticationError as CerebrasAuthenticationError
    from cerebras.cloud.sdk import BadRequestError as CerebrasBadRequestError
    from cerebras.cloud.sdk import NotFoundError as CerebrasNotFoundError
    from cerebras.cloud.sdk import PermissionDeniedError as CerebrasPermissionDeniedError
    from cerebras.cloud.sdk import RateLimitError as CerebrasRateLimitError
except ImportError:  # pragma: no cover - handled gracefully below
    CerebrasAPIConnectionError = CerebrasAPIStatusError = CerebrasAuthenticationError = None
    CerebrasBadRequestError = CerebrasNotFoundError = CerebrasPermissionDeniedError = None
    CerebrasRateLimitError = None

FALLBACK_PROVIDER_PRIORITY: tuple[str, ...] = (
    "onerouter",
    "openai",  # Native OpenAI - try first for openai/* models
    "anthropic",  # Native Anthropic - try first for anthropic/* models
    "google-vertex",
    "openrouter",  # Fallback for OpenAI/Anthropic models
    "cerebras",
    "huggingface",
    "featherless",
    "vercel-ai-gateway",
    "aihubmix",
    "anannas",
    "alibaba-cloud",
    "fireworks",
    "together",
)
FALLBACK_ELIGIBLE_PROVIDERS = set(FALLBACK_PROVIDER_PRIORITY)
# Include 402 (Payment Required) to allow failover when provider credits are exhausted
FAILOVER_STATUS_CODES = {401, 402, 403, 404, 502, 503, 504}
_OPENROUTER_SUFFIX_LOCKS = {"exacto", "free", "extended"}
_OPENROUTER_ONLY_PREFIX_LOCKS = ("openrouter/",)  # Models that can ONLY use OpenRouter
# Models that should try native provider first, then OpenRouter as fallback
_NATIVE_PROVIDER_PREFIXES = {
    "openai/": ("openai", "openrouter"),  # OpenAI models: try openai first, then openrouter
    "anthropic/": (
        "anthropic",
        "openrouter",
    ),  # Anthropic models: try anthropic first, then openrouter
}


def build_provider_failover_chain(initial_provider: str | None) -> list[str]:
    """Return the provider attempt order starting with the initial provider.

    Always includes all eligible providers in the failover chain.
    Provider availability checks happen at request time, not at chain building time.
    """
    provider = (initial_provider or "").lower()

    if provider not in FALLBACK_ELIGIBLE_PROVIDERS:
        return [provider] if provider else ["onerouter"]

    chain: list[str] = []
    if provider:
        chain.append(provider)

    for candidate in FALLBACK_PROVIDER_PRIORITY:
        if candidate not in chain:
            chain.append(candidate)

    # Always include onerouter as ultimate fallback if nothing else is available
    if not chain or (len(chain) == 1 and chain[0] == provider and provider != "onerouter"):
        if "onerouter" not in chain:
            chain.append("onerouter")

    return chain


def should_failover(http_exc: HTTPException) -> bool:
    """Return True if the raised HTTPException qualifies for a failover attempt."""
    return http_exc.status_code in FAILOVER_STATUS_CODES


def enforce_model_failover_rules(
    model_id: str | None,
    provider_chain: list[str],
    allow_payment_failover: bool = False,
) -> list[str]:
    """
    Restrict the provider chain when a model is provider-specific.

    For OpenAI and Anthropic models:
    - Try the native provider first (openai for openai/*, anthropic for anthropic/*)
    - Fall back to OpenRouter if the native provider fails

    For OpenRouter-specific models (openrouter/* prefix or :exacto/:free/:extended suffix):
    - Lock to OpenRouter only

    Args:
        model_id: The model identifier being requested
        provider_chain: List of providers to attempt
        allow_payment_failover: If True, allow failover even for provider-locked models
            when the primary provider returned a 402 (Payment Required) error.
            This ensures users can still be served via alternative providers when
            a provider's credits are exhausted.

    Returns:
        Filtered provider chain based on model restrictions
    """
    if not model_id:
        return provider_chain

    # Apply model aliases to normalize model IDs (e.g., "gpt-4" -> "openai/gpt-4")
    # This ensures bare OpenAI/Anthropic model names are correctly routed
    from src.services.model_transformations import apply_model_alias

    aliased_model_id = apply_model_alias(model_id)
    normalized = aliased_model_id.lower()

    # Check for native provider prefixes (openai/, anthropic/)
    # These should try native provider first, then OpenRouter as fallback
    # IMPORTANT: These restrictions apply EVEN with payment failover enabled,
    # because these models can ONLY work with their native provider or OpenRouter.
    # Routing them to other providers like Cerebras/HuggingFace will always fail.
    for prefix, allowed_providers in _NATIVE_PROVIDER_PREFIXES.items():
        if normalized.startswith(prefix):
            # Filter chain to only include the allowed providers for this model type
            filtered_chain = [p for p in provider_chain if p in allowed_providers]
            if filtered_chain:
                # Ensure native provider comes first if it's in the chain
                native_provider = allowed_providers[0]
                if native_provider in filtered_chain and filtered_chain[0] != native_provider:
                    filtered_chain.remove(native_provider)
                    filtered_chain.insert(0, native_provider)
                logger.info(
                    "Model '%s' routed to native provider chain: %s (allow_payment_failover=%s)",
                    model_id,
                    filtered_chain,
                    allow_payment_failover,
                )
                return filtered_chain
            # If no allowed providers in chain, return original chain
            return provider_chain

    # If payment failover is allowed (e.g., after a 402 error), allow broader provider chain
    # for models that are not vendor-specific (i.e., not openai/* or anthropic/*)
    # Note: OpenRouter-only models (:free, :exacto, :extended suffixes) still need to respect
    # their restrictions even with payment failover, as these models only exist on OpenRouter.
    if allow_payment_failover:
        # Check for OpenRouter-only suffixes first - these must still be restricted
        if ":" in normalized:
            suffix = normalized.split(":", 1)[1]
            if suffix in _OPENROUTER_SUFFIX_LOCKS:
                if "openrouter" in provider_chain:
                    logger.info(
                        "Model '%s' has OpenRouter-specific suffix ':%s'; "
                        "locking to OpenRouter (even with payment failover)",
                        model_id,
                        suffix,
                    )
                    return ["openrouter"]
        # Check for OpenRouter-only prefixes
        if normalized.startswith(_OPENROUTER_ONLY_PREFIX_LOCKS):
            if "openrouter" in provider_chain:
                logger.info(
                    "Model '%s' is restricted to OpenRouter only (even with payment failover)",
                    model_id,
                )
                return ["openrouter"]
        # For other models, allow broader failover
        logger.info(
            "Payment failover enabled for model '%s'; allowing alternative providers",
            model_id,
        )
        return provider_chain

    # Check for OpenRouter-only prefixes
    if normalized.startswith(_OPENROUTER_ONLY_PREFIX_LOCKS):
        if "openrouter" in provider_chain:
            logger.info(
                "Model '%s' is restricted to OpenRouter only",
                model_id,
            )
            return ["openrouter"]
        return provider_chain

    # Check for OpenRouter-specific suffixes (:exacto, :free, :extended)
    if ":" in normalized:
        suffix = normalized.split(":", 1)[1]
        if suffix in _OPENROUTER_SUFFIX_LOCKS:
            if "openrouter" in provider_chain:
                # EMERGENCY FAILOVER: If OpenRouter circuit breaker is open,
                # allow failover to paid alternatives for :free models
                # This prevents complete service failure when OpenRouter is down
                if suffix == "free":
                    try:
                        from src.services.circuit_breaker import CircuitState, get_circuit_breaker

                        breaker = get_circuit_breaker("openrouter")
                        breaker_state = breaker.get_state()

                        if breaker_state.get("state") == CircuitState.OPEN.value:
                            # Circuit breaker is open - try fallback providers
                            # Strip :free suffix and use the base model
                            base_model = model_id.rsplit(":", 1)[0]
                            logger.warning(
                                "⚠️ EMERGENCY FAILOVER: OpenRouter circuit breaker OPEN for model '%s'. "
                                "Attempting failover to paid alternatives using base model '%s'. "
                                "Paid rates may apply. Retry in %ss.",
                                model_id,
                                base_model,
                                breaker_state.get("seconds_until_retry", 60),
                            )
                            # Return full provider chain to allow failover
                            # The base model (without :free) should work on other providers
                            return provider_chain
                    except Exception as e:
                        # If circuit breaker check fails, fall through to normal logic
                        logger.warning(f"Failed to check circuit breaker for {model_id}: {e}")

                logger.info(
                    "Model '%s' has OpenRouter-specific suffix ':%s'; locking to OpenRouter",
                    model_id,
                    suffix,
                )
                return ["openrouter"]

    return provider_chain


def get_fallback_model_id(model_id: str, provider: str) -> str:
    """
    Transform model ID for fallback provider when circuit breaker triggers failover.

    For :free models when OpenRouter circuit breaker is open, strip the :free suffix
    to allow the model to work on alternative (paid) providers.

    Args:
        model_id: Original model ID (e.g., "xiaomi/mimo-v2-flash:free")
        provider: The fallback provider being used

    Returns:
        Transformed model ID suitable for the fallback provider
    """
    # Only transform :free models when failing over from OpenRouter
    if ":free" in model_id.lower() and provider != "openrouter":
        base_model = model_id.rsplit(":", 1)[0]
        logger.info(
            f"🔄 Model transformation for failover: '{model_id}' → '{base_model}' (provider: {provider})"
        )
        return base_model

    return model_id


def filter_by_circuit_breaker(
    model_id: str | None, provider_chain: list[str], allow_emergency_fallback: bool = True
) -> list[str]:
    """
    Filter provider chain based on circuit breaker state.

    Removes providers with OPEN circuit breakers, but keeps at least one provider
    if allow_emergency_fallback is True to prevent complete failure.

    Args:
        model_id: The model being requested
        provider_chain: Original provider chain
        allow_emergency_fallback: If True, keep least-failed provider even if circuit is open

    Returns:
        Filtered provider chain with circuit breakers considered
    """
    if not model_id or not provider_chain:
        return provider_chain

    try:
        from src.services.model_availability import availability_service

        # Filter providers based on circuit breaker state
        available_providers = []
        unavailable_providers = []

        for provider in provider_chain:
            is_available = availability_service.is_model_available(model_id, provider)

            if is_available:
                available_providers.append(provider)
            else:
                unavailable_providers.append(provider)

                # Log why provider was filtered out
                availability = availability_service.get_model_availability(model_id, provider)
                if availability:
                    logger.info(
                        f"Provider '{provider}' filtered from chain for model '{model_id}': "
                        f"circuit_breaker={availability.circuit_breaker_state}, "
                        f"status={availability.status}"
                    )

        # If all providers filtered out and emergency fallback allowed, use least-failed
        if not available_providers and allow_emergency_fallback and unavailable_providers:
            # Use the first provider from original chain as emergency fallback
            emergency_provider = unavailable_providers[0]
            logger.warning(
                f"All providers have open circuits for model '{model_id}'. "
                f"Using emergency fallback: {emergency_provider}"
            )
            return [emergency_provider]

        # Log filtering results
        if unavailable_providers:
            logger.info(
                f"Circuit breaker filtering for '{model_id}': "
                f"available={available_providers}, filtered_out={unavailable_providers}"
            )

        return available_providers if available_providers else provider_chain

    except Exception as e:
        # Never let circuit breaker checking break routing
        logger.warning(f"Circuit breaker filter error for '{model_id}': {e}")
        return provider_chain


def map_provider_error(
    provider: str,
    model: str,
    exc: Exception,
) -> HTTPException:
    """
    Map upstream exceptions to HTTPException responses.
    Keeps existing status/detail semantics while allowing centralized handling.
    """
    # Log all upstream errors for debugging
    logger.warning(
        f"Provider error: provider={provider}, model={model}, "
        f"error_type={type(exc).__name__}, error={str(exc)[:200]}"
    )

    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, ValueError):
        error_msg = str(exc)
        # Check if this is a credential/authentication error that should trigger failover
        credential_keywords = [
            "access token",
            "credential",
            "authentication",
            "api key",
            "not configured",
            "id_token",
            "service account",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_VERTEX_CREDENTIALS_JSON",
        ]
        if any(keyword.lower() in error_msg.lower() for keyword in credential_keywords):
            # Map credential errors to 503 to trigger failover to alternative providers
            logger.info(
                f"Detected credential error for provider '{provider}': {error_msg[:200]}. "
                "This will trigger failover to alternative providers."
            )
            return HTTPException(
                status_code=503,
                detail=f"{provider} credentials not configured or invalid. Trying alternative providers.",
            )

        # Check if this is a "no candidates" error from Vertex AI or Gemini
        # This can happen due to safety filters, model overload, or transient issues
        # Map to 503 to trigger failover to alternative providers
        no_candidates_keywords = [
            "no candidates",
            "returned no candidates",
            "empty candidates",
            "promptfeedback",
            "block reason",
        ]
        if any(keyword.lower() in error_msg.lower() for keyword in no_candidates_keywords):
            logger.info(
                f"Detected 'no candidates' error for provider '{provider}': {error_msg[:300]}. "
                "This will trigger failover to alternative providers."
            )
            return HTTPException(
                status_code=503,
                detail=f"{provider} returned no response candidates. Trying alternative providers.",
            )

        # Other ValueErrors are treated as bad requests
        return HTTPException(status_code=400, detail=str(exc))

    # OpenAI SDK exceptions (used for OpenRouter and other compatible providers)
    # Check APITimeoutError before APIConnectionError as it may be a subclass
    if APITimeoutError and isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="Upstream timeout")

    if APIConnectionError and isinstance(exc, APIConnectionError):
        return HTTPException(status_code=503, detail="Upstream service unavailable")

    if APIStatusError and isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 500
        detail = "Upstream error"
        headers: dict[str, str] | None = None

        if RateLimitError and isinstance(exc, RateLimitError):
            retry_after = None
            if getattr(exc, "response", None):
                retry_after = exc.response.headers.get("retry-after")
            if retry_after is None and isinstance(getattr(exc, "body", None), dict):
                retry_after = exc.body.get("retry_after")
            if retry_after:
                headers = {"Retry-After": str(retry_after)}
            return HTTPException(
                status_code=429, detail="Upstream rate limit exceeded", headers=headers
            )

        auth_error_classes = tuple(
            err for err in (AuthenticationError, PermissionDeniedError) if err is not None
        )
        if auth_error_classes and isinstance(exc, auth_error_classes):
            detail = f"{provider} authentication error"
            # Always map auth errors to 401 for consistency
            status = 401
        elif NotFoundError and isinstance(exc, NotFoundError):
            detail = f"Model {model} not found or unavailable on {provider}"
            status = 404
        elif BadRequestError and isinstance(exc, BadRequestError):
            # Extract actual error message from BadRequestError
            error_msg = getattr(exc, "message", None) or str(exc)
            try:
                # Try to get response body if available
                if hasattr(exc, "response") and exc.response:
                    response_text = getattr(exc.response, "text", None)
                    if response_text:
                        error_msg = f"{error_msg} | Response: {response_text[:200]}"
            except Exception as log_exc:
                logger.debug("Failed to extract OpenAI BadRequestError response body: %r", log_exc)
            detail = f"Provider '{provider}' rejected request for model '{model}': {error_msg}"
            status = 400
        elif status == 403:
            detail = f"{provider} authentication error"
            status = 401  # Map 403 to 401 for consistency with auth error handling
        elif status == 404:
            detail = f"Model {model} not found or unavailable on {provider}"
        elif 500 <= status < 600:
            detail = "Upstream service error"

        # Fall back to message body if we still have the generic detail
        if detail == "Upstream error":
            error_msg = getattr(exc, "message", None) or str(exc)
            # Include provider and model in error message for better error tracking
            detail = f"Provider '{provider}' error for model '{model}': {error_msg}"

        return HTTPException(status_code=status, detail=detail, headers=headers)

    if OpenAIError and isinstance(exc, OpenAIError):
        return HTTPException(status_code=502, detail=str(exc))

    # Cerebras SDK exceptions (similar structure to OpenAI SDK but separate classes)
    if CerebrasAPIConnectionError and isinstance(exc, CerebrasAPIConnectionError):
        return HTTPException(status_code=503, detail="Upstream service unavailable")

    if CerebrasAPIStatusError and isinstance(exc, CerebrasAPIStatusError):
        status = getattr(exc, "status_code", None)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 500
        detail = "Upstream error"
        headers: dict[str, str] | None = None

        if CerebrasRateLimitError and isinstance(exc, CerebrasRateLimitError):
            retry_after = None
            if getattr(exc, "response", None):
                retry_after = exc.response.headers.get("retry-after")
            if retry_after is None and isinstance(getattr(exc, "body", None), dict):
                retry_after = exc.body.get("retry_after")
            if retry_after:
                headers = {"Retry-After": str(retry_after)}
            return HTTPException(
                status_code=429, detail="Upstream rate limit exceeded", headers=headers
            )

        cerebras_auth_error_classes = tuple(
            err
            for err in (CerebrasAuthenticationError, CerebrasPermissionDeniedError)
            if err is not None
        )
        if cerebras_auth_error_classes and isinstance(exc, cerebras_auth_error_classes):
            detail = f"{provider} authentication error"
            # Always map auth errors to 401 for consistency
            status = 401
        elif CerebrasNotFoundError and isinstance(exc, CerebrasNotFoundError):
            detail = f"Model {model} not found or unavailable on {provider}"
            status = 404
        elif CerebrasBadRequestError and isinstance(exc, CerebrasBadRequestError):
            # Extract actual error message from BadRequestError
            error_msg = getattr(exc, "message", None) or str(exc)
            try:
                # Try to get response body if available
                if hasattr(exc, "response") and exc.response:
                    response_text = getattr(exc.response, "text", None)
                    if response_text:
                        error_msg = f"{error_msg} | Response: {response_text[:200]}"
            except Exception as log_exc:
                logger.debug(
                    "Failed to extract Cerebras BadRequestError response body: %r", log_exc
                )
            detail = f"Provider '{provider}' rejected request for model '{model}': {error_msg}"
            status = 400
        elif status == 403:
            detail = f"{provider} authentication error"
            status = 401  # Map 403 to 401 for consistency with auth error handling
        elif status == 404:
            detail = f"Model {model} not found or unavailable on {provider}"
        elif 500 <= status < 600:
            detail = "Upstream service error"

        # Fall back to message body if we still have the generic detail
        if detail == "Upstream error":
            error_msg = getattr(exc, "message", None) or str(exc)
            # Include provider and model in error message for better error tracking
            detail = f"Provider '{provider}' error for model '{model}': {error_msg}"

        return HTTPException(status_code=status, detail=detail, headers=headers)

    if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
        return HTTPException(status_code=504, detail="Upstream timeout")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = exc.response.headers.get("retry-after")

        if status == 429:
            headers = {"Retry-After": retry_after} if retry_after else None
            return HTTPException(
                status_code=429, detail="Upstream rate limit exceeded", headers=headers
            )
        if status in (401, 403):
            return HTTPException(status_code=500, detail=f"{provider} authentication error")
        if status == 404:
            return HTTPException(
                status_code=404,
                detail=f"Model {model} not found or unavailable on {provider}",
            )
        if 400 <= status < 500:
            # Extract error details from response
            error_detail = (
                f"Provider '{provider}' rejected request for model '{model}' (HTTP {status})"
            )
            try:
                response_body = exc.response.text[:500] if exc.response.text else "No response body"
                error_detail += f" | Response: {response_body}"
            except Exception as log_exc:
                logger.debug("Failed to extract httpx response body: %r", log_exc)
            return HTTPException(status_code=400, detail=error_detail)
        return HTTPException(
            status_code=502, detail=f"Provider '{provider}' service error for model '{model}'"
        )

    if isinstance(exc, httpx.RequestError):
        return HTTPException(
            status_code=503, detail=f"Provider '{provider}' service unavailable for model '{model}'"
        )

    # Final fallback - include provider and model for better error tracking
    return HTTPException(
        status_code=502, detail=f"Provider '{provider}' error for model '{model}': {str(exc)}"
    )


def map_provider_error_detailed(
    provider: str,
    model: str,
    exc: Exception,
    request_id: str | None = None,
) -> HTTPException:
    """
    Map provider errors to detailed error responses with suggestions and context.

    This function wraps map_provider_error and enhances the response with
    detailed error information including suggestions, context, and documentation links.

    Args:
        provider: Provider name
        model: Model ID
        exc: Exception raised by provider
        request_id: Optional request ID for tracking

    Returns:
        HTTPException with detailed error response

    Usage:
        try:
            response = provider_client.make_request(...)
        except Exception as e:
            raise map_provider_error_detailed(provider, model, e, request_id)
    """
    from src.utils.error_factory import DetailedErrorFactory

    # Get the basic HTTP exception from the existing mapper
    http_exc = map_provider_error(provider, model, exc)

    # Enhance with detailed error response based on status code
    status_code = http_exc.status_code
    provider_message = str(exc)

    # Determine the appropriate detailed error type
    if status_code == 404:
        # Model not found - add suggestions
        error_response = DetailedErrorFactory.model_not_found(
            model_id=model,
            provider=provider,
            suggested_models=None,  # Could fetch similar models here
            request_id=request_id,
        )
    elif status_code == 429:
        # Rate limit exceeded
        retry_after = None
        if http_exc.headers and "Retry-After" in http_exc.headers:
            try:
                retry_after = int(http_exc.headers["Retry-After"])
            except ValueError:
                pass

        error_response = DetailedErrorFactory.rate_limit_exceeded(
            limit_type="provider_rate_limit",
            retry_after=retry_after,
            request_id=request_id,
        )
    elif status_code == 401 or status_code == 403:
        # Authentication/authorization error
        error_response = DetailedErrorFactory.provider_error(
            provider=provider,
            model=model,
            provider_message=provider_message,
            status_code=401,
            request_id=request_id,
        )
    elif status_code == 504:
        # Timeout
        error_response = DetailedErrorFactory.provider_timeout(
            provider=provider,
            model=model,
            request_id=request_id,
        )
    elif status_code in (502, 503):
        # Provider unavailable
        error_response = DetailedErrorFactory.provider_error(
            provider=provider,
            model=model,
            provider_message=provider_message,
            status_code=status_code,
            request_id=request_id,
        )
    else:
        # Generic provider error
        error_response = DetailedErrorFactory.provider_error(
            provider=provider,
            model=model,
            provider_message=provider_message,
            status_code=status_code,
            request_id=request_id,
        )

    # Return HTTPException with detailed error response
    return HTTPException(
        status_code=error_response.error.status,
        detail=error_response.dict(exclude_none=True),
        headers=http_exc.headers,
    )
