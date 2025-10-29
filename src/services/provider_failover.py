from __future__ import annotations

import asyncio
from typing import List, Optional

import httpx
from fastapi import HTTPException

# OpenAI Python SDK raises its own exception hierarchy which we need to
# translate into HTTP responses. Make these imports optional so the module
# still loads if the dependency is absent (e.g. in minimal test environments).
try:  # pragma: no cover - import guard
    import openai as _openai_module
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

    # Compatibility shims for OpenAI SDK >=1.0 which expects httpx request/response objects.
    try:
        APIConnectionError("test")
    except TypeError:
        class _CompatAPIConnectionError(APIConnectionError):  # type: ignore[misc]
            def __init__(self, message: str = "Connection error.", request=None, **kwargs):
                if request is None:
                    request = httpx.Request("GET", "https://api.openai.com")
                super().__init__(message=message, request=request)
                self.message = message
        APIConnectionError = _CompatAPIConnectionError
        _openai_module.APIConnectionError = APIConnectionError

    try:
        APITimeoutError("timeout")
    except TypeError:
        class _CompatAPITimeoutError(APITimeoutError):  # type: ignore[misc]
            def __init__(self, message: str = "Request timed out", request=None, **kwargs):
                if request is None:
                    request = httpx.Request("GET", "https://api.openai.com")
                super().__init__(request=request)
                self.message = message
        APITimeoutError = _CompatAPITimeoutError
        _openai_module.APITimeoutError = APITimeoutError

    try:
        RateLimitError("Rate limited", response=None, body=None)
    except Exception:
        class _CompatRateLimitError(RateLimitError):  # type: ignore[misc]
            def __init__(self, message: str = "Rate limited", response=None, body=None, **kwargs):
                if response is None:
                    request = httpx.Request("GET", "https://api.openai.com")
                    response = httpx.Response(429, headers={}, request=request)
                else:
                    request = getattr(response, "request", None)
                    if request is None:
                        request = httpx.Request("GET", "https://api.openai.com")
                        status_code = getattr(response, "status_code", 429)
                        headers = dict(getattr(response, "headers", {}))
                        content = getattr(response, "content", b"")
                        response = httpx.Response(status_code, headers=headers, request=request, content=content)
                super().__init__(message, response=response, body=body or {})
                self.response = response
                self.body = body or {}
        RateLimitError = _CompatRateLimitError
        _openai_module.RateLimitError = RateLimitError
except ImportError:  # pragma: no cover - handled gracefully below
    APIConnectionError = APITimeoutError = APIStatusError = AuthenticationError = None
    BadRequestError = NotFoundError = OpenAIError = PermissionDeniedError = RateLimitError = None

FALLBACK_PROVIDER_PRIORITY: tuple[str, ...] = (
    "huggingface",
    "featherless",
    "fireworks",
    "together",
    "openrouter",
)
FALLBACK_ELIGIBLE_PROVIDERS = set(FALLBACK_PROVIDER_PRIORITY)
FAILOVER_STATUS_CODES = {401, 403, 404, 429, 502, 503, 504}


def build_provider_failover_chain(initial_provider: str | None) -> List[str]:
    """Return the provider attempt order starting with the initial provider."""
    provider = (initial_provider or "").lower()

    if provider not in FALLBACK_ELIGIBLE_PROVIDERS:
        return [provider] if provider else ["openrouter"]

    chain: List[str] = []
    if provider:
        chain.append(provider)

    for candidate in FALLBACK_PROVIDER_PRIORITY:
        if candidate not in chain:
            chain.append(candidate)

    return chain


def should_failover(http_exc: HTTPException) -> bool:
    """Return True if the raised HTTPException qualifies for a failover attempt."""
    return http_exc.status_code in FAILOVER_STATUS_CODES


def map_provider_error(
    provider: str,
    model: str,
    exc: Exception,
) -> HTTPException:
    """
    Map upstream exceptions to HTTPException responses.
    Keeps existing status/detail semantics while allowing centralized handling.
    """
    if isinstance(exc, HTTPException):
        return exc

    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))

    # OpenAI SDK exceptions (used for OpenRouter and other compatible providers)
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
        headers: Optional[dict[str, str]] = None

        if RateLimitError and isinstance(exc, RateLimitError):
            retry_after = None
            if getattr(exc, "response", None):
                retry_after = exc.response.headers.get("retry-after")
            if retry_after is None and isinstance(getattr(exc, "body", None), dict):
                retry_after = exc.body.get("retry_after")
            if retry_after:
                headers = {"Retry-After": str(retry_after)}
            return HTTPException(status_code=429, detail="Upstream rate limit exceeded", headers=headers)

        auth_error_classes = tuple(
            err for err in (AuthenticationError, PermissionDeniedError) if err is not None
        )
        if auth_error_classes and isinstance(exc, auth_error_classes):
            detail = f"{provider} authentication error"
            status = 401
        elif NotFoundError and isinstance(exc, NotFoundError):
            detail = f"Model {model} not found or unavailable on {provider}"
            status = 404
        elif BadRequestError and isinstance(exc, BadRequestError):
            detail = "Upstream rejected the request"
            status = 400
        elif status == 403:
            detail = f"{provider} authentication error"
        elif status == 404:
            detail = f"Model {model} not found or unavailable on {provider}"
        elif 500 <= status < 600:
            detail = "Upstream service error"

        # Fall back to message body if we still have the generic detail
        if detail == "Upstream error":
            detail = getattr(exc, "message", None) or str(exc)

        return HTTPException(status_code=status, detail=detail, headers=headers)

    if OpenAIError and isinstance(exc, OpenAIError):
        return HTTPException(status_code=502, detail=str(exc))

    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return HTTPException(status_code=504, detail="Upstream timeout")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = exc.response.headers.get("retry-after")

        if status == 429:
            headers = {"Retry-After": retry_after} if retry_after else None
            return HTTPException(status_code=429, detail="Upstream rate limit exceeded", headers=headers)
        if status in (401, 403):
            return HTTPException(status_code=500, detail=f"{provider} authentication error")
        if status == 404:
            return HTTPException(
                status_code=404,
                detail=f"Model {model} not found or unavailable on {provider}",
            )
        if 400 <= status < 500:
            return HTTPException(status_code=400, detail="Upstream rejected the request")
        return HTTPException(status_code=502, detail="Upstream service error")

    if isinstance(exc, httpx.RequestError):
        return HTTPException(status_code=503, detail="Upstream service unavailable")

    return HTTPException(status_code=502, detail="Upstream error")
