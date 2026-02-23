"""
Error Handler Utilities

Utilities for converting exceptions to detailed error responses.
Integrates with FastAPI's exception handling system.

Usage:
    from src.utils.error_handlers import detailed_http_exception_handler

    # In main.py
    app.add_exception_handler(HTTPException, detailed_http_exception_handler)
"""

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.schemas.errors import ErrorResponse
from src.utils.error_factory import DetailedErrorFactory

logger = logging.getLogger(__name__)


async def detailed_http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """
    Convert HTTPException to detailed error response.

    This handler checks if the exception already contains a detailed error
    (in the expected format), and if not, maps it to a detailed error.

    Args:
        request: FastAPI Request object
        exc: HTTPException to convert

    Returns:
        JSONResponse with detailed error

    Usage:
        app.add_exception_handler(HTTPException, detailed_http_exception_handler)
    """
    # Extract request_id from request state if available
    request_id = getattr(request.state, "request_id", None)

    # Check if detail is already a detailed error response
    if isinstance(exc.detail, dict):
        if "error" in exc.detail:
            # Already a detailed error - return as-is
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers=exc.headers or {},
            )

    # Convert simple HTTPException to detailed format
    error_response = _map_http_exception_to_detailed_error(
        exc, request_id=request_id, request=request
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.dict(exclude_none=True),
        headers=exc.headers or {},
    )


def _map_http_exception_to_detailed_error(
    exc: HTTPException,
    request_id: str | None = None,
    request: Request | None = None,
) -> ErrorResponse:
    """
    Map HTTPException to detailed error response.

    Analyzes the status code and detail message to determine the
    appropriate detailed error type.

    Args:
        exc: HTTPException to map
        request_id: Optional request ID
        request: Optional FastAPI Request object

    Returns:
        ErrorResponse with detailed error information
    """
    status_code = exc.status_code
    detail = str(exc.detail) if exc.detail else "An error occurred"

    # Extract endpoint info from request if available
    endpoint = None
    if request:
        endpoint = f"{request.method} {request.url.path}"

    # Map based on status code and detail content
    if status_code == 400:
        # Bad request - check detail for specific error type
        detail_lower = detail.lower()

        if "missing" in detail_lower and "required" in detail_lower:
            # Try to extract field name
            field_name = _extract_field_name(detail)
            return DetailedErrorFactory.missing_required_field(
                field_name=field_name or "unknown",
                endpoint=endpoint,
                request_id=request_id,
            )
        elif "empty" in detail_lower and "messages" in detail_lower:
            return DetailedErrorFactory.empty_messages_array(request_id=request_id)
        else:
            # Generic bad request - use invalid_parameter
            return DetailedErrorFactory.invalid_parameter(
                parameter_name="request",
                parameter_value=detail,
                request_id=request_id,
            )

    elif status_code == 401:
        # Unauthorized - invalid API key
        return DetailedErrorFactory.invalid_api_key(
            reason=detail if detail != "Invalid API key or unauthorized access" else None,
            request_id=request_id,
        )

    elif status_code == 402:
        # Payment required - insufficient credits
        # Try to extract credit amounts from detail
        current, required = _extract_credit_amounts(detail)
        if current is not None and required is not None:
            return DetailedErrorFactory.insufficient_credits(
                current_credits=current,
                required_credits=required,
                request_id=request_id,
            )
        else:
            # Generic payment error
            return DetailedErrorFactory.insufficient_credits(
                current_credits=0.0,
                required_credits=0.0,
                request_id=request_id,
            )

    elif status_code == 403:
        # Forbidden - check detail for specific error type
        detail_lower = detail.lower()

        if "trial" in detail_lower and "expired" in detail_lower:
            return DetailedErrorFactory.trial_expired(request_id=request_id)
        elif "plan" in detail_lower and "limit" in detail_lower:
            return DetailedErrorFactory.plan_limit_reached(
                reason=detail, request_id=request_id
            )
        elif "ip" in detail_lower:
            # Try to extract IP address
            ip = _extract_ip_address(detail)
            return DetailedErrorFactory.ip_restricted(
                ip_address=ip or "unknown", request_id=request_id
            )
        else:
            # Generic forbidden - use plan_limit_reached
            return DetailedErrorFactory.plan_limit_reached(
                reason=detail, request_id=request_id
            )

    elif status_code == 404:
        # Not found - check if it's a model error
        detail_lower = detail.lower()

        if "model" in detail_lower:
            # Extract model ID from detail
            model_id = _extract_model_id(detail)
            return DetailedErrorFactory.model_not_found(
                model_id=model_id or "unknown", request_id=request_id
            )
        else:
            # Generic not found
            return DetailedErrorFactory.model_not_found(
                model_id="resource", request_id=request_id
            )

    elif status_code == 429:
        # Rate limit exceeded
        # Try to extract retry_after from detail or headers
        retry_after = None
        if exc.headers and "Retry-After" in exc.headers:
            try:
                retry_after = int(exc.headers["Retry-After"])
            except ValueError:
                pass

        return DetailedErrorFactory.rate_limit_exceeded(
            limit_type="request_rate",
            retry_after=retry_after,
            request_id=request_id,
        )

    elif status_code in [502, 503, 504]:
        # Provider or service errors
        detail_lower = detail.lower()

        if "provider" in detail_lower or "upstream" in detail_lower:
            # Provider error
            provider, model = _extract_provider_and_model(detail)
            return DetailedErrorFactory.provider_error(
                provider=provider or "unknown",
                model=model or "unknown",
                provider_message=detail,
                status_code=status_code,
                request_id=request_id,
            )
        elif status_code == 503:
            return DetailedErrorFactory.service_unavailable(
                request_id=request_id
            )
        else:
            # Generic provider error
            return DetailedErrorFactory.provider_error(
                provider="unknown",
                model="unknown",
                provider_message=detail,
                status_code=status_code,
                request_id=request_id,
            )

    elif status_code >= 500:
        # Internal server error
        return DetailedErrorFactory.internal_error(
            operation="request_processing",
            request_id=request_id,
        )

    else:
        # Fallback for unknown status codes
        return DetailedErrorFactory.internal_error(
            operation="request_processing",
            request_id=request_id,
        )


def _extract_field_name(detail: str) -> str | None:
    """Extract field name from error detail message."""
    # Common patterns: "Missing required field: 'field_name'"
    import re

    patterns = [
        r"field[:\s]+['\"]([^'\"]+)['\"]",
        r"['\"]([^'\"]+)['\"].*required",
        r"missing[:\s]+['\"]([^'\"]+)['\"]",
    ]

    for pattern in patterns:
        match = re.search(pattern, detail, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def _extract_credit_amounts(detail: str) -> tuple[float | None, float | None]:
    """Extract current and required credit amounts from detail message."""
    import re

    # Pattern: "Required: $2.00, Current: $0.50"
    pattern = r"Required:\s*\$?([\d.]+).*Current:\s*\$?([\d.]+)"
    match = re.search(pattern, detail, re.IGNORECASE)

    if match:
        try:
            required = float(match.group(1))
            current = float(match.group(2))
            return current, required
        except ValueError:
            pass

    # Alternative pattern: "Insufficient credits. Current: $0.50, Required: $2.00"
    pattern2 = r"Current:\s*\$?([\d.]+).*Required:\s*\$?([\d.]+)"
    match2 = re.search(pattern2, detail, re.IGNORECASE)

    if match2:
        try:
            current = float(match2.group(1))
            required = float(match2.group(2))
            return current, required
        except ValueError:
            pass

    return None, None


def _extract_model_id(detail: str) -> str | None:
    """Extract model ID from error detail message."""
    import re

    # Common patterns: "Model 'gpt-4' not found", "Model: gpt-4"
    patterns = [
        r"[Mm]odel[:\s]+['\"]([^'\"]+)['\"]",
        r"['\"]([^'\"]+)['\"].*not found",
    ]

    for pattern in patterns:
        match = re.search(pattern, detail)
        if match:
            return match.group(1)

    return None


def _extract_ip_address(detail: str) -> str | None:
    """Extract IP address from error detail message."""
    import re

    # Pattern for IPv4: xxx.xxx.xxx.xxx
    pattern = r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
    match = re.search(pattern, detail)

    if match:
        return match.group(1)

    return None


def _extract_provider_and_model(detail: str) -> tuple[str | None, str | None]:
    """Extract provider and model from error detail message."""
    import re

    provider = None
    model = None

    # Pattern for provider: "Provider 'openrouter' failed"
    provider_pattern = r"[Pp]rovider[:\s]+['\"]([^'\"]+)['\"]"
    provider_match = re.search(provider_pattern, detail)
    if provider_match:
        provider = provider_match.group(1)

    # Pattern for model
    model_pattern = r"[Mm]odel[:\s]+['\"]([^'\"]+)['\"]"
    model_match = re.search(model_pattern, detail)
    if model_match:
        model = model_match.group(1)

    return provider, model


def create_error_response_dict(
    error_response: ErrorResponse,
    include_request_id_header: bool = True,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """
    Convert ErrorResponse to dict and optional headers.

    Args:
        error_response: ErrorResponse to convert
        include_request_id_header: Whether to include X-Request-ID header

    Returns:
        Tuple of (response_dict, headers)
    """
    response_dict = error_response.dict(exclude_none=True)

    headers = None
    if include_request_id_header:
        headers = {"X-Request-ID": error_response.error.request_id}

        # Add Retry-After header if present in context
        if error_response.error.context and error_response.error.context.retry_after:
            headers["Retry-After"] = str(error_response.error.context.retry_after)

    return response_dict, headers


def raise_detailed_error(error_response: ErrorResponse) -> None:
    """
    Raise an HTTPException with detailed error response.

    Args:
        error_response: ErrorResponse to raise

    Raises:
        HTTPException with detailed error

    Usage:
        error = DetailedErrorFactory.model_not_found("gpt-5")
        raise_detailed_error(error)
    """
    response_dict, headers = create_error_response_dict(error_response)

    raise HTTPException(
        status_code=error_response.error.status,
        detail=response_dict,
        headers=headers,
    )
