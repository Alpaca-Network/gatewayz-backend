"""
Tests for src/utils/db_retry.py

Tests the database retry utilities for handling transient HTTP/2 connection errors.
"""

import time
from unittest.mock import Mock, patch, MagicMock

import pytest


class TestIsHttp2ConnectionError:
    """Test the is_http2_connection_error function"""

    def test_detects_stream_id_too_low_error(self):
        """Test that StreamIDTooLowError is detected as HTTP/2 connection error"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("StreamIDTooLowError: Stream ID 1 is too low")
        assert is_http2_connection_error(error) is True

    def test_detects_connection_terminated_error(self):
        """Test that ConnectionTerminated is detected as HTTP/2 connection error"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("ConnectionTerminated: Remote peer closed connection")
        assert is_http2_connection_error(error) is True

    def test_detects_server_disconnected_error(self):
        """Test that Server disconnected is detected as HTTP/2 connection error"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("Server disconnected unexpectedly")
        assert is_http2_connection_error(error) is True

    def test_detects_local_protocol_error(self):
        """Test that LocalProtocolError is detected as HTTP/2 connection error"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("LocalProtocolError: Invalid input")
        assert is_http2_connection_error(error) is True

    def test_detects_closed_state_error(self):
        """Test that RECV_DATA in ConnectionState.CLOSED is detected"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception(
            "Invalid input ConnectionInputs.RECV_DATA in state ConnectionState.CLOSED"
        )
        assert is_http2_connection_error(error) is True

    def test_detects_nested_http2_error(self):
        """Test that nested HTTP/2 errors in exception chain are detected"""
        from src.utils.db_retry import is_http2_connection_error

        inner_error = Exception("StreamIDTooLowError: Inner error")
        outer_error = Exception("Outer error")
        outer_error.__cause__ = inner_error

        assert is_http2_connection_error(outer_error) is True

    def test_does_not_detect_regular_errors(self):
        """Test that regular errors are not detected as HTTP/2 connection errors"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("User not found")
        assert is_http2_connection_error(error) is False

    def test_does_not_detect_database_errors(self):
        """Test that database errors are not detected as HTTP/2 connection errors"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("duplicate key value violates unique constraint")
        assert is_http2_connection_error(error) is False

    def test_detects_h2_exceptions(self):
        """Test that h2.exceptions errors are detected"""
        from src.utils.db_retry import is_http2_connection_error

        error = Exception("h2.exceptions.StreamClosedError: Stream 1 is closed")
        assert is_http2_connection_error(error) is True


class TestWithDbRetry:
    """Test the with_db_retry decorator"""

    def test_successful_operation_no_retry(self):
        """Test that successful operations don't trigger retry"""
        from src.utils.db_retry import with_db_retry

        call_count = 0

        @with_db_retry("test operation")
        def successful_function():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_function()

        assert result == "success"
        assert call_count == 1

    def test_retries_on_http2_error(self):
        """Test that HTTP/2 errors trigger retry"""
        from src.utils.db_retry import with_db_retry

        call_count = 0

        @with_db_retry("test operation", reset_on_error=False)
        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("StreamIDTooLowError: Stream ID too low")
            return "success"

        result = flaky_function()

        assert result == "success"
        assert call_count == 2

    def test_no_retry_on_regular_error(self):
        """Test that regular errors don't trigger retry"""
        from src.utils.db_retry import with_db_retry

        call_count = 0

        @with_db_retry("test operation")
        def failing_function():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        with pytest.raises(ValueError, match="Invalid input"):
            failing_function()

        assert call_count == 1

    def test_max_retries_exhausted(self):
        """Test that max retries are respected"""
        from src.utils.db_retry import with_db_retry

        call_count = 0

        @with_db_retry("test operation", max_retries=2, reset_on_error=False)
        def always_failing_function():
            nonlocal call_count
            call_count += 1
            raise Exception("ConnectionTerminated: Always fails")

        with pytest.raises(Exception, match="ConnectionTerminated"):
            always_failing_function()

        # Should be called max_retries + 1 times (initial + retries)
        assert call_count == 3

    def test_resets_client_on_error(self):
        """Test that Supabase client is reset on HTTP/2 error"""
        from src.utils.db_retry import with_db_retry

        with patch("src.utils.db_retry.reset_supabase_client") as mock_reset:
            call_count = 0

            @with_db_retry("test operation", max_retries=1, reset_on_error=True)
            def flaky_function():
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise Exception("StreamIDTooLowError: First attempt fails")
                return "success"

            result = flaky_function()

            assert result == "success"
            mock_reset.assert_called_once()

    def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata"""
        from src.utils.db_retry import with_db_retry

        @with_db_retry("test operation")
        def my_function():
            """This is my function docstring."""
            return "result"

        assert my_function.__name__ == "my_function"
        assert "docstring" in my_function.__doc__


class TestExecuteWithRetry:
    """Test the execute_with_retry function"""

    def test_executes_function_successfully(self):
        """Test that execute_with_retry runs function successfully"""
        from src.utils.db_retry import execute_with_retry

        def my_function():
            return "success"

        result = execute_with_retry(my_function, operation_name="test")
        assert result == "success"

    def test_passes_args_and_kwargs(self):
        """Test that execute_with_retry passes arguments correctly"""
        from src.utils.db_retry import execute_with_retry

        def add_numbers(a, b, multiplier=1):
            return (a + b) * multiplier

        result = execute_with_retry(
            add_numbers, 2, 3, multiplier=2, operation_name="add numbers"
        )
        assert result == 10

    def test_retries_on_http2_error(self):
        """Test that execute_with_retry retries on HTTP/2 errors"""
        from src.utils.db_retry import execute_with_retry

        call_count = 0

        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Server disconnected")
            return "success"

        with patch("src.utils.db_retry.reset_supabase_client"):
            result = execute_with_retry(
                flaky_function, operation_name="flaky operation", reset_on_error=True
            )

        assert result == "success"
        assert call_count == 2


class TestResetSupabaseClient:
    """Test the reset_supabase_client function"""

    def test_calls_cleanup_function(self):
        """Test that reset_supabase_client calls cleanup_supabase_client"""
        from src.utils.db_retry import reset_supabase_client

        with patch(
            "src.utils.db_retry.cleanup_supabase_client"
        ) as mock_cleanup:
            reset_supabase_client()
            mock_cleanup.assert_called_once()

    def test_handles_cleanup_errors_gracefully(self):
        """Test that reset_supabase_client handles cleanup errors gracefully"""
        from src.utils.db_retry import reset_supabase_client

        with patch(
            "src.utils.db_retry.cleanup_supabase_client",
            side_effect=Exception("Cleanup failed"),
        ):
            # Should not raise exception
            reset_supabase_client()


class TestRetryTiming:
    """Test retry timing and backoff behavior"""

    def test_exponential_backoff(self):
        """Test that retry uses exponential backoff"""
        from src.utils.db_retry import with_db_retry

        delays = []

        with patch("src.utils.db_retry.time.sleep") as mock_sleep:
            with patch("src.utils.db_retry.reset_supabase_client"):
                call_count = 0

                @with_db_retry("test operation", max_retries=2, reset_on_error=True)
                def always_failing():
                    nonlocal call_count
                    call_count += 1
                    raise Exception("StreamIDTooLowError")

                with pytest.raises(Exception):
                    always_failing()

                # Check that sleep was called with increasing delays
                assert mock_sleep.call_count == 2
                delays = [call.args[0] for call in mock_sleep.call_args_list]

                # First delay should be smaller than second (exponential backoff)
                assert delays[1] > delays[0]
