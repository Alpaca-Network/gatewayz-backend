"""
Unit tests for DetailedErrorFactory.

Tests all factory methods to ensure they create properly structured
detailed error responses with correct codes, messages, and context.
"""

from datetime import datetime

import pytest

from src.schemas.errors import ErrorResponse
from src.utils.error_codes import ErrorCode
from src.utils.error_factory import DetailedErrorFactory


class TestDetailedErrorFactory:
    """Test suite for detailed error factory."""

    def test_model_not_found_basic(self):
        """Test basic model not found error."""
        error = DetailedErrorFactory.model_not_found(
            model_id="invalid-model", request_id="test_123"
        )

        assert isinstance(error, ErrorResponse)
        assert error.error.type == "model_not_found"
        assert error.error.code == ErrorCode.MODEL_NOT_FOUND
        assert error.error.status == 404
        assert "invalid-model" in error.error.message
        assert error.error.request_id == "test_123"
        assert error.error.suggestions is not None
        assert len(error.error.suggestions) > 0
        assert error.error.docs_url is not None

    def test_model_not_found_with_suggestions(self):
        """Test model not found with similar model suggestions."""
        error = DetailedErrorFactory.model_not_found(
            model_id="gpt-5",
            suggested_models=["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
            request_id="test_123",
        )

        assert error.error.context.suggested_models == [
            "gpt-4",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ]
        assert any("gpt-4" in s for s in error.error.suggestions)

    def test_model_not_found_with_provider(self):
        """Test model not found includes provider context."""
        error = DetailedErrorFactory.model_not_found(
            model_id="invalid-model", provider="openrouter", request_id="test_123"
        )

        assert error.error.context.provider == "openrouter"
        assert (
            "openrouter" in error.error.message.lower()
            or error.error.context.provider == "openrouter"
        )

    def test_insufficient_credits(self):
        """Test insufficient credits error."""
        error = DetailedErrorFactory.insufficient_credits(
            current_credits=0.5, required_credits=2.0, request_id="test_123"
        )

        assert error.error.type == "insufficient_credits"
        assert error.error.code == ErrorCode.INSUFFICIENT_CREDITS
        assert error.error.status == 402
        assert error.error.context.current_credits == 0.5
        assert error.error.context.required_credits == 2.0
        # Check deficit is calculated
        assert "$1.5" in error.error.message or "$1.50" in error.error.message
        assert error.error.support_url is not None
        assert error.error.suggestions is not None

    def test_invalid_api_key_basic(self):
        """Test invalid API key error."""
        error = DetailedErrorFactory.invalid_api_key(request_id="test_123")

        assert error.error.type == "invalid_api_key"
        assert error.error.code == ErrorCode.INVALID_API_KEY
        assert error.error.status == 401
        assert error.error.request_id == "test_123"
        assert error.error.suggestions is not None
        assert len(error.error.suggestions) > 0

    def test_invalid_api_key_with_reason(self):
        """Test invalid API key with specific reason."""
        error = DetailedErrorFactory.invalid_api_key(
            reason="expired", key_prefix="gw_live_abc", request_id="test_123"
        )

        assert (
            "expired" in error.error.message.lower()
            or error.error.detail
            and "expired" in error.error.detail.lower()
        )
        assert error.error.context.api_key_prefix == "gw_live_abc"

    def test_rate_limit_exceeded_basic(self):
        """Test rate limit exceeded error."""
        error = DetailedErrorFactory.rate_limit_exceeded(
            limit_type="requests_per_minute", retry_after=60, request_id="test_123"
        )

        assert error.error.type == "rate_limit_exceeded"
        assert error.error.code == ErrorCode.RATE_LIMIT_EXCEEDED
        assert error.error.status == 429
        assert error.error.context.retry_after == 60
        assert "60" in str(error.error.suggestions) or error.error.context.retry_after == 60

    def test_rate_limit_exceeded_with_usage(self):
        """Test rate limit with usage information."""
        error = DetailedErrorFactory.rate_limit_exceeded(
            limit_type="tokens_per_minute",
            limit_value=100000,
            current_usage=105000,
            retry_after=30,
            request_id="test_123",
        )

        assert error.error.context.limit_value == 100000
        assert error.error.context.current_usage == 105000
        assert "100000" in error.error.message or "100,000" in error.error.message

    def test_invalid_parameter_basic(self):
        """Test invalid parameter error."""
        error = DetailedErrorFactory.invalid_parameter(
            parameter_name="temperature",
            parameter_value=5.0,
            min_value=0.0,
            max_value=2.0,
            request_id="test_123",
        )

        assert error.error.type == "invalid_parameter"
        assert error.error.status == 400
        assert "temperature" in error.error.message
        assert error.error.context.parameter_name == "temperature"
        assert error.error.context.parameter_value == 5.0
        assert error.error.context.min_value == 0.0
        assert error.error.context.max_value == 2.0

    def test_invalid_parameter_with_allowed_values(self):
        """Test invalid parameter with allowed values."""
        error = DetailedErrorFactory.invalid_parameter(
            parameter_name="format",
            parameter_value="xml",
            allowed_values=["json", "text"],
            request_id="test_123",
        )

        assert error.error.context.allowed_values == ["json", "text"]
        assert "json" in str(error.error.suggestions)

    def test_invalid_parameter_type_mismatch(self):
        """Test invalid parameter with type mismatch."""
        error = DetailedErrorFactory.invalid_parameter(
            parameter_name="max_tokens",
            parameter_value="not_a_number",
            expected_type="integer",
            request_id="test_123",
        )

        assert error.error.context.expected_type == "integer"
        assert "integer" in error.error.message.lower() or "int" in error.error.message.lower()

    def test_provider_error_basic(self):
        """Test provider error."""
        error = DetailedErrorFactory.provider_error(
            provider="openrouter",
            model="gpt-4",
            provider_message="Connection timeout",
            request_id="test_123",
        )

        assert error.error.type == "provider_error"
        assert error.error.status in [502, 503, 504]
        assert "openrouter" in error.error.message.lower()
        assert "gpt-4" in error.error.message
        assert error.error.context.provider == "openrouter"
        assert error.error.context.requested_model == "gpt-4"
        assert error.error.context.provider_message == "Connection timeout"

    def test_provider_error_custom_status(self):
        """Test provider error with custom status code."""
        error = DetailedErrorFactory.provider_error(
            provider="openrouter",
            model="gpt-4",
            status_code=503,
            request_id="test_123",
        )

        assert error.error.status == 503

    def test_internal_error_basic(self):
        """Test internal error."""
        error = DetailedErrorFactory.internal_error(operation="user_lookup", request_id="test_123")

        assert error.error.type == "internal_error"
        assert error.error.code == ErrorCode.INTERNAL_ERROR
        assert error.error.status == 500
        assert "user_lookup" in error.error.message

    def test_internal_error_with_exception(self):
        """Test internal error with exception details."""
        test_exception = ValueError("Something went wrong")
        error = DetailedErrorFactory.internal_error(
            operation="database_query", error=test_exception, request_id="test_123"
        )

        assert "database_query" in error.error.message
        assert error.error.context.error_type == "ValueError"
        assert error.error.context.error_message == "Something went wrong"

    def test_missing_required_parameter(self):
        """Test missing required parameter error."""
        error = DetailedErrorFactory.missing_required_parameter(
            parameter_name="messages", request_id="test_123"
        )

        assert error.error.type == "missing_required_parameter"
        assert error.error.status == 400
        assert "messages" in error.error.message
        assert error.error.context.parameter_name == "messages"

    def test_empty_messages_array(self):
        """Test empty messages array error."""
        error = DetailedErrorFactory.empty_messages_array(request_id="test_123")

        assert error.error.type == "empty_messages_array"
        assert error.error.status == 400
        assert "messages" in error.error.message.lower()

    def test_invalid_message_format(self):
        """Test invalid message format error."""
        error = DetailedErrorFactory.invalid_message_format(
            message_index=0,
            issue="Missing 'role' field",
            invalid_message={"content": "test"},
            request_id="test_123",
        )

        assert error.error.type == "invalid_message_format"
        assert error.error.status == 400
        assert "role" in error.error.message.lower()
        assert error.error.context.message_index == 0

    def test_provider_timeout(self):
        """Test provider timeout error."""
        error = DetailedErrorFactory.provider_timeout(
            provider="openrouter", model="gpt-4", timeout=30, request_id="test_123"
        )

        assert error.error.type == "provider_timeout"
        assert error.error.status == 504
        assert "timeout" in error.error.message.lower()
        assert error.error.context.timeout == 30

    def test_provider_unavailable(self):
        """Test provider unavailable error."""
        error = DetailedErrorFactory.provider_unavailable(
            provider="openrouter", retry_after=300, request_id="test_123"
        )

        assert error.error.type == "provider_unavailable"
        assert error.error.status == 503
        assert error.error.context.retry_after == 300

    def test_all_providers_failed(self):
        """Test all providers failed error."""
        providers_tried = ["openrouter", "portkey", "together"]
        error = DetailedErrorFactory.all_providers_failed(
            model="gpt-4", providers_tried=providers_tried, request_id="test_123"
        )

        assert error.error.type == "all_providers_failed"
        assert error.error.status == 503
        assert error.error.context.providers_tried == providers_tried

    def test_error_json_serialization(self):
        """Test error response can be serialized to JSON."""
        error = DetailedErrorFactory.model_not_found(model_id="test-model", request_id="test_123")

        # Should not raise exception
        json_dict = error.dict(exclude_none=True)

        assert "error" in json_dict
        assert "type" in json_dict["error"]
        assert "message" in json_dict["error"]
        assert "request_id" in json_dict["error"]
        assert "code" in json_dict["error"]
        assert "status" in json_dict["error"]

    def test_error_excludes_none_values(self):
        """Test that None values are excluded from serialization."""
        error = DetailedErrorFactory.model_not_found(model_id="test-model", request_id="test_123")

        json_dict = error.dict(exclude_none=True)

        # Check that no value is None
        def check_no_none(d):
            if isinstance(d, dict):
                for v in d.values():
                    assert v is not None
                    if isinstance(v, dict):
                        check_no_none(v)

        check_no_none(json_dict)

    def test_request_id_generation(self):
        """Test that request_id is auto-generated if not provided."""
        error = DetailedErrorFactory.model_not_found(model_id="test-model")

        assert error.error.request_id is not None
        assert error.error.request_id.startswith("req_")

    def test_timestamp_format(self):
        """Test that timestamp is in correct ISO format."""
        error = DetailedErrorFactory.model_not_found(model_id="test-model", request_id="test_123")

        # Should be valid ISO format timestamp
        timestamp = error.error.timestamp
        assert timestamp.endswith("Z")
        # Should be parseable
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

    def test_docs_url_present(self):
        """Test that docs_url is present for appropriate errors."""
        errors_with_docs = [
            DetailedErrorFactory.model_not_found("test", request_id="test"),
            DetailedErrorFactory.invalid_api_key(request_id="test"),
            DetailedErrorFactory.insufficient_credits(1.0, 2.0, request_id="test"),
            DetailedErrorFactory.rate_limit_exceeded("test", request_id="test"),
        ]

        for error in errors_with_docs:
            assert error.error.docs_url is not None
            assert error.error.docs_url.startswith("http")

    def test_support_url_for_payment_errors(self):
        """Test that support_url is present for payment errors."""
        error = DetailedErrorFactory.insufficient_credits(1.0, 2.0, request_id="test_123")

        assert error.error.support_url is not None
        assert (
            "support" in error.error.support_url.lower()
            or "contact" in error.error.support_url.lower()
        )

    def test_suggestions_always_list(self):
        """Test that suggestions are always a list when present."""
        error = DetailedErrorFactory.model_not_found(model_id="test", request_id="test_123")

        assert isinstance(error.error.suggestions, list)
        assert len(error.error.suggestions) > 0
        assert all(isinstance(s, str) for s in error.error.suggestions)

    def test_context_properly_typed(self):
        """Test that context object is properly typed."""
        error = DetailedErrorFactory.model_not_found(
            model_id="test-model",
            provider="openrouter",
            suggested_models=["gpt-4"],
            request_id="test_123",
        )

        context = error.error.context
        assert context.requested_model == "test-model"
        assert context.provider == "openrouter"
        assert context.suggested_models == ["gpt-4"]

    def test_error_codes_match_types(self):
        """Test that error codes match error types."""
        test_cases = [
            (
                DetailedErrorFactory.model_not_found("test", request_id="test"),
                "MODEL_NOT_FOUND",
                "model_not_found",
            ),
            (
                DetailedErrorFactory.invalid_api_key(request_id="test"),
                "INVALID_API_KEY",
                "invalid_api_key",
            ),
            (
                DetailedErrorFactory.insufficient_credits(1.0, 2.0, request_id="test"),
                "INSUFFICIENT_CREDITS",
                "insufficient_credits",
            ),
            (
                DetailedErrorFactory.rate_limit_exceeded("test", request_id="test"),
                "RATE_LIMIT_EXCEEDED",
                "rate_limit_exceeded",
            ),
        ]

        for error, expected_code, expected_type in test_cases:
            assert error.error.code == expected_code
            assert error.error.type == expected_type

    def test_http_status_codes_correct(self):
        """Test that HTTP status codes are correct for each error type."""
        test_cases = [
            (DetailedErrorFactory.model_not_found("test", request_id="test"), 404),
            (DetailedErrorFactory.invalid_api_key(request_id="test"), 401),
            (
                DetailedErrorFactory.insufficient_credits(1.0, 2.0, request_id="test"),
                402,
            ),
            (
                DetailedErrorFactory.rate_limit_exceeded("test", request_id="test"),
                429,
            ),
            (
                DetailedErrorFactory.invalid_parameter("test", "value", request_id="test"),
                400,
            ),
            (
                DetailedErrorFactory.provider_error("test", "model", request_id="test"),
                502,
            ),
            (DetailedErrorFactory.internal_error("test", request_id="test"), 500),
        ]

        for error, expected_status in test_cases:
            assert error.error.status == expected_status
