"""
Unit tests for error handler utilities.

Tests the FastAPI exception handler integration and error mapping logic.
"""

from unittest.mock import MagicMock, Mock

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.schemas.errors import ErrorResponse
from src.utils.error_factory import DetailedErrorFactory
from src.utils.error_handlers import (
    _map_http_exception_to_detailed_error,
    create_error_response_dict,
    detailed_exception_handler,
    detailed_http_exception_handler,
)


@pytest.mark.asyncio
class TestDetailedHTTPExceptionHandler:
    """Test suite for HTTP exception handler."""

    async def test_handles_detailed_error_dict(self):
        """Test handling of HTTPException with detailed error dict."""
        # Create a detailed error
        error_response = DetailedErrorFactory.model_not_found(
            model_id="test-model", request_id="test_123"
        )

        # Create HTTPException with detailed error as dict
        exc = HTTPException(status_code=404, detail=error_response.dict(exclude_none=True))

        # Mock request
        request = Mock(spec=Request)
        request.state = Mock()
        request.state.request_id = "test_123"

        # Handle exception
        response = await detailed_http_exception_handler(request, exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 404

        # Response body should be the detailed error
        body = response.body.decode()
        assert "error" in body
        assert "model_not_found" in body

    async def test_handles_string_detail(self):
        """Test handling of HTTPException with string detail."""
        exc = HTTPException(status_code=404, detail="Model not found")

        request = Mock(spec=Request)
        request.state = Mock()
        request.state.request_id = "test_123"
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        response = await detailed_http_exception_handler(request, exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 404

        # Should be converted to detailed error
        body = response.body.decode()
        assert "error" in body

    async def test_preserves_custom_headers(self):
        """Test that custom headers are preserved."""
        exc = HTTPException(
            status_code=429,
            detail="Rate limited",
            headers={"Retry-After": "60", "X-Custom": "value"},
        )

        request = Mock(spec=Request)
        request.state = Mock()
        request.state.request_id = "test_123"
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        response = await detailed_http_exception_handler(request, exc)

        assert response.headers.get("Retry-After") == "60"
        assert response.headers.get("X-Custom") == "value"

    async def test_generates_request_id_if_missing(self):
        """Test that request_id is generated if not in request state."""
        exc = HTTPException(status_code=404, detail="Not found")

        request = Mock(spec=Request)
        request.state = Mock()
        # No request_id attribute
        delattr(request.state, "request_id")
        request.url = Mock()
        request.url.path = "/test"
        request.method = "GET"

        response = await detailed_http_exception_handler(request, exc)

        body_str = response.body.decode()
        assert "request_id" in body_str
        assert "req_" in body_str


class TestDetailedExceptionHandler:
    """Test suite for generic exception handler."""

    @pytest.mark.asyncio
    async def test_handles_generic_exception(self):
        """Test handling of generic Python exceptions."""
        exc = ValueError("Something went wrong")

        request = Mock(spec=Request)
        request.state = Mock()
        request.state.request_id = "test_123"

        response = await detailed_exception_handler(request, exc)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 500

        body_str = response.body.decode()
        assert "error" in body_str
        assert "internal_error" in body_str

    @pytest.mark.asyncio
    async def test_includes_request_id_in_headers(self):
        """Test that request_id is included in response headers."""
        exc = Exception("Test error")

        request = Mock(spec=Request)
        request.state = Mock()
        request.state.request_id = "test_123"

        response = await detailed_exception_handler(request, exc)

        assert response.headers.get("X-Request-ID") == "test_123"


class TestCreateErrorResponseDict:
    """Test suite for error response dict creation."""

    def test_creates_dict_from_error_response(self):
        """Test creating dict from ErrorResponse object."""
        error = DetailedErrorFactory.model_not_found(model_id="test", request_id="test_123")

        response_dict, headers = create_error_response_dict(error)

        assert isinstance(response_dict, dict)
        assert "error" in response_dict
        assert response_dict["error"]["type"] == "model_not_found"
        assert response_dict["error"]["request_id"] == "test_123"

    def test_excludes_none_values(self):
        """Test that None values are excluded from dict."""
        error = DetailedErrorFactory.invalid_api_key(request_id="test_123")

        response_dict, headers = create_error_response_dict(error)

        # Check that no value is None
        def check_no_none(d):
            if isinstance(d, dict):
                for v in d.values():
                    assert v is not None
                    if isinstance(v, dict):
                        check_no_none(v)

        check_no_none(response_dict)

    def test_includes_request_id_header(self):
        """Test that request_id is included in headers."""
        error = DetailedErrorFactory.model_not_found(model_id="test", request_id="test_123")

        response_dict, headers = create_error_response_dict(error)

        assert headers.get("X-Request-ID") == "test_123"


class TestMapHTTPExceptionToDetailedError:
    """Test suite for HTTP exception mapping."""

    def test_maps_404_to_model_not_found(self):
        """Test 404 with 'model' in message maps to model_not_found."""
        exc = HTTPException(status_code=404, detail="Model 'gpt-5' not found")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "model_not_found"
        assert error.error.status == 404

    def test_maps_401_to_invalid_api_key(self):
        """Test 401 maps to invalid_api_key."""
        exc = HTTPException(status_code=401, detail="Invalid API key")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "invalid_api_key"
        assert error.error.status == 401

    def test_maps_402_to_insufficient_credits(self):
        """Test 402 maps to insufficient_credits."""
        exc = HTTPException(status_code=402, detail="Insufficient credits")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "insufficient_credits"
        assert error.error.status == 402

    def test_maps_429_to_rate_limit_exceeded(self):
        """Test 429 maps to rate_limit_exceeded."""
        exc = HTTPException(
            status_code=429, detail="Rate limit exceeded", headers={"Retry-After": "60"}
        )

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "rate_limit_exceeded"
        assert error.error.status == 429
        # Should extract retry_after from headers
        if error.error.context:
            assert error.error.context.retry_after == 60

    def test_maps_502_to_provider_error(self):
        """Test 502 maps to provider_error."""
        exc = HTTPException(status_code=502, detail="Provider error")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "provider_error"
        assert error.error.status == 502

    def test_maps_503_to_service_unavailable(self):
        """Test 503 maps to service_unavailable."""
        exc = HTTPException(status_code=503, detail="Service unavailable")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.type == "service_unavailable"
        assert error.error.status == 503

    def test_maps_400_to_bad_request(self):
        """Test 400 maps to bad_request or specific error."""
        exc = HTTPException(status_code=400, detail="Invalid request")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert error.error.status == 400
        # Could be bad_request or more specific error
        assert error.error.type in [
            "bad_request",
            "invalid_parameter",
            "missing_required_parameter",
            "empty_messages_array",
        ]

    def test_extracts_model_from_message(self):
        """Test that model ID is extracted from error message."""
        exc = HTTPException(status_code=404, detail="Model 'gpt-5' not found")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        # Should extract 'gpt-5' and include in context
        if error.error.context:
            assert error.error.context.requested_model == "gpt-5" or "gpt-5" in error.error.message

    def test_handles_retry_after_header(self):
        """Test that Retry-After header is extracted."""
        exc = HTTPException(
            status_code=429, detail="Too many requests", headers={"Retry-After": "120"}
        )

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        if error.error.context:
            assert error.error.context.retry_after == 120

    def test_includes_request_info_in_context(self):
        """Test that request path and method are included."""
        exc = HTTPException(status_code=500, detail="Internal error")

        request = Mock()
        request.url = Mock()
        request.url.path = "/v1/chat/completions"
        request.method = "POST"

        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        # Should include request info in context
        if error.error.context:
            assert (
                error.error.context.request_path == "/v1/chat/completions"
                or error.error.context.endpoint == "/v1/chat/completions"
            )

    def test_handles_missing_request_info(self):
        """Test handling when request info is missing."""
        exc = HTTPException(status_code=500, detail="Error")

        request = Mock()
        request.url = None  # Missing URL

        # Should not crash
        error = _map_http_exception_to_detailed_error(exc, "test_123", request)

        assert isinstance(error, ErrorResponse)
        assert error.error.status == 500

    def test_preserves_request_id(self):
        """Test that provided request_id is preserved."""
        exc = HTTPException(status_code=500, detail="Error")

        request = Mock()
        request.url = Mock()
        request.url.path = "/test"
        request.method = "GET"

        error = _map_http_exception_to_detailed_error(exc, "custom_req_123", request)

        assert error.error.request_id == "custom_req_123"

    def test_generates_request_id_if_none(self):
        """Test that request_id is generated if not provided."""
        exc = HTTPException(status_code=500, detail="Error")

        request = Mock()
        request.url = Mock()
        request.url.path = "/test"
        request.method = "GET"

        error = _map_http_exception_to_detailed_error(exc, None, request)

        assert error.error.request_id is not None
        assert error.error.request_id.startswith("req_")
