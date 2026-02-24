"""
Unit tests for error response Pydantic schemas.

Tests ErrorContext, ErrorDetail, and ErrorResponse models to ensure
proper validation and serialization.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.schemas.errors import ErrorContext, ErrorDetail, ErrorResponse


class TestErrorContext:
    """Test suite for ErrorContext schema."""

    def test_minimal_context(self):
        """Test creating minimal context with no fields."""
        context = ErrorContext()

        assert context is not None
        # All fields should be None
        assert context.requested_model is None
        assert context.suggested_models is None
        assert context.provider is None

    def test_model_not_found_context(self):
        """Test context for model not found error."""
        context = ErrorContext(
            requested_model="gpt-5",
            suggested_models=["gpt-4", "gpt-4-turbo"],
            provider="openrouter",
        )

        assert context.requested_model == "gpt-5"
        assert context.suggested_models == ["gpt-4", "gpt-4-turbo"]
        assert context.provider == "openrouter"

    def test_insufficient_credits_context(self):
        """Test context for insufficient credits error."""
        context = ErrorContext(current_credits=0.5, required_credits=2.0, credit_deficit=1.5)

        assert context.current_credits == 0.5
        assert context.required_credits == 2.0
        assert context.credit_deficit == 1.5

    def test_rate_limit_context(self):
        """Test context for rate limit error."""
        context = ErrorContext(
            limit_type="requests_per_minute",
            limit_value=100,
            current_usage=105,
            retry_after=60,
        )

        assert context.limit_type == "requests_per_minute"
        assert context.limit_value == 100
        assert context.current_usage == 105
        assert context.retry_after == 60

    def test_parameter_validation_context(self):
        """Test context for parameter validation error."""
        context = ErrorContext(
            parameter_name="temperature",
            parameter_value=5.0,
            expected_type="float",
            min_value=0.0,
            max_value=2.0,
        )

        assert context.parameter_name == "temperature"
        assert context.parameter_value == 5.0
        assert context.expected_type == "float"
        assert context.min_value == 0.0
        assert context.max_value == 2.0

    def test_provider_error_context(self):
        """Test context for provider error."""
        context = ErrorContext(
            provider="openrouter",
            requested_model="gpt-4",
            provider_message="Connection timeout",
            provider_status_code=504,
        )

        assert context.provider == "openrouter"
        assert context.requested_model == "gpt-4"
        assert context.provider_message == "Connection timeout"
        assert context.provider_status_code == 504

    def test_internal_error_context(self):
        """Test context for internal error."""
        context = ErrorContext(
            error_type="ValueError",
            error_message="Something went wrong",
            operation="database_query",
        )

        assert context.error_type == "ValueError"
        assert context.error_message == "Something went wrong"
        assert context.operation == "database_query"

    def test_api_key_context(self):
        """Test context for API key error."""
        context = ErrorContext(api_key_prefix="gw_live_abc", api_key_valid=False)

        assert context.api_key_prefix == "gw_live_abc"
        assert context.api_key_valid is False

    def test_serialization(self):
        """Test context serialization to dict."""
        context = ErrorContext(requested_model="test", provider="test-provider")

        data = context.dict(exclude_none=True)

        assert "requested_model" in data
        assert "provider" in data
        # None values should be excluded
        assert "suggested_models" not in data

    def test_allowed_values_list(self):
        """Test that allowed_values accepts list."""
        context = ErrorContext(allowed_values=["json", "text", "yaml"])

        assert context.allowed_values == ["json", "text", "yaml"]


class TestErrorDetail:
    """Test suite for ErrorDetail schema."""

    def test_minimal_error_detail(self):
        """Test creating minimal valid error detail."""
        error = ErrorDetail(
            type="test_error",
            message="Test message",
            code="TEST_ERROR",
            status=400,
            request_id="test_123",
            timestamp="2025-01-21T00:00:00Z",
        )

        assert error.type == "test_error"
        assert error.message == "Test message"
        assert error.code == "TEST_ERROR"
        assert error.status == 400
        assert error.request_id == "test_123"
        assert error.timestamp == "2025-01-21T00:00:00Z"

    def test_full_error_detail(self):
        """Test fully populated error detail."""
        context = ErrorContext(requested_model="test")
        error = ErrorDetail(
            type="model_not_found",
            message="Model not found",
            detail="The requested model does not exist",
            code="MODEL_NOT_FOUND",
            status=404,
            request_id="test_123",
            timestamp="2025-01-21T00:00:00Z",
            suggestions=["Check /v1/models", "Verify spelling"],
            context=context,
            docs_url="https://docs.example.com/errors#model-not-found",
            support_url="https://support.example.com",
        )

        assert error.type == "model_not_found"
        assert error.detail == "The requested model does not exist"
        assert len(error.suggestions) == 2
        assert error.context == context
        assert error.docs_url == "https://docs.example.com/errors#model-not-found"
        assert error.support_url == "https://support.example.com"

    def test_missing_required_fields_raises_error(self):
        """Test that missing required fields raises ValidationError."""
        with pytest.raises(ValidationError):
            ErrorDetail(
                type="test_error",
                # Missing: message, code, status, request_id, timestamp
            )

    def test_suggestions_is_list(self):
        """Test that suggestions must be a list."""
        error = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
            suggestions=["suggestion1", "suggestion2"],
        )

        assert isinstance(error.suggestions, list)
        assert len(error.suggestions) == 2

    def test_status_is_integer(self):
        """Test that status must be an integer."""
        error = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=404,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
        )

        assert isinstance(error.status, int)
        assert error.status == 404

    def test_invalid_status_type_raises_error(self):
        """Test that invalid status type raises ValidationError."""
        with pytest.raises(ValidationError):
            ErrorDetail(
                type="test",
                message="test",
                code="TEST",
                status="404",  # String instead of int
                request_id="test",
                timestamp="2025-01-21T00:00:00Z",
            )

    def test_serialization_excludes_none(self):
        """Test that None values can be excluded from serialization."""
        error = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
        )

        data = error.dict(exclude_none=True)

        assert "type" in data
        assert "message" in data
        # Optional fields that are None should not be present
        assert "detail" not in data
        assert "suggestions" not in data
        assert "context" not in data

    def test_context_nested_serialization(self):
        """Test that nested context is properly serialized."""
        context = ErrorContext(requested_model="test", provider="test-provider")
        error = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
            context=context,
        )

        data = error.dict(exclude_none=True)

        assert "context" in data
        assert data["context"]["requested_model"] == "test"
        assert data["context"]["provider"] == "test-provider"


class TestErrorResponse:
    """Test suite for ErrorResponse schema."""

    def test_minimal_error_response(self):
        """Test creating minimal valid error response."""
        detail = ErrorDetail(
            type="test_error",
            message="Test message",
            code="TEST_ERROR",
            status=400,
            request_id="test_123",
            timestamp="2025-01-21T00:00:00Z",
        )
        error = ErrorResponse(error=detail)

        assert error.error == detail
        assert error.error.type == "test_error"

    def test_full_error_response(self):
        """Test fully populated error response."""
        context = ErrorContext(requested_model="test-model", provider="openrouter")
        detail = ErrorDetail(
            type="model_not_found",
            message="Model not found",
            detail="The requested model does not exist",
            code="MODEL_NOT_FOUND",
            status=404,
            request_id="test_123",
            timestamp="2025-01-21T00:00:00Z",
            suggestions=["Check available models", "Verify spelling"],
            context=context,
            docs_url="https://docs.example.com",
        )
        error = ErrorResponse(error=detail)

        assert error.error.type == "model_not_found"
        assert error.error.status == 404
        assert len(error.error.suggestions) == 2
        assert error.error.context.requested_model == "test-model"

    def test_missing_error_field_raises_error(self):
        """Test that missing error field raises ValidationError."""
        with pytest.raises(ValidationError):
            ErrorResponse()  # Missing required 'error' field

    def test_serialization_structure(self):
        """Test that serialization has correct structure."""
        detail = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
        )
        error = ErrorResponse(error=detail)

        data = error.dict(exclude_none=True)

        # Should have 'error' key at root
        assert "error" in data
        assert isinstance(data["error"], dict)

        # Error object should have required fields
        assert "type" in data["error"]
        assert "message" in data["error"]
        assert "code" in data["error"]
        assert "status" in data["error"]
        assert "request_id" in data["error"]
        assert "timestamp" in data["error"]

    def test_json_serialization(self):
        """Test that error response can be serialized to JSON."""
        detail = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
        )
        error = ErrorResponse(error=detail)

        # Should serialize to JSON without errors
        json_str = error.json(exclude_none=True)

        assert isinstance(json_str, str)
        assert "test" in json_str

    def test_nested_context_in_response(self):
        """Test that nested context is properly included."""
        context = ErrorContext(
            requested_model="gpt-5",
            suggested_models=["gpt-4", "gpt-4-turbo"],
            provider="openrouter",
        )
        detail = ErrorDetail(
            type="model_not_found",
            message="Model not found",
            code="MODEL_NOT_FOUND",
            status=404,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
            context=context,
        )
        error = ErrorResponse(error=detail)

        data = error.dict(exclude_none=True)

        assert "context" in data["error"]
        assert data["error"]["context"]["requested_model"] == "gpt-5"
        assert data["error"]["context"]["suggested_models"] == ["gpt-4", "gpt-4-turbo"]

    def test_example_model_not_found_response(self):
        """Test example model not found error response."""
        context = ErrorContext(
            requested_model="gpt-5",
            suggested_models=["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
            provider="openrouter",
        )
        detail = ErrorDetail(
            type="model_not_found",
            message="Model 'gpt-5' not found",
            detail="The requested model is not available in our catalog",
            code="MODEL_NOT_FOUND",
            status=404,
            request_id="req_abc123",
            timestamp="2025-01-21T10:30:00Z",
            suggestions=[
                "Check available models at /v1/models",
                "Try 'gpt-4' or 'gpt-4-turbo' instead",
                "Verify the model ID spelling",
            ],
            context=context,
            docs_url="https://docs.gatewayz.ai/errors#model-not-found",
        )
        response = ErrorResponse(error=detail)

        data = response.dict(exclude_none=True)

        # Validate complete structure
        assert data["error"]["type"] == "model_not_found"
        assert data["error"]["status"] == 404
        assert len(data["error"]["suggestions"]) == 3
        assert data["error"]["context"]["requested_model"] == "gpt-5"

    def test_example_insufficient_credits_response(self):
        """Test example insufficient credits error response."""
        context = ErrorContext(current_credits=0.50, required_credits=2.00, credit_deficit=1.50)
        detail = ErrorDetail(
            type="insufficient_credits",
            message="Insufficient credits. Required: $2.00, Current: $0.50",
            code="INSUFFICIENT_CREDITS",
            status=402,
            request_id="req_def456",
            timestamp="2025-01-21T10:35:00Z",
            suggestions=[
                "Add credits at https://gatewayz.ai/billing",
                "Upgrade your plan for higher limits",
                "Contact support for assistance",
            ],
            context=context,
            docs_url="https://docs.gatewayz.ai/errors#insufficient-credits",
            support_url="https://gatewayz.ai/support",
        )
        response = ErrorResponse(error=detail)

        data = response.dict(exclude_none=True)

        assert data["error"]["status"] == 402
        assert data["error"]["context"]["credit_deficit"] == 1.50
        assert "support_url" in data["error"]

    def test_example_rate_limit_response(self):
        """Test example rate limit error response."""
        context = ErrorContext(
            limit_type="requests_per_minute",
            limit_value=100,
            current_usage=105,
            retry_after=60,
        )
        detail = ErrorDetail(
            type="rate_limit_exceeded",
            message="Rate limit exceeded: 105/100 requests per minute",
            code="RATE_LIMIT_EXCEEDED",
            status=429,
            request_id="req_ghi789",
            timestamp="2025-01-21T10:40:00Z",
            suggestions=[
                "Wait 60 seconds before retrying",
                "Implement exponential backoff",
                "Upgrade plan for higher limits",
            ],
            context=context,
            docs_url="https://docs.gatewayz.ai/errors#rate-limit-exceeded",
        )
        response = ErrorResponse(error=detail)

        data = response.dict(exclude_none=True)

        assert data["error"]["status"] == 429
        assert data["error"]["context"]["retry_after"] == 60

    def test_timestamp_validation(self):
        """Test that timestamp format is validated."""
        detail = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",  # Valid ISO format
        )

        assert detail.timestamp == "2025-01-21T00:00:00Z"

    def test_urls_are_strings(self):
        """Test that URL fields accept strings."""
        detail = ErrorDetail(
            type="test",
            message="test",
            code="TEST",
            status=400,
            request_id="test",
            timestamp="2025-01-21T00:00:00Z",
            docs_url="https://docs.example.com",
            support_url="https://support.example.com",
        )

        assert isinstance(detail.docs_url, str)
        assert isinstance(detail.support_url, str)
