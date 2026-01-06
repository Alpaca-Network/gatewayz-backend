"""
HTTP Exception Factories

Centralized exception creation with consistent error messages and status codes.
Eliminates 308+ duplicate HTTPException patterns across the codebase.

Usage:
    from src.utils.exceptions import APIExceptions

    # Instead of:
    raise HTTPException(status_code=401, detail="Invalid API key")

    # Use:
    raise APIExceptions.unauthorized()
"""

from fastapi import HTTPException
from typing import Optional, Dict, Any
import logging

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
    def payment_required(detail: str = "Insufficient credits", credits: Optional[float] = None) -> HTTPException:
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
    def not_found(resource: str = "Resource", resource_id: Optional[Any] = None) -> HTTPException:
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
        retry_after: Optional[int] = None,
        detail: str = "Rate limit exceeded",
        reason: Optional[str] = None
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
        return HTTPException(
            status_code=429,
            detail=f"Plan limit exceeded: {reason}"
        )

    @staticmethod
    def bad_request(detail: str = "Bad request", errors: Optional[Dict[str, Any]] = None) -> HTTPException:
        """
        400 Bad Request - Invalid request data.

        Args:
            detail: Error message
            errors: Optional validation errors dict

        Returns:
            HTTPException with status 400
        """
        if errors:
            return HTTPException(
                status_code=400,
                detail={"message": detail, "errors": errors}
            )
        return HTTPException(status_code=400, detail=detail)

    @staticmethod
    def internal_error(
        operation: str = "operation",
        error: Optional[Exception] = None,
        include_details: bool = False
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
    def database_error(operation: str = "database operation", error: Optional[Exception] = None) -> HTTPException:
        """
        500 Internal Server Error - Database operation failed.

        Args:
            operation: Name of the database operation
            error: Optional exception

        Returns:
            HTTPException with status 500
        """
        logger.error(f"Database error during {operation}: {error}", exc_info=True)
        return HTTPException(
            status_code=500,
            detail=f"Database error during {operation}"
        )

    @staticmethod
    def provider_error(
        provider: str,
        model: str,
        error: Optional[Exception] = None,
        status_code: int = 502
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
    def service_unavailable(service: str = "service", retry_after: Optional[int] = None) -> HTTPException:
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
            status_code=503,
            detail=f"{service} is temporarily unavailable",
            headers=headers
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
        return HTTPException(
            status_code=422,
            detail={
                "field": field,
                "message": message
            }
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
