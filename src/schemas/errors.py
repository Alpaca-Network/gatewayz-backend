"""
Error Response Schemas

Standardized Pydantic models for detailed error responses across all API endpoints.
Provides comprehensive error information including context, suggestions, and documentation links.

Usage:
    from src.schemas.errors import ErrorResponse, ErrorDetail, ErrorContext

    error = ErrorResponse(
        error=ErrorDetail(
            type="model_not_found",
            message="Model 'gpt-5' not found",
            code="MODEL_NOT_FOUND",
            status=404,
            request_id="req_123",
            timestamp="2025-01-21T12:00:00Z"
        )
    )
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorContext(BaseModel):
    """
    Additional context for debugging errors.

    Provides relevant details about the error condition without exposing sensitive data.
    """

    # Model-related context
    requested_model: str | None = Field(None, description="Model ID that was requested")
    suggested_models: list[str] | None = Field(None, description="Similar or alternative models")
    available_models: list[str] | None = Field(None, description="List of available models")
    provider: str | None = Field(None, description="Provider name")
    provider_status: str | None = Field(None, description="Provider availability status")

    # Endpoint/Request context
    endpoint: str | None = Field(None, description="API endpoint that was called")
    method: str | None = Field(None, description="HTTP method used")

    # Parameter validation context
    parameter_name: str | None = Field(None, description="Name of invalid parameter")
    parameter_value: Any | None = Field(None, description="Value that was provided")
    expected_type: str | None = Field(None, description="Expected parameter type")
    min_value: float | None = Field(None, description="Minimum allowed value")
    max_value: float | None = Field(None, description="Maximum allowed value")
    allowed_values: list[Any] | None = Field(None, description="List of allowed values")

    # Credit/Payment context
    current_credits: float | None = Field(None, description="User's current credit balance")
    required_credits: float | None = Field(None, description="Credits required for request")
    credit_deficit: float | None = Field(None, description="Amount of credits needed")

    # Rate limiting context
    limit_type: str | None = Field(None, description="Type of rate limit hit")
    limit_value: int | None = Field(None, description="Rate limit threshold")
    current_usage: int | None = Field(None, description="Current usage count")
    retry_after: int | None = Field(None, description="Seconds until retry is allowed")
    reset_time: str | None = Field(None, description="When the limit resets (ISO 8601)")

    # Trial/Plan context
    trial_status: str | None = Field(None, description="Trial account status")
    plan_name: str | None = Field(None, description="Subscription plan name")
    plan_limit: int | None = Field(None, description="Plan usage limit")

    # Authentication context
    key_prefix: str | None = Field(None, description="First few characters of API key")
    ip_address: str | None = Field(None, description="Request IP address")
    allowed_ips: list[str] | None = Field(None, description="List of allowed IPs")

    # Provider error context
    provider_error_message: str | None = Field(None, description="Original provider error message")
    provider_status_code: int | None = Field(None, description="Provider HTTP status code")
    failed_providers: list[str] | None = Field(None, description="List of providers that failed")

    # Token/Context length errors
    input_tokens: int | None = Field(None, description="Number of input tokens")
    max_context_length: int | None = Field(None, description="Model's maximum context length")
    requested_max_tokens: int | None = Field(None, description="Requested max output tokens")

    # Additional context (flexible)
    additional_info: dict[str, Any] | None = Field(
        None, description="Additional error-specific context"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "requested_model": "gpt-5-ultra",
                "suggested_models": ["gpt-4", "gpt-4-turbo"],
                "provider": "openrouter",
                "current_credits": 0.50,
                "required_credits": 2.00,
            }
        }


class ErrorDetail(BaseModel):
    """
    Detailed error information structure.

    Provides comprehensive error details including type, message, suggestions,
    and links to documentation.
    """

    type: str = Field(
        ...,
        description="Error type identifier (snake_case, e.g., 'model_not_found')",
        examples=["model_not_found", "insufficient_credits", "rate_limit_exceeded"],
    )

    message: str = Field(
        ..., description="Human-readable error message", examples=["Model 'gpt-5-ultra' not found"]
    )

    detail: str | None = Field(
        None,
        description="Additional explanation and context about the error",
        examples=[
            "The requested model is not available in our catalog. Please check the model name and try again."
        ],
    )

    code: str = Field(
        ...,
        description="Error code constant (UPPER_SNAKE_CASE)",
        examples=["MODEL_NOT_FOUND", "INSUFFICIENT_CREDITS"],
    )

    status: int = Field(
        ..., description="HTTP status code", ge=400, le=599, examples=[404, 402, 429, 500]
    )

    request_id: str = Field(
        ...,
        description="Unique request identifier for support and tracking",
        examples=["req_abc123", "550e8400-e29b-41d4-a716-446655440000"],
    )

    timestamp: str = Field(
        ..., description="ISO 8601 timestamp when error occurred", examples=["2025-01-21T12:00:00Z"]
    )

    suggestions: list[str] | None = Field(
        None,
        description="Actionable suggestions to resolve the error",
        examples=[
            [
                "Check available models at /v1/models",
                "Visit https://docs.gatewayz.ai/models for the complete model list",
            ]
        ],
    )

    context: ErrorContext | None = Field(None, description="Additional context for debugging")

    docs_url: str | None = Field(
        None,
        description="Link to relevant documentation",
        examples=["https://docs.gatewayz.ai/errors/model-not-found"],
    )

    support_url: str | None = Field(
        None,
        description="Link to support or contact page",
        examples=["https://gatewayz.ai/support"],
    )

    class Config:
        json_schema_extra = {
            "example": {
                "type": "model_not_found",
                "message": "Model 'gpt-5-ultra' not found",
                "detail": "Model 'gpt-5-ultra' is not available in our catalog. Did you mean 'gpt-4'?",
                "code": "MODEL_NOT_FOUND",
                "status": 404,
                "request_id": "req_abc123",
                "timestamp": "2025-01-21T12:00:00Z",
                "suggestions": [
                    "Check available models at /v1/models",
                    "Try using 'gpt-4' or 'gpt-3.5-turbo' instead",
                    "Visit https://docs.gatewayz.ai/models for model list",
                ],
                "context": {
                    "requested_model": "gpt-5-ultra",
                    "suggested_models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
                    "provider": "openrouter",
                },
                "docs_url": "https://docs.gatewayz.ai/errors/model-not-found",
            }
        }


class ErrorResponse(BaseModel):
    """
    Top-level error response wrapper.

    Wraps ErrorDetail in an 'error' field for consistency with OpenAI and Anthropic APIs.
    """

    error: ErrorDetail = Field(..., description="Detailed error information")

    class Config:
        json_schema_extra = {
            "example": {
                "error": {
                    "type": "insufficient_credits",
                    "message": "Insufficient credits. Please add credits to continue.",
                    "detail": "Please add credits to your account to complete this request.",
                    "code": "INSUFFICIENT_CREDITS",
                    "status": 402,
                    "request_id": "req_xyz789",
                    "timestamp": "2025-01-21T12:00:00Z",
                    "suggestions": [
                        "Add credits at https://gatewayz.ai/billing",
                        "Consider upgrading to a subscription plan for better rates",
                    ],
                    "context": {},
                    "docs_url": "https://docs.gatewayz.ai/errors/insufficient-credits",
                    "support_url": "https://gatewayz.ai/support",
                }
            }
        }


# Convenience function for creating simple errors
def create_simple_error(
    error_type: str,
    message: str,
    status: int,
    code: str,
    request_id: str | None = None,
    detail: str | None = None,
    suggestions: list[str] | None = None,
) -> ErrorResponse:
    """
    Create a simple error response without extensive context.

    Args:
        error_type: Error type identifier
        message: Human-readable error message
        status: HTTP status code
        code: Error code constant
        request_id: Optional request ID (generated if not provided)
        detail: Optional additional explanation
        suggestions: Optional list of suggestions

    Returns:
        ErrorResponse object
    """
    import uuid

    return ErrorResponse(
        error=ErrorDetail(
            type=error_type,
            message=message,
            detail=detail,
            code=code,
            status=status,
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
        )
    )
