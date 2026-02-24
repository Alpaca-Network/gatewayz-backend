"""
Tests for src/config/supabase_config.py

Tests the Supabase client initialization and URL validation.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest


def create_mock_supabase_client_with_connection():
    """Create a mock Supabase client configured for connection testing.

    Returns a mock client with proper chainable methods that simulate
    a successful database connection test.
    """
    execute_result = Mock()
    execute_result.data = []

    mock_client = Mock()
    mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
        execute_result
    )
    mock_client.postgrest = Mock()
    mock_client.postgrest.session = Mock()

    return mock_client


class TestGetSupabaseClientValidation:
    """Test SUPABASE_URL validation in get_supabase_client"""

    def test_raises_error_when_supabase_url_not_set(self):
        """Test that get_supabase_client raises RuntimeError when SUPABASE_URL is not set"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Patch Config to simulate missing SUPABASE_URL
        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", None):
            with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                with pytest.raises(RuntimeError) as exc_info:
                    supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        assert "SUPABASE_URL" in error_message
        assert "not set" in error_message.lower()

    def test_raises_error_when_supabase_url_missing_protocol(self):
        """Test that get_supabase_client raises RuntimeError when SUPABASE_URL lacks protocol"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Patch Config to simulate missing protocol
        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                with pytest.raises(RuntimeError) as exc_info:
                    supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        assert "http://" in error_message or "https://" in error_message

    def test_error_message_includes_example_fix(self):
        """Test that error message for missing protocol includes example of correct format"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Patch Config to simulate missing protocol
        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "myproject.supabase.co"):
            with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                with pytest.raises(RuntimeError) as exc_info:
                    supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        # Should include the expected format as a hint
        assert "https://myproject.supabase.co" in error_message

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_accepts_valid_https_url(self, mock_httpx_client, mock_create_client):
        """Test that get_supabase_client accepts valid https:// URL"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Mock the Supabase client using helper
        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        # Patch Config with valid URL
        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    # Should not raise
                    client = supabase_config_mod.get_supabase_client()
                    assert client is not None
                    mock_create_client.assert_called_once()

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_accepts_valid_http_url_for_local_dev(self, mock_httpx_client, mock_create_client):
        """Test that get_supabase_client accepts valid http:// URL for local development"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Mock the Supabase client using helper
        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        # Patch Config with valid local URL
        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "http://localhost:54321"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    # Should not raise
                    client = supabase_config_mod.get_supabase_client()
                    assert client is not None

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_logs_masked_url_on_initialization(self, mock_httpx_client, mock_create_client, caplog):
        """Test that initialization logs a masked version of the URL"""
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Mock the Supabase client using helper
        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        # Patch Config with a long URL to test masking
        with patch.object(
            supabase_config_mod.Config, "SUPABASE_URL", "https://verylongprojectname.supabase.co"
        ):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    with caplog.at_level("INFO"):
                        supabase_config_mod.get_supabase_client()

        # Check that some form of the URL was logged (masked)
        assert any("Initializing Supabase client" in record.message for record in caplog.records)


class TestHttpxClientConfiguration:
    """Test the httpx client is configured with proper authentication headers.

    These tests verify that when we inject a custom httpx client into the Supabase
    postgrest session, it includes the required authentication headers. Without these
    headers, all database operations fail with "No API key found in request" errors.
    """

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_includes_apikey_header(self, mock_httpx_client, mock_create_client):
        """Test that httpx client is created with apikey header.

        The apikey header is required by Supabase PostgREST API for authentication.
        Without this header, requests fail with:
        {'message': 'No API key found in request', 'hint': 'No `apikey` request header or url param was found.'}
        """
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        test_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test_key"

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", test_key):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        mock_httpx_client.assert_called_once()
        call_kwargs = mock_httpx_client.call_args[1]

        assert "headers" in call_kwargs, "httpx.Client must be created with headers"
        assert "apikey" in call_kwargs["headers"], "headers must include 'apikey'"
        assert call_kwargs["headers"]["apikey"] == test_key

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_includes_authorization_header(
        self, mock_httpx_client, mock_create_client
    ):
        """Test that httpx client is created with Authorization Bearer header.

        The Authorization header with Bearer token is required for authenticated
        Supabase operations beyond anonymous access.
        """
        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client and error state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        test_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test_key"

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", test_key):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        mock_httpx_client.assert_called_once()
        call_kwargs = mock_httpx_client.call_args[1]

        assert "headers" in call_kwargs, "httpx.Client must be created with headers"
        assert "Authorization" in call_kwargs["headers"], "headers must include 'Authorization'"
        assert call_kwargs["headers"]["Authorization"] == f"Bearer {test_key}"
        assert call_kwargs["headers"]["Authorization"].startswith(
            "Bearer "
        ), "Authorization must use Bearer scheme"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_auth_headers_match_supabase_key(
        self, mock_httpx_client, mock_create_client
    ):
        """Test that both auth headers use the same SUPABASE_KEY value.

        Both apikey and Authorization headers must use the exact same key value
        to ensure consistent authentication across all request types.
        """
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        # Use a realistic JWT-like key
        test_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRlc3QifQ.signature"

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", test_key):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        call_kwargs = mock_httpx_client.call_args[1]
        headers = call_kwargs["headers"]

        # Extract key from Authorization header
        auth_key = headers["Authorization"].replace("Bearer ", "")

        assert headers["apikey"] == auth_key, "apikey and Authorization Bearer token must match"
        assert headers["apikey"] == test_key, "Both must match the configured SUPABASE_KEY"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_includes_base_url(self, mock_httpx_client, mock_create_client):
        """Test that httpx client is created with correct PostgREST base_url.

        The base_url must point to the PostgREST endpoint (/rest/v1) so that
        relative paths in database queries resolve correctly.
        """
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        test_url = "https://myproject.supabase.co"

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", test_url):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        mock_httpx_client.assert_called_once()
        call_kwargs = mock_httpx_client.call_args[1]

        assert "base_url" in call_kwargs, "httpx.Client must be created with base_url"
        assert call_kwargs["base_url"] == f"{test_url}/rest/v1"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_injected_into_postgrest_session(
        self, mock_httpx_client, mock_create_client
    ):
        """Test that the configured httpx client is injected into postgrest.session.

        The custom httpx client with auth headers must replace the postgrest session
        so all database operations use the authenticated client.
        """
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        # Create a mock that has postgrest.session attribute
        mock_postgrest = Mock()
        mock_postgrest.session = Mock()  # Original session to be replaced

        mock_client = create_mock_supabase_client_with_connection()
        mock_client.postgrest = mock_postgrest
        mock_create_client.return_value = mock_client

        mock_httpx_instance = Mock()
        mock_httpx_client.return_value = mock_httpx_instance

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        # Verify the httpx client was injected into postgrest.session
        assert (
            mock_client.postgrest.session == mock_httpx_instance
        ), "Custom httpx client must be injected into postgrest.session"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.HTTPTransport")
    def test_httpx_client_has_http2_disabled(self, mock_http_transport, mock_create_client):
        """Test that httpx client is created with HTTP/2 disabled to prevent connection errors.

        HTTP/2 was disabled to fix "Bad file descriptor" errors (errno 9) that occur
        when Supabase closes idle HTTP/2 connections. HTTP/1.1 with connection pooling
        provides better stability for long-running services.
        """
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        # Verify HTTP/2 is disabled in the transport configuration
        call_kwargs = mock_http_transport.call_args[1]
        assert (
            call_kwargs.get("http2") is False
        ), "HTTP/2 must be disabled to prevent stale connection errors"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_has_connection_pooling(self, mock_httpx_client, mock_create_client):
        """Test that httpx client is created with connection pooling limits."""
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        call_kwargs = mock_httpx_client.call_args[1]
        assert "limits" in call_kwargs, "httpx.Client must be created with connection limits"

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_httpx_client_has_timeout_configured(self, mock_httpx_client, mock_create_client):
        """Test that httpx client is created with appropriate timeout settings."""
        import src.config.supabase_config as supabase_config_mod

        supabase_config_mod._supabase_client = None

        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    supabase_config_mod.get_supabase_client()

        call_kwargs = mock_httpx_client.call_args[1]
        assert "timeout" in call_kwargs, "httpx.Client must be created with timeout"


class TestLazySupabaseClient:
    """Test the _LazySupabaseClient proxy class"""

    def test_lazy_client_repr(self):
        """Test that lazy client has a meaningful repr"""
        import src.config.supabase_config as supabase_config_mod

        lazy_client = supabase_config_mod._LazySupabaseClient()
        assert repr(lazy_client) == "<LazySupabaseClient proxy>"

    def test_lazy_client_raises_attribute_error_for_dunder_attrs(self):
        """Test that lazy client raises AttributeError for dunder attributes"""
        import src.config.supabase_config as supabase_config_mod

        lazy_client = supabase_config_mod._LazySupabaseClient()

        with pytest.raises(AttributeError):
            _ = lazy_client._private_attr

        with pytest.raises(AttributeError):
            _ = lazy_client.__some_dunder__


class TestGetInitializationStatus:
    """Test the get_initialization_status function"""

    def test_get_initialization_status_when_not_initialized(self):
        """Test initialization status when client is not yet initialized"""
        import src.config.supabase_config as supabase_config_mod

        # Reset state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        status = supabase_config_mod.get_initialization_status()

        assert status["initialized"] is False
        assert status["has_error"] is False
        assert status["error_message"] is None
        assert status["error_type"] is None

    def test_get_initialization_status_when_initialized(self):
        """Test initialization status when client is successfully initialized"""
        import src.config.supabase_config as supabase_config_mod

        # Reset state - use helper for proper mock structure
        supabase_config_mod._supabase_client = create_mock_supabase_client_with_connection()
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        status = supabase_config_mod.get_initialization_status()

        assert status["initialized"] is True
        assert status["has_error"] is False
        assert status["error_message"] is None
        assert status["error_type"] is None

    def test_get_initialization_status_when_error_occurred(self):
        """Test initialization status when initialization failed"""
        import src.config.supabase_config as supabase_config_mod

        # Reset state
        supabase_config_mod._supabase_client = None
        test_error = RuntimeError("Database connection failed")
        supabase_config_mod._last_error = test_error
        supabase_config_mod._last_error_time = 0

        status = supabase_config_mod.get_initialization_status()

        assert status["initialized"] is False
        assert status["has_error"] is True
        assert "Database connection failed" in status["error_message"]
        assert status["error_type"] == "RuntimeError"


class TestErrorPersistence:
    """Test error persistence and re-raising behavior"""

    def test_get_supabase_client_reraises_previous_error(self):
        """Test that get_supabase_client re-raises previous initialization errors within TTL"""
        import time

        import src.config.supabase_config as supabase_config_mod

        # Simulate a previous initialization failure
        test_error = RuntimeError("Previous initialization failed")
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = test_error
        supabase_config_mod._last_error_time = time.time()  # Fresh error

        # Trying to get the client again should raise the cached error (within TTL)
        with pytest.raises(RuntimeError) as exc_info:
            supabase_config_mod.get_supabase_client()

        assert (
            "unavailable" in str(exc_info.value).lower() or "retry" in str(exc_info.value).lower()
        )

    def test_initialization_error_captured_on_failure(self):
        """Test that initialization errors are captured for future reference"""
        import src.config.supabase_config as supabase_config_mod

        # Reset state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", None):
            with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                with pytest.raises(RuntimeError):
                    supabase_config_mod.get_supabase_client()

        # Error should be captured
        assert supabase_config_mod._last_error is not None


class TestTestConnectionInternal:
    """Test the _test_connection_internal function"""

    def test_test_connection_internal_success(self):
        """Test successful connection test"""
        import src.config.supabase_config as supabase_config_mod

        mock_client = create_mock_supabase_client_with_connection()

        result = supabase_config_mod._test_connection_internal(mock_client)

        assert result is True
        mock_client.table.assert_called_once_with("users")

    def test_test_connection_internal_failure(self):
        """Test connection test failure"""
        import src.config.supabase_config as supabase_config_mod

        mock_client = create_mock_supabase_client_with_connection()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception(
            "Connection timeout"
        )

        with pytest.raises(RuntimeError, match="Database connection failed"):
            supabase_config_mod._test_connection_internal(mock_client)


class TestTestConnection:
    """Test the public test_connection function"""

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_test_connection_uses_cached_client(self, mock_httpx_client, mock_create_client):
        """Test that test_connection uses the cached client"""
        import src.config.supabase_config as supabase_config_mod

        # Reset state
        supabase_config_mod._supabase_client = None
        supabase_config_mod._last_error = None
        supabase_config_mod._last_error_time = 0

        # Setup mock using helper
        mock_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = mock_client

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    # First call initializes
                    result = supabase_config_mod.test_connection()
                    assert result is True

                    # Second call should use cached client
                    result2 = supabase_config_mod.test_connection()
                    assert result2 is True

                    # create_client should only be called once
                    assert mock_create_client.call_count == 1


class TestConnectionErrorHandling:
    """Test connection error handling and retry logic"""

    def test_is_connection_error_detects_write_error(self):
        """Test that is_connection_error detects WriteError with bad file descriptor"""
        import src.config.supabase_config as supabase_config_mod

        # Simulate the exact error from Railway logs
        error = Exception("WriteError: [Errno 9] Bad file descriptor")
        assert supabase_config_mod.is_connection_error(error) is True

    def test_is_connection_error_detects_connection_terminated(self):
        """Test that is_connection_error detects connection terminated errors"""
        import src.config.supabase_config as supabase_config_mod

        error = Exception("ConnectionTerminated: HTTP/2 connection was terminated")
        assert supabase_config_mod.is_connection_error(error) is True

    def test_is_connection_error_ignores_other_errors(self):
        """Test that is_connection_error doesn't flag non-connection errors"""
        import src.config.supabase_config as supabase_config_mod

        error = ValueError("Invalid input")
        assert supabase_config_mod.is_connection_error(error) is False

    @patch("src.config.supabase_config.get_supabase_client")
    @patch("src.config.supabase_config.refresh_supabase_client")
    def test_execute_with_retry_retries_on_connection_error(self, mock_refresh, mock_get_client):
        """Test that execute_with_retry retries when a connection error occurs"""
        import src.config.supabase_config as supabase_config_mod

        mock_client = create_mock_supabase_client_with_connection()

        # First call fails with connection error, second succeeds
        call_count = [0]

        def operation(client):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("WriteError: [Errno 9] Bad file descriptor")
            return {"success": True}

        mock_get_client.return_value = mock_client
        mock_refresh.return_value = mock_client

        result = supabase_config_mod.execute_with_retry(
            operation, max_retries=2, retry_delay=0.1, operation_name="test operation"
        )

        assert result == {"success": True}
        assert call_count[0] == 2  # First call failed, second succeeded
        mock_refresh.assert_called_once()  # Client was refreshed

    @patch("src.config.supabase_config.get_supabase_client")
    def test_execute_with_retry_exhausts_retries(self, mock_get_client):
        """Test that execute_with_retry raises after exhausting retries"""
        import src.config.supabase_config as supabase_config_mod

        mock_client = create_mock_supabase_client_with_connection()

        def operation(client):
            raise Exception("WriteError: [Errno 9] Bad file descriptor")

        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="Bad file descriptor"):
            supabase_config_mod.execute_with_retry(
                operation, max_retries=2, retry_delay=0.1, operation_name="test operation"
            )

    @patch("src.config.supabase_config.get_supabase_client")
    def test_execute_with_retry_doesnt_retry_non_connection_errors(self, mock_get_client):
        """Test that execute_with_retry doesn't retry non-connection errors"""
        import src.config.supabase_config as supabase_config_mod

        mock_client = create_mock_supabase_client_with_connection()

        call_count = [0]

        def operation(client):
            call_count[0] += 1
            raise ValueError("Invalid input")

        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="Invalid input"):
            supabase_config_mod.execute_with_retry(
                operation, max_retries=2, retry_delay=0.1, operation_name="test operation"
            )

        # Should fail immediately without retries
        assert call_count[0] == 1

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_refresh_supabase_client_closes_old_connection(
        self, mock_httpx_client, mock_create_client
    ):
        """Test that refresh_supabase_client properly closes the old connection"""
        import src.config.supabase_config as supabase_config_mod

        # Set up an existing client with a session
        old_session = MagicMock()
        old_session.close = MagicMock()

        old_client = create_mock_supabase_client_with_connection()
        old_client.postgrest.session = old_session

        supabase_config_mod._supabase_client = old_client

        # Mock new client creation
        new_client = create_mock_supabase_client_with_connection()
        mock_create_client.return_value = new_client

        with patch.object(supabase_config_mod.Config, "SUPABASE_URL", "https://test.supabase.co"):
            with patch.object(supabase_config_mod.Config, "SUPABASE_KEY", "test_key"):
                with patch.object(supabase_config_mod.Config, "validate", return_value=True):
                    result = supabase_config_mod.refresh_supabase_client()

        # Verify old session was closed
        old_session.close.assert_called_once()
        # Verify we got a new client
        assert result == new_client
