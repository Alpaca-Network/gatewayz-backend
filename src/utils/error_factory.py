"""
Error Factory

Factory functions for creating detailed, user-friendly error responses.
Provides standardized error creation across the entire API.

Usage:
    from src.utils.error_factory import DetailedErrorFactory

    # Model not found error
    error = DetailedErrorFactory.model_not_found(
        model_id="gpt-5",
        suggested_models=["gpt-4", "gpt-4-turbo"],
        request_id="req_123"
    )

    # Raise as HTTPException
    raise HTTPException(
        status_code=error.error.status,
        detail=error.dict(exclude_none=True)
    )
"""

import uuid
from datetime import datetime
from typing import Any

from src.schemas.errors import ErrorContext, ErrorDetail, ErrorResponse
from src.utils.error_codes import ErrorCode, get_error_type, get_status_code
from src.utils.error_messages import (
    get_docs_url,
    get_error_detail,
    get_error_message,
    get_suggestions,
)


class DetailedErrorFactory:
    """Factory class for creating detailed error responses."""

    # ==================== Model Errors ====================

    @staticmethod
    def model_not_found(
        model_id: str,
        provider: str | None = None,
        suggested_models: list[str] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a model not found error with suggestions.

        Args:
            model_id: The requested model ID
            provider: Optional provider name
            suggested_models: Optional list of similar models
            request_id: Optional request ID

        Returns:
            ErrorResponse with detailed model not found error
        """
        code = ErrorCode.MODEL_NOT_FOUND
        message = get_error_message(code, model_id=model_id)
        detail = get_error_detail(code)
        suggestions = get_suggestions(code)

        # Add specific model suggestions if provided
        if suggested_models:
            suggestions.insert(
                1, f"Try using one of these similar models: {', '.join(suggested_models[:3])}"
            )

        context = ErrorContext(
            requested_model=model_id,
            suggested_models=suggested_models,
            provider=provider,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def model_unavailable(
        model_id: str,
        provider: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a model unavailable error."""
        code = ErrorCode.MODEL_UNAVAILABLE
        message = get_error_message(code, model_id=model_id)
        detail = get_error_detail(code)
        if reason:
            detail = f"{detail} Reason: {reason}"

        context = ErrorContext(
            requested_model=model_id,
            provider=provider,
            provider_status=reason,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Validation Errors ====================

    @staticmethod
    def missing_required_field(
        field_name: str,
        endpoint: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a missing required field error."""
        code = ErrorCode.MISSING_REQUIRED_FIELD
        message = get_error_message(code, field_name=field_name)

        context = ErrorContext(
            parameter_name=field_name,
            endpoint=endpoint,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def invalid_parameter(
        parameter_name: str,
        parameter_value: Any,
        expected_type: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        allowed_values: list[Any] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an invalid parameter error.

        Args:
            parameter_name: Name of the invalid parameter
            parameter_value: The invalid value provided
            expected_type: Expected type (for type mismatches)
            min_value: Minimum allowed value (for range errors)
            max_value: Maximum allowed value (for range errors)
            allowed_values: List of allowed values
            request_id: Optional request ID

        Returns:
            ErrorResponse with parameter validation error
        """
        # Determine which error code to use
        if min_value is not None or max_value is not None:
            code = ErrorCode.PARAMETER_OUT_OF_RANGE
            message = get_error_message(
                code,
                parameter_name=parameter_name,
                value=parameter_value,
                min_value=min_value or 0,
                max_value=max_value or 0,
            )
        elif expected_type:
            code = ErrorCode.INVALID_PARAMETER_TYPE
            actual_type = type(parameter_value).__name__
            message = get_error_message(
                code,
                parameter_name=parameter_name,
                expected_type=expected_type,
                actual_type=actual_type,
            )
        else:
            # Generic parameter error
            code = ErrorCode.INVALID_PARAMETER_TYPE
            message = f"Invalid value for parameter '{parameter_name}'"

        context = ErrorContext(
            parameter_name=parameter_name,
            parameter_value=parameter_value,
            expected_type=expected_type,
            min_value=min_value,
            max_value=max_value,
            allowed_values=allowed_values,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def context_length_exceeded(
        input_tokens: int,
        max_context_length: int,
        model_id: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a context length exceeded error."""
        code = ErrorCode.CONTEXT_LENGTH_EXCEEDED
        message = get_error_message(
            code,
            input_tokens=input_tokens,
            max_context=max_context_length,
        )

        context = ErrorContext(
            input_tokens=input_tokens,
            max_context_length=max_context_length,
            requested_model=model_id,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def empty_messages_array(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an empty messages array error."""
        code = ErrorCode.EMPTY_MESSAGES_ARRAY
        message = get_error_message(code)

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Authentication Errors ====================

    @staticmethod
    def invalid_api_key(
        reason: str | None = None,
        key_prefix: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an invalid API key error.

        Args:
            reason: Optional reason for invalidity
            key_prefix: First few characters of the key (for debugging)
            request_id: Optional request ID

        Returns:
            ErrorResponse with invalid API key error
        """
        code = ErrorCode.INVALID_API_KEY
        message = get_error_message(code)
        detail = get_error_detail(code)
        if reason:
            detail = f"{detail} {reason}"

        context = ErrorContext(
            key_prefix=key_prefix,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def api_key_missing(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an API key missing error."""
        code = ErrorCode.API_KEY_MISSING

        error = ErrorDetail(
            type=get_error_type(code),
            message=get_error_message(code),
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Authorization Errors ====================

    @staticmethod
    def trial_expired(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a trial expired error."""
        code = ErrorCode.TRIAL_EXPIRED

        context = ErrorContext(
            trial_status="expired",
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=get_error_message(code),
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def plan_limit_reached(
        reason: str,
        plan_name: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a plan limit reached error."""
        code = ErrorCode.PLAN_LIMIT_REACHED
        message = get_error_message(code, reason=reason)

        context = ErrorContext(
            plan_name=plan_name,
            plan_limit=limit,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def ip_restricted(
        ip_address: str,
        allowed_ips: list[str] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an IP restricted error."""
        code = ErrorCode.IP_RESTRICTED
        message = get_error_message(code, ip_address=ip_address)

        context = ErrorContext(
            ip_address=ip_address,
            allowed_ips=allowed_ips,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Payment & Credit Errors ====================

    @staticmethod
    def insufficient_credits(
        current_credits: float,
        required_credits: float,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an insufficient credits error.

        SECURITY: Does not expose exact credit amounts in the response.
        Amounts are kept for internal logging only.

        Args:
            current_credits: User's current credit balance (logged server-side only)
            required_credits: Credits required for the request (logged server-side only)
            request_id: Optional request ID

        Returns:
            ErrorResponse with sanitized insufficient credits error
        """
        code = ErrorCode.INSUFFICIENT_CREDITS
        message = get_error_message(code)
        detail = "Please add credits to your account to complete this request."

        # SECURITY: Do not include exact credit amounts in the response context
        context = ErrorContext()

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    @staticmethod
    def insufficient_credits_for_reservation(
        current_credits: float,
        max_cost: float,
        model_id: str,
        max_tokens: int,
        input_tokens: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an insufficient credits error for credit reservation (pre-flight check).

        This error is raised BEFORE making a provider request when the user doesn't
        have enough credits to cover the maximum possible cost.

        Args:
            current_credits: User's current credit balance
            max_cost: Maximum possible cost for the request
            model_id: Model being requested
            max_tokens: Maximum output tokens parameter
            input_tokens: Optional estimated input tokens
            request_id: Optional request ID

        Returns:
            ErrorResponse with detailed insufficient credits error for reservation
        """
        code = ErrorCode.INSUFFICIENT_CREDITS

        # SECURITY: Sanitize user-facing message - no dollar amounts or credit values
        message = "Insufficient credits for this request. Please add credits to continue."

        detail = (
            "Your account does not have enough credits for this request. "
            "Consider reducing max_tokens or using a less expensive model."
        )

        # SECURITY: Sanitized suggestions without dollar amounts
        suggestions = [
            "Add more credits to your account",
            "Reduce max_tokens to lower the maximum possible cost",
            "Use a less expensive model",
            "Visit https://gatewayz.ai/pricing to add credits",
        ]

        # SECURITY: Do not include exact credit amounts in the response context
        context = ErrorContext(
            requested_model=model_id,
            requested_max_tokens=max_tokens,
            input_tokens=input_tokens,
            additional_info={
                "reason": "pre_flight_check",
                "check_type": "credit_reservation",
                "note": "This is a conservative estimate. Actual cost may be lower based on actual token usage.",
            },
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url="https://docs.gatewayz.ai/pricing-and-billing/credits",
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    # ==================== Rate Limiting Errors ====================

    @staticmethod
    def rate_limit_exceeded(
        limit_type: str,
        retry_after: int | None = None,
        limit_value: int | None = None,
        current_usage: int | None = None,
        reset_time: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a rate limit exceeded error.

        Args:
            limit_type: Type of rate limit (e.g., "requests_per_minute")
            retry_after: Seconds until retry is allowed
            limit_value: Rate limit threshold
            current_usage: Current usage count
            reset_time: When the limit resets (ISO 8601)
            request_id: Optional request ID

        Returns:
            ErrorResponse with rate limit error
        """
        code = ErrorCode.RATE_LIMIT_EXCEEDED
        message = get_error_message(code, limit_type=limit_type)

        context = ErrorContext(
            limit_type=limit_type,
            limit_value=limit_value,
            current_usage=current_usage,
            retry_after=retry_after,
            reset_time=reset_time,
        )

        suggestions = get_suggestions(code)
        if retry_after:
            suggestions.insert(0, f"Wait {retry_after} seconds before retrying")

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def daily_quota_exceeded(
        limit: int,
        used: int,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a daily quota exceeded error."""
        code = ErrorCode.DAILY_QUOTA_EXCEEDED
        message = get_error_message(code, limit=limit, used=used)

        context = ErrorContext(
            limit_type="daily_quota",
            limit_value=limit,
            current_usage=used,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Provider Errors ====================

    @staticmethod
    def provider_error(
        provider: str,
        model: str,
        provider_message: str | None = None,
        status_code: int = 502,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a provider error.

        Args:
            provider: Provider name
            model: Model ID
            provider_message: Original provider error message
            status_code: HTTP status code (default 502)
            request_id: Optional request ID

        Returns:
            ErrorResponse with provider error
        """
        code = ErrorCode.PROVIDER_ERROR
        message = get_error_message(
            code,
            provider=provider,
            model_id=model,
            error_message=provider_message or "Unknown error",
        )

        context = ErrorContext(
            provider=provider,
            requested_model=model,
            provider_error_message=provider_message,
            provider_status_code=status_code,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=status_code,
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def provider_timeout(
        provider: str,
        model: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a provider timeout error."""
        code = ErrorCode.PROVIDER_TIMEOUT
        message = get_error_message(code, provider=provider)

        context = ErrorContext(
            provider=provider,
            requested_model=model,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def provider_unavailable(
        provider: str,
        model: str,
        retry_after: int = 60,
        circuit_breaker_state: str = "open",
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a provider unavailable error (circuit breaker open).

        This error is raised when the circuit breaker is open for a provider,
        preventing requests from being sent to an unhealthy provider.

        Args:
            provider: Provider name
            model: Model ID
            retry_after: Seconds until provider will be retried
            circuit_breaker_state: Current circuit breaker state (open/half_open)
            request_id: Optional request ID

        Returns:
            ErrorResponse with provider unavailable error (503)
        """
        code = ErrorCode.PROVIDER_ERROR
        message = f"Provider '{provider}' is temporarily unavailable for model '{model}'"

        detail = (
            f"The circuit breaker for provider '{provider}' is currently {circuit_breaker_state.upper()}. "
            f"This provider has been experiencing issues and has been temporarily disabled to prevent cascading failures. "
            f"The provider will be automatically retried in {retry_after} seconds."
        )

        context = ErrorContext(
            provider=provider,
            requested_model=model,
            provider_status="circuit_breaker_open",
            retry_after=retry_after,
            additional_info={
                "circuit_breaker_state": circuit_breaker_state,
                "auto_retry_in_seconds": retry_after,
                "reason": "Provider health check failed, circuit breaker activated",
            },
        )

        suggestions = [
            f"Wait {retry_after} seconds for automatic retry",
            "Try a different model that uses a different provider",
            "Check the status page for provider health: https://status.gatewayz.ai",
            "Contact support if the issue persists",
        ]

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=503,  # Service Unavailable
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url="https://docs.gatewayz.ai/troubleshooting/circuit-breaker",
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    @staticmethod
    def all_providers_failed(
        model: str,
        failed_providers: list[str],
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an all providers failed error."""
        code = ErrorCode.ALL_PROVIDERS_FAILED
        message = get_error_message(code, model_id=model)

        context = ErrorContext(
            requested_model=model,
            failed_providers=failed_providers,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Service Errors ====================

    @staticmethod
    def internal_error(
        operation: str = "operation",
        error: Exception | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an internal server error.

        Args:
            operation: Name of operation that failed
            error: Optional exception that caused the error
            request_id: Optional request ID

        Returns:
            ErrorResponse with internal error
        """
        code = ErrorCode.INTERNAL_ERROR
        message = get_error_message(code)

        # Don't expose internal error details to users
        error_info = None
        if error:
            error_info = {"operation": operation}

        context = ErrorContext(
            additional_info=error_info,
        )

        resp = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=resp)

    @staticmethod
    def service_unavailable(
        service: str = "service",
        retry_after: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a service unavailable error."""
        code = ErrorCode.SERVICE_UNAVAILABLE
        message = get_error_message(code)

        context = ErrorContext(
            retry_after=retry_after,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def database_error(
        operation: str = "database operation",
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a database error."""
        code = ErrorCode.DATABASE_ERROR
        message = get_error_message(code)

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)
