"""
HTTP Exception Factories

Centralized exception creation with consistent error messages and status codes.
Eliminates 308+ duplicate HTTPException patterns across the codebase.

Supports both simple and detailed error modes:
- Simple mode: Traditional HTTPException with string detail
- Detailed mode: Rich error responses with context, suggestions, and documentation

Usage:
    from src.utils.exceptions import APIExceptions

    # Simple mode (backward compatible):
    raise APIExceptions.unauthorized()

    # Detailed mode:
    raise APIExceptions.model_not_found_detailed(
        model_id="gpt-5",
        suggested_models=["gpt-4", "gpt-4-turbo"],
        request_id="req_123"
    )
"""

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class APIExceptions:
    """Factory class for creating standardized HTTP exceptions."""

    @staticmethod
    def unauthorized(detail: str = "Invalid API key or unauthorized access") -> HTTPException:
        """
        401 Unauthorized - Authentication failed.

        Args:
            detail: Custom error message

        Returns:
            HTTPException with status 401
        """
        return HTTPException(status_code=401, detail=detail)

    @staticmethod
    def invalid_api_key() -> HTTPException:
        """401 Unauthorized - Specific for invalid API key."""
        return HTTPException(status_code=401, detail="Invalid API key")

    @staticmethod
    def payment_required(
        detail: str = "Insufficient credits", credits: float | None = None
    ) -> HTTPException:
        """
        402 Payment Required - User has insufficient credits.

        Args:
            detail: Custom error message
            credits: Optional current credit balance

        Returns:
            HTTPException with status 402
        """
        if credits is not None:
            detail = f"{detail}. Current balance: ${credits:.4f}"
        return HTTPException(status_code=402, detail=detail)

    @staticmethod
    def forbidden(detail: str = "Access forbidden") -> HTTPException:
        """
        403 Forbidden - User doesn't have permission.

        Args:
            detail: Custom error message

        Returns:
            HTTPException with status 403
        """
        return HTTPException(status_code=403, detail=detail)

    @staticmethod
    def not_found(resource: str = "Resource", resource_id: Any | None = None) -> HTTPException:
        """
        404 Not Found - Resource doesn't exist.

        Args:
            resource: Type of resource (e.g., "User", "Model", "Session")
            resource_id: Optional ID of the resource

        Returns:
            HTTPException with status 404
        """
        detail = f"{resource} not found"
        if resource_id is not None:
            detail += f": {resource_id}"
        return HTTPException(status_code=404, detail=detail)

    @staticmethod
    def rate_limited(
        retry_after: int | None = None,
        detail: str = "Rate limit exceeded",
        reason: str | None = None,
    ) -> HTTPException:
        """
        429 Too Many Requests - Rate limit exceeded.

        Args:
            retry_after: Seconds until retry is allowed
            detail: Custom error message
            reason: Optional reason for rate limit (e.g., "token_limit", "request_limit")

        Returns:
            HTTPException with status 429 and optional Retry-After header
        """
        if reason:
            detail = f"{detail}: {reason}"

        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return HTTPException(status_code=429, detail=detail, headers=headers)

    @staticmethod
    def plan_limit_exceeded(reason: str = "unknown") -> HTTPException:
        """
        429 Too Many Requests - Plan limit exceeded.

        Args:
            reason: Reason for limit (e.g., "monthly_quota", "request_limit")

        Returns:
            HTTPException with status 429
        """
        return HTTPException(status_code=429, detail=f"Plan limit exceeded: {reason}")

    @staticmethod
    def bad_request(
        detail: str = "Bad request", errors: dict[str, Any] | None = None
    ) -> HTTPException:
        """
        400 Bad Request - Invalid request data.

        Args:
            detail: Error message
            errors: Optional validation errors dict

        Returns:
            HTTPException with status 400
        """
        if errors:
            return HTTPException(status_code=400, detail={"message": detail, "errors": errors})
        return HTTPException(status_code=400, detail=detail)

    @staticmethod
    def internal_error(
        operation: str = "operation", error: Exception | None = None, include_details: bool = False
    ) -> HTTPException:
        """
        500 Internal Server Error - Server-side error.

        Args:
            operation: Name of the operation that failed
            error: Optional exception that caused the error
            include_details: Whether to include error details (dev mode only)

        Returns:
            HTTPException with status 500
        """
        detail = f"Internal error during {operation}"

        if error and include_details:
            detail += f": {str(error)}"

        # Log the full error for debugging
        if error:
            logger.error(f"Internal error in {operation}: {error}", exc_info=True)

        return HTTPException(status_code=500, detail=detail)

    @staticmethod
    def database_error(
        operation: str = "database operation", error: Exception | None = None
    ) -> HTTPException:
        """
        500 Internal Server Error - Database operation failed.

        Args:
            operation: Name of the database operation
            error: Optional exception

        Returns:
            HTTPException with status 500
        """
        logger.error(f"Database error during {operation}: {error}", exc_info=True)
        return HTTPException(status_code=500, detail=f"Database error during {operation}")

    @staticmethod
    def provider_error(
        provider: str, model: str, error: Exception | None = None, status_code: int = 502
    ) -> HTTPException:
        """
        502 Bad Gateway - Upstream provider error.

        Args:
            provider: Provider name (e.g., "openrouter", "anthropic")
            model: Model name
            error: Optional exception from provider
            status_code: HTTP status code (default 502)

        Returns:
            HTTPException with appropriate status code
        """
        detail = f"Provider {provider} failed for model {model}"

        if error:
            logger.error(f"Provider error ({provider}/{model}): {error}", exc_info=True)

        return HTTPException(status_code=status_code, detail=detail)

    @staticmethod
    def service_unavailable(
        service: str = "service", retry_after: int | None = None
    ) -> HTTPException:
        """
        503 Service Unavailable - Service temporarily unavailable.

        Args:
            service: Name of the unavailable service
            retry_after: Optional seconds to wait before retry

        Returns:
            HTTPException with status 503
        """
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return HTTPException(
            status_code=503, detail=f"{service} is temporarily unavailable", headers=headers
        )

    @staticmethod
    def validation_error(field: str, message: str) -> HTTPException:
        """
        422 Unprocessable Entity - Validation failed.

        Args:
            field: Field that failed validation
            message: Validation error message

        Returns:
            HTTPException with status 422
        """
        return HTTPException(status_code=422, detail={"field": field, "message": message})

    # ==================== Detailed Error Methods ====================
    # These methods use the DetailedErrorFactory for rich error responses

    @staticmethod
    def model_not_found_detailed(
        model_id: str,
        provider: str | None = None,
        suggested_models: list[str] | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        404 Model Not Found - Detailed version with suggestions.

        Args:
            model_id: The requested model ID
            provider: Optional provider name
            suggested_models: Optional list of similar models
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.model_not_found(
            model_id=model_id,
            provider=provider,
            suggested_models=suggested_models,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def insufficient_credits_detailed(
        current_credits: float,
        required_credits: float,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        402 Insufficient Credits - Detailed version with amounts.

        Args:
            current_credits: User's current credit balance
            required_credits: Credits required for request
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.insufficient_credits(
            current_credits=current_credits,
            required_credits=required_credits,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def insufficient_credits_for_reservation(
        current_credits: float,
        max_cost: float,
        model_id: str,
        max_tokens: int,
        input_tokens: int | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        402 Insufficient Credits - Pre-flight check version with max cost details.

        This error is raised BEFORE making a provider request when the user doesn't
        have enough credits to cover the maximum possible cost (based on max_tokens).

        Args:
            current_credits: User's current credit balance
            max_cost: Maximum possible cost for the request
            model_id: Model being requested
            max_tokens: Maximum output tokens parameter
            input_tokens: Optional estimated input tokens
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response including:
            - Maximum possible cost
            - Current balance
            - Shortfall amount
            - Actionable suggestions (reduce max_tokens, add credits)
            - Calculated recommended max_tokens

        Example:
            >>> raise APIExceptions.insufficient_credits_for_reservation(
            ...     current_credits=0.05,
            ...     max_cost=0.20,
            ...     model_id="gpt-4o",
            ...     max_tokens=4096,
            ...     input_tokens=100
            ... )
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=current_credits,
            max_cost=max_cost,
            model_id=model_id,
            max_tokens=max_tokens,
            input_tokens=input_tokens,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def invalid_api_key_detailed(
        reason: str | None = None,
        key_prefix: str | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        401 Invalid API Key - Detailed version.

        Args:
            reason: Optional reason for invalidity
            key_prefix: First few characters of the key
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.invalid_api_key(
            reason=reason,
            key_prefix=key_prefix,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def rate_limit_exceeded_detailed(
        limit_type: str,
        retry_after: int | None = None,
        limit_value: int | None = None,
        current_usage: int | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        429 Rate Limit Exceeded - Detailed version with retry info.

        Args:
            limit_type: Type of rate limit
            retry_after: Seconds until retry is allowed
            limit_value: Rate limit threshold
            current_usage: Current usage count
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.rate_limit_exceeded(
            limit_type=limit_type,
            retry_after=retry_after,
            limit_value=limit_value,
            current_usage=current_usage,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def provider_error_detailed(
        provider: str,
        model: str,
        provider_message: str | None = None,
        status_code: int = 502,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        502 Provider Error - Detailed version.

        Args:
            provider: Provider name
            model: Model ID
            provider_message: Original provider error message
            status_code: HTTP status code
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.provider_error(
            provider=provider,
            model=model,
            provider_message=provider_message,
            status_code=status_code,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def invalid_parameter_detailed(
        parameter_name: str,
        parameter_value: Any,
        expected_type: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        allowed_values: list[Any] | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        400 Invalid Parameter - Detailed version.

        Args:
            parameter_name: Name of invalid parameter
            parameter_value: The invalid value
            expected_type: Expected type (for type mismatches)
            min_value: Minimum allowed value
            max_value: Maximum allowed value
            allowed_values: List of allowed values
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_factory import DetailedErrorFactory
        from src.utils.error_handlers import create_error_response_dict

        error = DetailedErrorFactory.invalid_parameter(
            parameter_name=parameter_name,
            parameter_value=parameter_value,
            expected_type=expected_type,
            min_value=min_value,
            max_value=max_value,
            allowed_values=allowed_values,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )


# Convenience aliases for common exceptions
unauthorized = APIExceptions.unauthorized
invalid_api_key = APIExceptions.invalid_api_key
payment_required = APIExceptions.payment_required
forbidden = APIExceptions.forbidden
not_found = APIExceptions.not_found
rate_limited = APIExceptions.rate_limited
plan_limit_exceeded = APIExceptions.plan_limit_exceeded
bad_request = APIExceptions.bad_request
internal_error = APIExceptions.internal_error
database_error = APIExceptions.database_error
provider_error = APIExceptions.provider_error
service_unavailable = APIExceptions.service_unavailable
validation_error = APIExceptions.validation_error
