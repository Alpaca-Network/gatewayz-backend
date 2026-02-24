"""
Tests for Auto-Sentry Decorator and Utilities

This test suite verifies the intelligent auto-capture decorator and
context detection functionality.
"""

import asyncio
from unittest.mock import Mock, call, patch

import pytest

from src.utils.auto_sentry import (
    _contains_sensitive_data,
    _detect_context_type,
    _extract_context_data,
    _extract_provider_from_module,
    _infer_auth_operation,
    _infer_cache_operation,
    _infer_db_operation,
    _infer_payment_operation,
    auto_capture_errors,
)


class TestAutoCaptureSentryDecorator:
    """Test the auto_capture_errors decorator"""

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_error")
    @pytest.mark.asyncio
    async def test_async_function_capture(self, mock_capture):
        """Test decorator captures errors from async functions"""

        @auto_capture_errors
        async def test_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await test_function()

        # Should have captured the error
        assert mock_capture.called

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_error")
    def test_sync_function_capture(self, mock_capture):
        """Test decorator captures errors from sync functions"""

        @auto_capture_errors
        def test_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            test_function()

        # Should have captured the error
        assert mock_capture.called

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", False)
    @pytest.mark.asyncio
    async def test_decorator_when_sentry_unavailable(self):
        """Test decorator works when Sentry is unavailable"""

        @auto_capture_errors
        async def test_function():
            return "success"

        result = await test_function()
        assert result == "success"

        # Should also still raise errors
        @auto_capture_errors
        async def test_error():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await test_error()

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_provider_error")
    @pytest.mark.asyncio
    async def test_explicit_context_type(self, mock_capture):
        """Test explicit context type in decorator"""

        @auto_capture_errors(context_type="provider")
        async def test_function(provider: str, model: str):
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await test_function("openrouter", "gpt-4")

        # Should have called provider-specific capture
        mock_capture.assert_called_once()
        call_args = mock_capture.call_args
        assert call_args[1]["provider"] == "openrouter"
        assert call_args[1]["model"] == "gpt-4"

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_error")
    def test_reraise_false(self, mock_capture):
        """Test decorator with reraise=False"""

        @auto_capture_errors(reraise=False)
        def test_function():
            raise ValueError("Test error")

        # Should NOT raise the error
        result = test_function()
        assert result is None

        # But should still capture it
        assert mock_capture.called

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_database_error")
    def test_database_context_detection(self, mock_capture):
        """Test automatic database context detection"""

        @auto_capture_errors
        def create_user(table: str, email: str):
            raise ValueError("Database error")

        with pytest.raises(ValueError):
            create_user("users", "test@example.com")

        # Should detect as database operation
        mock_capture.assert_called_once()


class TestContextDetection:
    """Test context type detection logic"""

    def test_detect_provider_context(self):
        """Test provider context detection"""
        # Function name detection
        assert _detect_context_type("make_provider_request", "src.services.test", {}) == "provider"
        assert _detect_context_type("openrouter_client", "src.services.test", {}) == "provider"

        # Module detection
        assert _detect_context_type("test_func", "src.services.openrouter_client", {}) == "provider"

    def test_detect_database_context(self):
        """Test database context detection"""
        # Module detection
        assert _detect_context_type("test_func", "src.db.users", {}) == "database"
        assert _detect_context_type("test_func", "src\\db\\users", {}) == "database"

        # Function name detection
        assert _detect_context_type("insert_user", "src.services.test", {}) == "database"
        assert _detect_context_type("query_data", "src.services.test", {}) == "database"
        assert _detect_context_type("supabase_query", "src.services.test", {}) == "database"

    def test_detect_payment_context(self):
        """Test payment context detection"""
        assert _detect_context_type("process_payment", "src.services.test", {}) == "payment"
        assert _detect_context_type("stripe_webhook", "src.services.test", {}) == "payment"
        assert _detect_context_type("deduct_credits", "src.services.test", {}) == "payment"

    def test_detect_auth_context(self):
        """Test auth context detection"""
        assert _detect_context_type("authenticate", "src.services.test", {}) == "auth"
        assert _detect_context_type("verify_token", "src.services.test", {}) == "auth"
        assert _detect_context_type("login_user", "src.services.test", {}) == "auth"
        assert _detect_context_type("privy_auth", "src.services.test", {}) == "auth"

    def test_detect_cache_context(self):
        """Test cache context detection"""
        assert _detect_context_type("get_cache", "src.services.test", {}) == "cache"
        assert _detect_context_type("redis_get", "src.services.test", {}) == "cache"

    def test_detect_general_context(self):
        """Test fallback to general context"""
        assert _detect_context_type("random_function", "src.services.test", {}) == "general"


class TestContextExtraction:
    """Test context data extraction"""

    def test_extract_provider_context(self):
        """Test provider context extraction"""
        params = {"provider": "openrouter", "model": "gpt-4", "request_id": "req-123"}

        context = _extract_context_data("provider", params, "test_func", "src.services.test")

        assert context["provider"] == "openrouter"
        assert context["model"] == "gpt-4"
        assert context["request_id"] == "req-123"

    def test_extract_provider_from_module(self):
        """Test provider extraction from module name"""
        assert _extract_provider_from_module("src.services.openrouter_client") == "openrouter"
        assert _extract_provider_from_module("src.services.portkey_client") == "portkey"
        assert _extract_provider_from_module("src.services.deepinfra_client") == "deepinfra"
        assert _extract_provider_from_module("src.services.unknown_module") == "unknown"

    def test_extract_database_context(self):
        """Test database context extraction"""
        params = {"table": "users", "id": "user-123"}

        context = _extract_context_data("database", params, "create_user", "src.db.users")

        assert context["operation"] == "insert"  # Inferred from "create_user"
        assert context["table"] == "users"
        assert context["record_id"] == "user-123"

    def test_extract_payment_context(self):
        """Test payment context extraction"""
        params = {"user_id": "user-123", "amount": 10.50, "currency": "USD"}

        context = _extract_context_data(
            "payment", params, "deduct_credits", "src.services.payments"
        )

        assert context["operation"] == "charge"  # Inferred from "deduct_credits"
        assert context["user_id"] == "user-123"
        assert context["amount"] == 10.50
        assert context["currency"] == "USD"

    def test_extract_auth_context(self):
        """Test auth context extraction"""
        params = {"user_id": "user-123", "email": "test@example.com"}

        context = _extract_context_data("auth", params, "login_user", "src.routes.auth")

        assert context["operation"] == "login"  # Inferred from "login_user"
        assert context["user_id"] == "user-123"
        assert context["email"] == "test@example.com"

    def test_extract_cache_context(self):
        """Test cache context extraction"""
        params = {"key": "cache-key-123", "cache_type": "redis"}

        context = _extract_context_data("cache", params, "get_value", "src.services.cache")

        assert context["operation"] == "get"  # Inferred from "get_value"
        assert context["cache_type"] == "redis"
        assert context["key"] == "cache-key-123"


class TestOperationInference:
    """Test operation type inference from function names"""

    def test_infer_db_operation(self):
        """Test database operation inference"""
        assert _infer_db_operation("create_user") == "insert"
        assert _infer_db_operation("insert_record") == "insert"
        assert _infer_db_operation("add_item") == "insert"

        assert _infer_db_operation("update_user") == "update"
        assert _infer_db_operation("modify_record") == "update"
        assert _infer_db_operation("edit_item") == "update"

        assert _infer_db_operation("delete_user") == "delete"
        assert _infer_db_operation("remove_record") == "delete"

        assert _infer_db_operation("get_user") == "select"
        assert _infer_db_operation("fetch_records") == "select"
        assert _infer_db_operation("find_item") == "select"

        assert _infer_db_operation("random_function") == "unknown"

    def test_infer_payment_operation(self):
        """Test payment operation inference"""
        assert _infer_payment_operation("process_webhook") == "webhook_processing"
        assert _infer_payment_operation("handle_checkout") == "checkout"
        assert _infer_payment_operation("charge_user") == "charge"
        assert _infer_payment_operation("deduct_credits") == "charge"
        assert _infer_payment_operation("refund_payment") == "refund"
        assert _infer_payment_operation("random_function") == "unknown"

    def test_infer_auth_operation(self):
        """Test auth operation inference"""
        assert _infer_auth_operation("login_user") == "login"
        assert _infer_auth_operation("verify_token") == "verify"
        assert _infer_auth_operation("authenticate_request") == "authentication"
        assert _infer_auth_operation("validate_token") == "token_validation"
        assert _infer_auth_operation("random_function") == "unknown"

    def test_infer_cache_operation(self):
        """Test cache operation inference"""
        assert _infer_cache_operation("get_value") == "get"
        assert _infer_cache_operation("set_cache") == "set"
        assert _infer_cache_operation("delete_key") == "delete"
        assert _infer_cache_operation("clear_cache") == "delete"
        assert _infer_cache_operation("random_function") == "unknown"


class TestSensitiveDataDetection:
    """Test sensitive data detection"""

    def test_contains_sensitive_data_true(self):
        """Test detection of sensitive data"""
        # Password
        assert _contains_sensitive_data({"password": "secret123"})
        assert _contains_sensitive_data({"user_password": "secret123"})

        # API keys
        assert _contains_sensitive_data({"api_key": "sk-123"})
        assert _contains_sensitive_data({"apikey": "sk-123"})

        # Tokens
        assert _contains_sensitive_data({"token": "bearer-token"})
        assert _contains_sensitive_data({"access_token": "token"})

        # Secrets
        assert _contains_sensitive_data({"secret": "secret-value"})
        assert _contains_sensitive_data({"client_secret": "secret"})

        # Credit cards
        assert _contains_sensitive_data({"credit_card": "4111111111111111"})

    def test_contains_sensitive_data_false(self):
        """Test safe data is not flagged"""
        assert not _contains_sensitive_data({"email": "test@example.com"})
        assert not _contains_sensitive_data({"name": "John Doe"})
        assert not _contains_sensitive_data({"user_id": "user-123"})
        assert not _contains_sensitive_data({"amount": 10.50})
        assert not _contains_sensitive_data({})


class TestAutoCaptureSentryIntegration:
    """Integration tests for auto-capture with real-world scenarios"""

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_provider_error")
    @pytest.mark.asyncio
    async def test_provider_error_capture_full_flow(self, mock_capture):
        """Test full flow of provider error capture"""

        @auto_capture_errors
        async def make_openrouter_request(provider: str, model: str, messages: list):
            # Simulate provider timeout
            raise TimeoutError("Provider request timed out")

        with pytest.raises(TimeoutError):
            await make_openrouter_request("openrouter", "gpt-4", [])

        # Verify capture was called with correct args
        mock_capture.assert_called_once()
        call_args = mock_capture.call_args

        # Check exception
        assert isinstance(call_args[0][0], TimeoutError)

        # Check context
        assert call_args[1]["provider"] == "openrouter"
        assert call_args[1]["model"] == "gpt-4"

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_database_error")
    def test_database_error_capture_full_flow(self, mock_capture):
        """Test full flow of database error capture"""

        @auto_capture_errors
        def create_user_record(table: str, email: str):
            # Simulate database error
            raise ConnectionError("Database connection failed")

        with pytest.raises(ConnectionError):
            create_user_record("users", "test@example.com")

        # Verify capture was called
        mock_capture.assert_called_once()
        call_args = mock_capture.call_args

        # Check exception
        assert isinstance(call_args[0][0], ConnectionError)

        # Check context
        assert call_args[1]["operation"] == "insert"  # Inferred from "create"
        assert call_args[1]["table"] == "users"

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_payment_error")
    def test_payment_error_capture_full_flow(self, mock_capture):
        """Test full flow of payment error capture"""

        @auto_capture_errors
        def deduct_user_credits(user_id: str, amount: float):
            # Simulate payment processing error
            raise ValueError("Insufficient credits")

        with pytest.raises(ValueError):
            deduct_user_credits("user-123", 10.50)

        # Verify capture was called
        mock_capture.assert_called_once()
        call_args = mock_capture.call_args

        # Check exception
        assert isinstance(call_args[0][0], ValueError)

        # Check context
        assert call_args[1]["operation"] == "charge"  # Inferred from "deduct"
        assert call_args[1]["user_id"] == "user-123"
        assert call_args[1]["amount"] == 10.50

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_error")
    @pytest.mark.asyncio
    async def test_successful_execution_no_capture(self, mock_capture):
        """Test that successful execution doesn't trigger capture"""

        @auto_capture_errors
        async def successful_function():
            return "success"

        result = await successful_function()
        assert result == "success"

        # Should NOT have captured anything
        mock_capture.assert_not_called()

    @patch("src.utils.auto_sentry.SENTRY_AVAILABLE", True)
    @patch("src.utils.auto_sentry.capture_error")
    def test_fallback_to_generic_capture(self, mock_capture):
        """Test fallback to generic capture for unknown context"""

        @auto_capture_errors
        def random_function_with_error():
            raise ValueError("Some error")

        with pytest.raises(ValueError):
            random_function_with_error()

        # Should fall back to generic capture_error
        mock_capture.assert_called_once()
