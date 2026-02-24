"""
Comprehensive tests for Sentry error context utilities.

Tests the helper functions and decorators for adding structured context
to errors captured by Sentry across the application.
"""

import asyncio
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.utils.sentry_context import (
    SENTRY_AVAILABLE,
    capture_auth_error,
    capture_cache_error,
    capture_database_error,
    capture_error,
    capture_model_health_error,
    capture_payment_error,
    capture_provider_error,
    set_error_context,
    set_error_tag,
    with_sentry_context,
)


class TestSetErrorContext:
    """Test set_error_context function"""

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_context")
    def test_set_error_context_success(self, mock_set_context):
        """Test setting error context successfully"""
        context_data = {
            "provider_name": "openrouter",
            "endpoint": "/api/chat/completions",
            "request_id": "req-123",
        }

        set_error_context("provider", context_data)

        mock_set_context.assert_called_once_with("provider", context_data)

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", False)
    @patch("src.utils.sentry_context.set_context")
    def test_set_error_context_sentry_unavailable(self, mock_set_context):
        """Test set_error_context when Sentry is unavailable"""
        set_error_context("provider", {"key": "value"})

        mock_set_context.assert_not_called()

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_context", side_effect=Exception("Sentry error"))
    def test_set_error_context_handles_exception(self, mock_set_context, caplog):
        """Test that exceptions are handled gracefully"""
        import logging

        with caplog.at_level(logging.WARNING):
            set_error_context("provider", {"key": "value"})

            assert any(
                "Failed to set Sentry context" in record.message for record in caplog.records
            )


class TestSetErrorTag:
    """Test set_error_tag function"""

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_tag")
    def test_set_error_tag_with_string(self, mock_set_tag):
        """Test setting error tag with string value"""
        set_error_tag("provider", "openrouter")

        mock_set_tag.assert_called_once_with("provider", "openrouter")

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_tag")
    def test_set_error_tag_with_int(self, mock_set_tag):
        """Test setting error tag with int value"""
        set_error_tag("status_code", 500)

        mock_set_tag.assert_called_once_with("status_code", "500")

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_tag")
    def test_set_error_tag_with_bool(self, mock_set_tag):
        """Test setting error tag with bool value"""
        set_error_tag("is_critical", True)

        mock_set_tag.assert_called_once_with("is_critical", "True")

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", False)
    @patch("src.utils.sentry_context.set_tag")
    def test_set_error_tag_sentry_unavailable(self, mock_set_tag):
        """Test set_error_tag when Sentry is unavailable"""
        set_error_tag("provider", "openrouter")

        mock_set_tag.assert_not_called()

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_tag", side_effect=Exception("Tag error"))
    def test_set_error_tag_handles_exception(self, mock_set_tag, caplog):
        """Test that exceptions are handled gracefully"""
        import logging

        with caplog.at_level(logging.WARNING):
            set_error_tag("provider", "openrouter")

            assert any("Failed to set Sentry tag" in record.message for record in caplog.records)


class TestCaptureError:
    """Test capture_error function"""

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.set_context")
    @patch("src.utils.sentry_context.set_tag")
    @patch("src.utils.sentry_context.capture_exception", return_value="event-id-123")
    def test_capture_error_with_full_context(self, mock_capture, mock_set_tag, mock_set_context):
        """Test capturing error with all context parameters"""
        exception = ValueError("test error")
        context_data = {"provider": "openrouter", "model": "gpt-4"}
        tags = {"provider": "openrouter", "error_type": "api_error"}

        event_id = capture_error(
            exception, context_type="provider", context_data=context_data, tags=tags, level="error"
        )

        assert event_id == "event-id-123"
        mock_set_context.assert_called_once_with("provider", context_data)
        assert mock_set_tag.call_count == 2
        mock_set_tag.assert_any_call("provider", "openrouter")
        mock_set_tag.assert_any_call("error_type", "api_error")
        mock_capture.assert_called_once_with(exception)

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_exception", return_value="event-id-456")
    def test_capture_error_minimal(self, mock_capture):
        """Test capturing error with minimal parameters"""
        exception = RuntimeError("test error")

        event_id = capture_error(exception)

        assert event_id == "event-id-456"
        mock_capture.assert_called_once_with(exception)

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", False)
    def test_capture_error_sentry_unavailable(self):
        """Test capture_error when Sentry is unavailable"""
        exception = ValueError("test error")

        event_id = capture_error(exception)

        assert event_id is None

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_exception", side_effect=Exception("Capture failed"))
    def test_capture_error_handles_exception(self, mock_capture, caplog):
        """Test that exceptions during capture are handled"""
        import logging

        with caplog.at_level(logging.WARNING):
            event_id = capture_error(ValueError("test"))

            assert event_id is None
            assert any(
                "Failed to capture exception to Sentry" in record.message
                for record in caplog.records
            )


class TestWithSentryContextDecorator:
    """Test with_sentry_context decorator"""

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    async def test_decorator_async_function_success(self, mock_capture):
        """Test decorator on async function that succeeds"""

        @with_sentry_context("provider")
        async def async_func():
            return "success"

        result = await async_func()

        assert result == "success"
        mock_capture.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    async def test_decorator_async_function_with_exception(self, mock_capture):
        """Test decorator on async function that raises exception"""
        test_exception = ValueError("async error")

        @with_sentry_context("provider")
        async def async_func():
            raise test_exception

        with pytest.raises(ValueError, match="async error"):
            await async_func()

        mock_capture.assert_called_once()
        call_args = mock_capture.call_args
        assert call_args[0][0] == test_exception
        assert call_args[1]["context_type"] == "provider"
        assert call_args[1]["tags"] == {"function": "async_func"}

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    async def test_decorator_async_with_context_fn(self, mock_capture):
        """Test decorator on async function with context function"""
        test_exception = ValueError("error with context")

        def context_fn(provider, model):
            return {"provider": provider, "model": model}

        @with_sentry_context("provider", context_fn=context_fn)
        async def async_func(provider, model):
            raise test_exception

        with pytest.raises(ValueError):
            await async_func("openrouter", "gpt-4")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"provider": "openrouter", "model": "gpt-4"}

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    def test_decorator_sync_function_success(self, mock_capture):
        """Test decorator on sync function that succeeds"""

        @with_sentry_context("database")
        def sync_func():
            return "success"

        result = sync_func()

        assert result == "success"
        mock_capture.assert_not_called()

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    def test_decorator_sync_function_with_exception(self, mock_capture):
        """Test decorator on sync function that raises exception"""
        test_exception = RuntimeError("sync error")

        @with_sentry_context("database")
        def sync_func():
            raise test_exception

        with pytest.raises(RuntimeError, match="sync error"):
            sync_func()

        mock_capture.assert_called_once()
        call_args = mock_capture.call_args
        assert call_args[0][0] == test_exception
        assert call_args[1]["context_type"] == "database"
        assert call_args[1]["tags"] == {"function": "sync_func"}

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    def test_decorator_sync_with_context_fn(self, mock_capture):
        """Test decorator on sync function with context function"""
        test_exception = ValueError("error with context")

        def context_fn(table, operation):
            return {"table": table, "operation": operation}

        @with_sentry_context("database", context_fn=context_fn)
        def sync_func(table, operation):
            raise test_exception

        with pytest.raises(ValueError):
            sync_func("users", "insert")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"table": "users", "operation": "insert"}

    @patch("src.utils.sentry_context.SENTRY_AVAILABLE", True)
    @patch("src.utils.sentry_context.capture_error")
    def test_decorator_handles_context_fn_exception(self, mock_capture, caplog):
        """Test decorator handles exceptions in context_fn"""
        import logging

        def broken_context_fn(*args, **kwargs):
            raise RuntimeError("context fn error")

        @with_sentry_context("provider", context_fn=broken_context_fn)
        def sync_func():
            raise ValueError("main error")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ValueError):
                sync_func()

        # Should still capture the error with empty context
        mock_capture.assert_called_once()
        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {}
        assert any("Failed to build context" in record.message for record in caplog.records)


class TestCaptureProviderError:
    """Test capture_provider_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-123")
    def test_capture_provider_error_full_params(self, mock_capture):
        """Test capturing provider error with all parameters"""
        exception = ConnectionError("API timeout")

        event_id = capture_provider_error(
            exception,
            provider="openrouter",
            model="gpt-4-turbo",
            request_id="req-456",
            endpoint="/api/chat/completions",
        )

        assert event_id == "event-123"
        call_args = mock_capture.call_args
        assert call_args[0][0] == exception
        assert call_args[1]["context_type"] == "provider"
        assert call_args[1]["context_data"] == {
            "provider": "openrouter",
            "model": "gpt-4-turbo",
            "request_id": "req-456",
            "endpoint": "/api/chat/completions",
        }
        assert call_args[1]["tags"] == {"provider": "openrouter"}

    @patch("src.utils.sentry_context.capture_error", return_value="event-456")
    def test_capture_provider_error_minimal(self, mock_capture):
        """Test capturing provider error with minimal parameters"""
        exception = ValueError("Invalid model")

        event_id = capture_provider_error(exception, provider="portkey")

        assert event_id == "event-456"
        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"provider": "portkey"}


class TestCaptureDatabaseError:
    """Test capture_database_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-789")
    def test_capture_database_error_full_params(self, mock_capture):
        """Test capturing database error with all parameters"""
        exception = RuntimeError("Connection pool exhausted")

        event_id = capture_database_error(
            exception,
            operation="insert",
            table="users",
            details={"user_id": "123", "retry_count": 3},
        )

        assert event_id == "event-789"
        call_args = mock_capture.call_args
        assert call_args[1]["context_type"] == "database"
        assert call_args[1]["context_data"] == {
            "operation": "insert",
            "table": "users",
            "user_id": "123",
            "retry_count": 3,
        }
        assert call_args[1]["tags"] == {"operation": "insert", "table": "users"}

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_database_error_minimal(self, mock_capture):
        """Test capturing database error with minimal parameters"""
        exception = ValueError("Invalid query")

        capture_database_error(exception, operation="select", table="api_keys")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"operation": "select", "table": "api_keys"}


class TestCapturePaymentError:
    """Test capture_payment_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-pay-123")
    def test_capture_payment_error_full_params(self, mock_capture):
        """Test capturing payment error with all parameters"""
        exception = RuntimeError("Webhook signature invalid")

        event_id = capture_payment_error(
            exception,
            operation="webhook",
            provider="stripe",
            user_id="user-789",
            amount=99.99,
            details={"event_type": "payment_succeeded", "customer_id": "cus_123"},
        )

        assert event_id == "event-pay-123"
        call_args = mock_capture.call_args
        assert call_args[1]["context_type"] == "payment"
        assert call_args[1]["context_data"] == {
            "operation": "webhook",
            "provider": "stripe",
            "user_id": "user-789",
            "amount": 99.99,
            "event_type": "payment_succeeded",
            "customer_id": "cus_123",
        }
        assert call_args[1]["tags"] == {"operation": "webhook", "provider": "stripe"}

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_payment_error_minimal(self, mock_capture):
        """Test capturing payment error with minimal parameters"""
        exception = ValueError("Invalid amount")

        capture_payment_error(exception, operation="charge")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"operation": "charge", "provider": "stripe"}


class TestCaptureAuthError:
    """Test capture_auth_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-auth-456")
    def test_capture_auth_error_full_params(self, mock_capture):
        """Test capturing auth error with all parameters"""
        exception = PermissionError("Invalid API key")

        event_id = capture_auth_error(
            exception,
            operation="verify_key",
            user_id="user-456",
            details={"key_id": "key-789", "ip_address": "192.168.1.1"},
        )

        assert event_id == "event-auth-456"
        call_args = mock_capture.call_args
        assert call_args[1]["context_type"] == "authentication"
        assert call_args[1]["context_data"] == {
            "operation": "verify_key",
            "user_id": "user-456",
            "key_id": "key-789",
            "ip_address": "192.168.1.1",
        }
        assert call_args[1]["tags"] == {"operation": "verify_key"}

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_auth_error_minimal(self, mock_capture):
        """Test capturing auth error with minimal parameters"""
        exception = ValueError("Invalid token")

        capture_auth_error(exception, operation="validate_token")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"operation": "validate_token"}


class TestCaptureCacheError:
    """Test capture_cache_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-cache-789")
    def test_capture_cache_error_full_params(self, mock_capture):
        """Test capturing cache error with all parameters"""
        exception = ConnectionError("Redis connection timeout")

        event_id = capture_cache_error(
            exception,
            operation="set",
            cache_type="redis",
            key="model:gpt-4:availability",
            details={"ttl": 300, "size_bytes": 1024},
        )

        assert event_id == "event-cache-789"
        call_args = mock_capture.call_args
        assert call_args[1]["context_type"] == "cache"
        assert call_args[1]["context_data"] == {
            "operation": "set",
            "cache_type": "redis",
            "key": "model:gpt-4:availability",
            "ttl": 300,
            "size_bytes": 1024,
        }
        assert call_args[1]["tags"] == {"operation": "set", "cache_type": "redis"}

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_cache_error_minimal(self, mock_capture):
        """Test capturing cache error with minimal parameters"""
        exception = ValueError("Invalid cache key")

        capture_cache_error(exception, operation="get")

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {"operation": "get", "cache_type": "redis"}


class TestCaptureModelHealthError:
    """Test capture_model_health_error helper function"""

    @patch("src.utils.sentry_context.capture_error", return_value="event-health-999")
    def test_capture_model_health_error_full_params(self, mock_capture):
        """Test capturing model health error with all parameters"""
        exception = RuntimeError("Health check failed")

        event_id = capture_model_health_error(
            exception,
            model_id="gpt-4-turbo",
            provider="openai",
            gateway="openrouter",
            operation="health_check",
            status="unhealthy",
            response_time_ms=5000.0,
            details={"error_count": 5, "success_rate": 0.2},
        )

        assert event_id == "event-health-999"
        call_args = mock_capture.call_args
        assert call_args[1]["context_type"] == "model_health"
        assert call_args[1]["context_data"] == {
            "model_id": "gpt-4-turbo",
            "provider": "openai",
            "gateway": "openrouter",
            "operation": "health_check",
            "status": "unhealthy",
            "response_time_ms": 5000.0,
            "error_count": 5,
            "success_rate": 0.2,
        }
        assert call_args[1]["tags"] == {
            "provider": "openai",
            "gateway": "openrouter",
            "model_id": "gpt-4-turbo",
            "operation": "health_check",
        }

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_model_health_error_minimal(self, mock_capture):
        """Test capturing model health error with minimal parameters"""
        exception = ValueError("Model unavailable")

        capture_model_health_error(
            exception, model_id="claude-3-opus", provider="anthropic", gateway="portkey"
        )

        call_args = mock_capture.call_args
        assert call_args[1]["context_data"] == {
            "model_id": "claude-3-opus",
            "provider": "anthropic",
            "gateway": "portkey",
            "operation": "health_check",
        }

    @patch("src.utils.sentry_context.capture_error")
    def test_capture_model_health_error_with_zero_response_time(self, mock_capture):
        """Test capturing model health error with zero response time"""
        exception = TimeoutError("Model timeout")

        capture_model_health_error(
            exception,
            model_id="llama-2-70b",
            provider="meta",
            gateway="together",
            response_time_ms=0.0,
        )

        call_args = mock_capture.call_args
        # response_time_ms of 0.0 should be included (not None)
        assert "response_time_ms" in call_args[1]["context_data"]
        assert call_args[1]["context_data"]["response_time_ms"] == 0.0


class TestSentryAvailability:
    """Test behavior when Sentry is not available"""

    def test_sentry_availability_flag(self):
        """Test that SENTRY_AVAILABLE flag is set correctly"""
        # This test verifies the module loads correctly
        # The actual value depends on whether sentry_sdk is installed
        assert isinstance(SENTRY_AVAILABLE, bool)


class TestDecoratorPreservesMetadata:
    """Test that decorator preserves function metadata"""

    def test_async_decorator_preserves_name_and_docstring(self):
        """Test async decorator preserves __name__ and __doc__"""

        @with_sentry_context("test")
        async def my_async_function():
            """My async docstring"""
            pass

        assert my_async_function.__name__ == "my_async_function"
        assert my_async_function.__doc__ == "My async docstring"

    def test_sync_decorator_preserves_name_and_docstring(self):
        """Test sync decorator preserves __name__ and __doc__"""

        @with_sentry_context("test")
        def my_sync_function():
            """My sync docstring"""
            pass

        assert my_sync_function.__name__ == "my_sync_function"
        assert my_sync_function.__doc__ == "My sync docstring"
