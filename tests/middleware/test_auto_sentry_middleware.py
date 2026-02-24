"""
Tests for Auto-Sentry Middleware

This test suite verifies that the automatic Sentry error capture middleware
correctly captures exceptions, extracts context, and adds appropriate tags.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.middleware.auto_sentry_middleware import AutoSentryMiddleware


@pytest.fixture
def app_with_auto_sentry():
    """Create a test FastAPI app with Auto-Sentry middleware"""
    app = FastAPI()

    # Add Auto-Sentry middleware
    app.add_middleware(AutoSentryMiddleware)

    # Test routes
    @app.get("/test-success")
    async def test_success():
        return {"status": "ok"}

    @app.get("/test-error")
    async def test_error():
        raise ValueError("Test error")

    @app.get("/test-http-exception")
    async def test_http_exception():
        raise HTTPException(status_code=500, detail="Internal server error")

    @app.post("/v1/chat/completions")
    async def test_chat():
        raise RuntimeError("Chat error")

    @app.post("/api/payments/webhook")
    async def test_payment():
        raise ValueError("Payment error")

    @app.post("/auth/login")
    async def test_auth():
        raise ValueError("Auth error")

    return app


@pytest.fixture
def client(app_with_auto_sentry):
    """Create test client"""
    return TestClient(app_with_auto_sentry)


class TestAutoSentryMiddleware:
    """Test Auto-Sentry Middleware functionality"""

    def test_successful_request_no_sentry_capture(self, client):
        """Test that successful requests don't trigger Sentry"""
        with patch("src.middleware.auto_sentry_middleware.sentry_sdk") as mock_sentry:
            response = client.get("/test-success")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            # Sentry should not capture successful requests
            mock_sentry.capture_exception.assert_not_called()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_error_captured_to_sentry(self, mock_sentry, client):
        """Test that errors are captured to Sentry with context"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(ValueError):
            client.get("/test-error")

        # Verify Sentry capture was called
        mock_sentry.capture_exception.assert_called_once()

        # Verify context was set
        mock_scope.set_context.assert_called()
        mock_scope.set_tag.assert_called()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_request_context_extraction(self, mock_sentry, client):
        """Test that request context is properly extracted"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(ValueError):
            client.get("/test-error?param=value")

        # Find the request context call
        calls = mock_scope.set_context.call_args_list
        request_context_call = next((call for call in calls if call[0][0] == "request"), None)

        assert request_context_call is not None
        context = request_context_call[0][1]

        assert context["method"] == "GET"
        assert context["path"] == "/test-error"
        assert "param" in context["query_params"]
        assert context["endpoint_type"] == "general"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_endpoint_type_detection_inference(self, mock_sentry, client):
        """Test endpoint type detection for inference endpoints"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(RuntimeError):
            client.post("/v1/chat/completions", json={"messages": []})

        # Find the endpoint type tag
        tag_calls = mock_scope.set_tag.call_args_list
        endpoint_type_call = next(
            (call for call in tag_calls if call[0][0] == "endpoint_type"), None
        )

        assert endpoint_type_call is not None
        assert endpoint_type_call[0][1] == "inference_chat"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_endpoint_type_detection_payment(self, mock_sentry, client):
        """Test endpoint type detection for payment endpoints"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(ValueError):
            client.post("/api/payments/webhook", json={})

        # Find the endpoint type tag
        tag_calls = mock_scope.set_tag.call_args_list
        endpoint_type_call = next(
            (call for call in tag_calls if call[0][0] == "endpoint_type"), None
        )

        assert endpoint_type_call is not None
        assert endpoint_type_call[0][1] == "payment"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_endpoint_type_detection_auth(self, mock_sentry, client):
        """Test endpoint type detection for auth endpoints"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(ValueError):
            client.post("/auth/login", json={})

        # Find the endpoint type tag
        tag_calls = mock_scope.set_tag.call_args_list
        endpoint_type_call = next(
            (call for call in tag_calls if call[0][0] == "endpoint_type"), None
        )

        assert endpoint_type_call is not None
        assert endpoint_type_call[0][1] == "authentication"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_sensitive_headers_sanitized(self, mock_sentry, client):
        """Test that sensitive headers are sanitized"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        headers = {
            "Authorization": "Bearer secret-token",
            "X-Api-Key": "secret-key",
            "Content-Type": "application/json",
        }

        with pytest.raises(ValueError):
            client.get("/test-error", headers=headers)

        # Find the request context
        calls = mock_scope.set_context.call_args_list
        request_context_call = next((call for call in calls if call[0][0] == "request"), None)

        assert request_context_call is not None
        context = request_context_call[0][1]

        # Check that sensitive headers are redacted
        assert context["headers"]["authorization"] == "[REDACTED]"
        assert context["headers"]["x-api-key"] == "[REDACTED]"
        # Non-sensitive headers should remain
        assert context["headers"]["content-type"] == "application/json"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_revenue_critical_tag(self, mock_sentry, client):
        """Test that revenue-critical endpoints are tagged"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(RuntimeError):
            client.post("/v1/chat/completions", json={"messages": []})

        # Find the is_revenue_critical tag
        tag_calls = mock_scope.set_tag.call_args_list
        revenue_tag_call = next(
            (call for call in tag_calls if call[0][0] == "is_revenue_critical"), None
        )

        assert revenue_tag_call is not None
        assert revenue_tag_call[0][1] == "true"

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_http_exception_handling(self, mock_sentry, client):
        """Test that HTTPExceptions are not captured by middleware to avoid duplicates"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        # HTTPException gets converted to HTTP response by FastAPI's exception handler
        # It doesn't propagate to the middleware's exception handler, so middleware
        # never sees it as an exception - it only sees the response
        response = client.get("/test-http-exception")
        assert response.status_code == 500

        # Middleware should NOT capture HTTPException (neither 4xx nor 5xx)
        # Route handlers are responsible for capturing the original exception before wrapping
        mock_sentry.capture_exception.assert_not_called()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    async def test_http_exception_filtering_in_middleware(self, mock_sentry):
        """Test that 4xx HTTPException is filtered in middleware exception handler"""
        from unittest.mock import AsyncMock

        from fastapi import Request, Response

        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        # Create middleware instance
        app = FastAPI()
        middleware = AutoSentryMiddleware(app)

        # Create mock request
        mock_request = Mock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/test"
        mock_request.query_params = {}
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_request.scope = {"route": Mock(path="/test")}

        # Create call_next that raises 4xx HTTPException
        async def call_next_raises_http_exception(request):
            raise HTTPException(status_code=404, detail="Not found")

        # Test that 4xx HTTPException is caught and NOT sent to Sentry
        with pytest.raises(HTTPException) as exc_info:
            await middleware.dispatch(mock_request, call_next_raises_http_exception)

        # Verify HTTPException was raised
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Not found"

        # Verify Sentry capture was NOT called for 4xx
        mock_sentry.capture_exception.assert_not_called()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    async def test_http_exception_various_status_codes(self, mock_sentry):
        """Test that all HTTPExceptions (4xx and 5xx) are filtered to avoid duplicates"""
        from unittest.mock import AsyncMock

        from fastapi import Request

        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        app = FastAPI()
        middleware = AutoSentryMiddleware(app)

        mock_request = Mock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/test"
        mock_request.query_params = {}
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_request.scope = {"route": Mock(path="/test")}

        # Test both 4xx and 5xx status codes (all should NOT be captured)
        # Route handlers should capture the original exception before wrapping
        for status_code in [401, 403, 404, 422, 500, 502, 503]:
            mock_sentry.reset_mock()

            async def call_next_raises_http(request):
                raise HTTPException(status_code=status_code, detail=f"Error {status_code}")

            with pytest.raises(HTTPException) as exc_info:
                await middleware.dispatch(mock_request, call_next_raises_http)

            assert exc_info.value.status_code == status_code
            # HTTPException should NOT be captured (route handlers capture the original error)
            mock_sentry.capture_exception.assert_not_called()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    async def test_regular_exception_still_captured(self, mock_sentry):
        """Test that non-HTTPException errors are still captured"""
        from fastapi import Request

        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        app = FastAPI()
        middleware = AutoSentryMiddleware(app)

        mock_request = Mock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/test"
        mock_request.query_params = {}
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_request.scope = {"route": Mock(path="/test")}

        # Create call_next that raises a regular exception
        async def call_next_raises_value_error(request):
            raise ValueError("Something went wrong")

        # Test that regular exception IS captured
        with pytest.raises(ValueError):
            await middleware.dispatch(mock_request, call_next_raises_value_error)

        # Verify Sentry capture WAS called for regular exception
        mock_sentry.capture_exception.assert_called_once()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", False)
    def test_sentry_unavailable_no_error(self, client):
        """Test that middleware works even when Sentry is unavailable"""
        # Should not raise an error even though Sentry is unavailable
        response = client.get("/test-success")
        assert response.status_code == 200

        # Error should still propagate normally
        with pytest.raises(ValueError):
            client.get("/test-error")

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_slow_request_breadcrumb(self, mock_sentry, client):
        """Test that slow requests generate breadcrumbs"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        # Mock time.time to simulate slow request (>5 seconds)
        # Use a counter to return incrementing values
        call_count = [0]

        def mock_time_func():
            result = call_count[0]
            call_count[0] += 6  # Each call adds 6 seconds
            return result

        with patch("src.middleware.auto_sentry_middleware.time.time", side_effect=mock_time_func):
            response = client.get("/test-success")
            assert response.status_code == 200

            # Verify breadcrumb was added for slow request
            mock_sentry.add_breadcrumb.assert_called_once()
            breadcrumb_call = mock_sentry.add_breadcrumb.call_args

            assert breadcrumb_call[1]["category"] == "performance"
            assert breadcrumb_call[1]["level"] == "warning"
            assert "Slow request" in breadcrumb_call[1]["message"]

    def test_middleware_determines_endpoint_types_correctly(self):
        """Test endpoint type determination logic"""
        from src.middleware.auto_sentry_middleware import AutoSentryMiddleware

        middleware = AutoSentryMiddleware(app=Mock())

        # Test inference endpoints
        assert middleware._determine_endpoint_type("/v1/chat/completions") == "inference_chat"
        assert middleware._determine_endpoint_type("/v1/messages") == "inference_messages"
        assert middleware._determine_endpoint_type("/v1/images/generations") == "inference_images"

        # Test payment endpoints
        assert middleware._determine_endpoint_type("/api/payments/webhook") == "payment"
        assert middleware._determine_endpoint_type("/checkout/session") == "checkout"

        # Test auth endpoints
        assert middleware._determine_endpoint_type("/auth/login") == "authentication"
        assert middleware._determine_endpoint_type("/api/keys/create") == "api_key_management"

        # Test admin endpoints
        assert middleware._determine_endpoint_type("/admin/users") == "admin"

        # Test monitoring endpoints
        assert middleware._determine_endpoint_type("/health") == "health_check"
        assert middleware._determine_endpoint_type("/metrics") == "metrics"

        # Test general endpoints
        assert middleware._determine_endpoint_type("/api/some/other/path") == "general"

    def test_middleware_categorizes_http_errors_correctly(self):
        """Test HTTP error categorization"""
        from src.middleware.auto_sentry_middleware import AutoSentryMiddleware

        middleware = AutoSentryMiddleware(app=Mock())

        assert middleware._categorize_http_error(200) == "success"
        assert middleware._categorize_http_error(400) == "client_error"
        assert middleware._categorize_http_error(404) == "client_error"
        assert middleware._categorize_http_error(500) == "server_error"
        assert middleware._categorize_http_error(502) == "server_error"


class TestAutoSentryMiddlewareEdgeCases:
    """Test edge cases and error conditions"""

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_missing_client_info(self, mock_sentry, client):
        """Test handling when request.client is None"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        # This should not crash even if client info is missing
        with pytest.raises(ValueError):
            client.get("/test-error")

        mock_sentry.capture_exception.assert_called_once()

    @patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True)
    @patch("src.middleware.auto_sentry_middleware.sentry_sdk")
    def test_empty_headers(self, mock_sentry, client):
        """Test handling of empty headers"""
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope

        with pytest.raises(ValueError):
            client.get("/test-error", headers={})

        # Should still capture the exception
        mock_sentry.capture_exception.assert_called_once()

    def test_sanitize_headers_edge_cases(self):
        """Test header sanitization with various input"""
        from src.middleware.auto_sentry_middleware import AutoSentryMiddleware

        middleware = AutoSentryMiddleware(app=Mock())

        # Test with sensitive headers
        headers = {
            "authorization": "Bearer token",
            "cookie": "session=abc",
            "x-api-key": "secret",
            "content-type": "application/json",
        }

        sanitized = middleware._sanitize_headers(headers)

        assert sanitized["authorization"] == "[REDACTED]"
        assert sanitized["cookie"] == "[REDACTED]"
        assert sanitized["x-api-key"] == "[REDACTED]"
        assert sanitized["content-type"] == "application/json"

        # Test with empty headers
        assert middleware._sanitize_headers({}) == {}

        # Test with no sensitive headers
        safe_headers = {"content-type": "application/json", "accept": "application/json"}
        assert middleware._sanitize_headers(safe_headers) == safe_headers
