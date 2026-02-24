"""
Comprehensive tests for retry utilities with exponential backoff.

Tests both synchronous and asynchronous retry decorators for handling
transient network and database connection errors.
"""

import asyncio
import time
from unittest.mock import Mock, call, patch

import pytest

from src.utils.retry import with_async_retry, with_retry


class TestWithRetry:
    """Test synchronous retry decorator functionality"""

    def test_successful_function_no_retry_needed(self):
        """Test that successful function executes without retries"""
        mock_func = Mock(return_value="success")
        decorated = with_retry()(mock_func)

        result = decorated("arg1", kwarg1="value1")

        assert result == "success"
        assert mock_func.call_count == 1
        mock_func.assert_called_once_with("arg1", kwarg1="value1")

    def test_retries_on_retryable_connection_error(self):
        """Test that function retries on connection errors"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("server disconnected")
            return "success"

        decorated = with_retry(max_attempts=3, initial_delay=0.01, exceptions=(ConnectionError,))(
            failing_func
        )

        with patch("time.sleep") as mock_sleep:
            result = decorated()

            assert result == "success"
            assert call_count == 3
            # Should have slept twice (before retry 2 and 3)
            assert mock_sleep.call_count == 2

    def test_respects_max_attempts(self):
        """Test that function fails after max_attempts"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("timeout error")

        decorated = with_retry(max_attempts=3, initial_delay=0.01, exceptions=(ConnectionError,))(
            failing_func
        )

        with patch("time.sleep"):
            with pytest.raises(ConnectionError, match="timeout error"):
                decorated()

            # Should have tried 3 times
            assert call_count == 3

    def test_exponential_backoff_calculation(self):
        """Test that delay increases exponentially"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ConnectionError("network error")
            return "success"

        decorated = with_retry(
            max_attempts=4, initial_delay=0.1, exponential_base=2.0, exceptions=(ConnectionError,)
        )(failing_func)

        with patch("time.sleep") as mock_sleep:
            result = decorated()

            assert result == "success"
            # Check exponential backoff: 0.1, 0.2, 0.4
            expected_delays = [0.1, 0.2, 0.4]
            actual_delays = [call_args[0][0] for call_args in mock_sleep.call_args_list]
            assert actual_delays == expected_delays

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ConnectionError("connection reset")
            return "success"

        decorated = with_retry(
            max_attempts=4,
            initial_delay=1.0,
            max_delay=1.5,
            exponential_base=2.0,
            exceptions=(ConnectionError,),
        )(failing_func)

        with patch("time.sleep") as mock_sleep:
            result = decorated()

            assert result == "success"
            # Delays should be: 1.0, min(2.0, 1.5)=1.5, min(4.0, 1.5)=1.5
            expected_delays = [1.0, 1.5, 1.5]
            actual_delays = [call_args[0][0] for call_args in mock_sleep.call_args_list]
            assert actual_delays == expected_delays

    def test_non_retryable_error_raises_immediately(self):
        """Test that non-retryable errors are not retried"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid value")

        decorated = with_retry(max_attempts=3, initial_delay=0.01, exceptions=(Exception,))(
            failing_func
        )

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(ValueError, match="invalid value"):
                decorated()

            # Should only try once since ValueError is not retryable
            assert call_count == 1
            # Should not sleep at all
            assert mock_sleep.call_count == 0

    def test_retryable_error_keywords(self):
        """Test that errors with retryable keywords are retried"""
        retryable_errors = [
            "server disconnected",
            "connection timeout",
            "network error",
            "remote protocol error",
            "broken pipe",
            "connection reset by peer",
        ]

        for error_msg in retryable_errors:
            call_count = 0

            def failing_func():
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise Exception(error_msg)
                return "success"

            decorated = with_retry(max_attempts=2, initial_delay=0.01)(failing_func)

            with patch("time.sleep"):
                result = decorated()

                assert result == "success"
                assert call_count == 2

    def test_case_insensitive_error_matching(self):
        """Test that error matching is case insensitive"""
        call_count = 0

        def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("SERVER DISCONNECTED")
            return "success"

        decorated = with_retry(max_attempts=2, initial_delay=0.01)(failing_func)

        with patch("time.sleep"):
            result = decorated()

            assert result == "success"
            assert call_count == 2

    def test_specific_exception_types(self):
        """Test retry only on specific exception types"""
        mock_func = Mock(side_effect=RuntimeError("some error"))

        decorated = with_retry(
            max_attempts=3, initial_delay=0.01, exceptions=(ConnectionError, TimeoutError)
        )

        # RuntimeError is not in the exceptions tuple, should raise immediately
        with pytest.raises(RuntimeError):
            decorated(mock_func)()

        assert mock_func.call_count == 1

    def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata"""

        def my_function():
            """My function docstring"""
            return "result"

        decorated = with_retry()(my_function)

        assert decorated.__name__ == "my_function"
        assert decorated.__doc__ == "My function docstring"

    def test_logging_on_retry(self, caplog):
        """Test that retry attempts are logged"""
        import logging

        mock_func = Mock(side_effect=[ConnectionError("timeout"), "success"])
        mock_func.__name__ = "test_func"

        decorated = with_retry(max_attempts=2, initial_delay=0.01, exceptions=(ConnectionError,))

        with patch("time.sleep"):
            with caplog.at_level(logging.WARNING):
                result = decorated(mock_func)()

                assert result == "success"
                # Check that retry was logged
                assert any(
                    "test_func" in record.message and "Retrying" in record.message
                    for record in caplog.records
                )

    def test_logging_on_max_attempts_reached(self, caplog):
        """Test that max attempts failure is logged"""
        import logging

        mock_func = Mock(side_effect=ConnectionError("timeout"))
        mock_func.__name__ = "test_func"

        decorated = with_retry(max_attempts=2, initial_delay=0.01, exceptions=(ConnectionError,))

        with patch("time.sleep"):
            with caplog.at_level(logging.ERROR):
                with pytest.raises(ConnectionError):
                    decorated(mock_func)()

                # Check that failure was logged
                assert any(
                    "test_func" in record.message and "failed after" in record.message
                    for record in caplog.records
                )

    def test_logging_non_retryable_error(self, caplog):
        """Test that non-retryable errors are logged"""
        import logging

        mock_func = Mock(side_effect=ValueError("bad value"))
        mock_func.__name__ = "test_func"

        decorated = with_retry(max_attempts=3, initial_delay=0.01)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ValueError):
                decorated(mock_func)()

            # Check that non-retryable error was logged
            assert any(
                "test_func" in record.message and "non-retryable" in record.message
                for record in caplog.records
            )


class TestWithAsyncRetry:
    """Test asynchronous retry decorator functionality"""

    @pytest.mark.asyncio
    async def test_successful_async_function_no_retry(self):
        """Test that successful async function executes without retries"""

        async def async_func(arg1, kwarg1=None):
            return "success"

        mock_func = Mock(side_effect=async_func)
        decorated = with_async_retry()(mock_func)

        result = await decorated("arg1", kwarg1="value1")

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_async_retries_on_connection_error(self):
        """Test that async function retries on connection errors"""
        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("server disconnected")
            return "success"

        decorated = with_async_retry(
            max_attempts=3, initial_delay=0.001, exceptions=(ConnectionError,)
        )(async_func)

        result = await decorated()

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_respects_max_attempts(self):
        """Test that async function fails after max_attempts"""
        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("timeout error")

        decorated = with_async_retry(
            max_attempts=3, initial_delay=0.001, exceptions=(ConnectionError,)
        )(async_func)

        with pytest.raises(ConnectionError, match="timeout error"):
            await decorated()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_exponential_backoff(self):
        """Test that async delay increases exponentially"""
        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ConnectionError("network error")
            return "success"

        decorated = with_async_retry(
            max_attempts=4, initial_delay=0.001, exponential_base=2.0, exceptions=(ConnectionError,)
        )(async_func)

        result = await decorated()

        assert result == "success"
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_async_max_delay_cap(self):
        """Test that async delay is capped at max_delay"""
        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ConnectionError("connection reset")
            return "success"

        decorated = with_async_retry(
            max_attempts=4,
            initial_delay=0.001,
            max_delay=0.002,
            exponential_base=2.0,
            exceptions=(ConnectionError,),
        )(async_func)

        result = await decorated()

        assert result == "success"
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_async_non_retryable_error_raises_immediately(self):
        """Test that async non-retryable errors are not retried"""
        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid value")

        decorated = with_async_retry(max_attempts=3, initial_delay=0.01, exceptions=(Exception,))(
            async_func
        )

        with patch(
            "asyncio.sleep", new_callable=lambda: Mock(side_effect=lambda x: asyncio.sleep(0))
        ):
            with pytest.raises(ValueError, match="invalid value"):
                await decorated()

            # Should only try once
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_retryable_error_keywords(self):
        """Test that async errors with retryable keywords are retried"""
        retryable_errors = [
            "server disconnected",
            "connection timeout",
            "network error",
            "remote protocol error",
            "broken pipe",
            "connection reset",
        ]

        for error_msg in retryable_errors:
            call_count = 0

            async def async_func():
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise Exception(error_msg)
                return "success"

            decorated = with_async_retry(max_attempts=2, initial_delay=0.001)(async_func)

            result = await decorated()

            assert result == "success"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_preserves_function_metadata(self):
        """Test that async decorator preserves function metadata"""

        async def my_async_function():
            """My async function docstring"""
            return "result"

        decorated = with_async_retry()(my_async_function)

        assert decorated.__name__ == "my_async_function"
        assert decorated.__doc__ == "My async function docstring"

    @pytest.mark.asyncio
    async def test_async_logging_on_retry(self, caplog):
        """Test that async retry attempts are logged"""
        import logging

        call_count = 0

        async def async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("timeout")
            return "success"

        async_func.__name__ = "test_async_func"

        decorated = with_async_retry(
            max_attempts=2, initial_delay=0.001, exceptions=(ConnectionError,)
        )(async_func)

        with caplog.at_level(logging.WARNING):
            result = await decorated()

            assert result == "success"
            # Check that retry was logged
            assert any(
                "test_async_func" in record.message and "Retrying" in record.message
                for record in caplog.records
            )

    @pytest.mark.asyncio
    async def test_async_specific_exception_types(self):
        """Test async retry only on specific exception types"""

        async def async_func():
            raise RuntimeError("some error")

        decorated = with_async_retry(
            max_attempts=3, initial_delay=0.01, exceptions=(ConnectionError, TimeoutError)
        )(async_func)

        call_count = 0

        async def counting_func():
            nonlocal call_count
            call_count += 1
            await async_func()

        decorated = with_async_retry(
            max_attempts=3, initial_delay=0.01, exceptions=(ConnectionError, TimeoutError)
        )(counting_func)

        # RuntimeError is not in the exceptions tuple, should raise immediately
        with pytest.raises(RuntimeError):
            await decorated()

        assert call_count == 1


class TestRetryIntegration:
    """Integration tests for retry utilities"""

    def test_sync_retry_with_real_timing(self):
        """Test sync retry with actual sleep delays (integration test)"""
        start_time = time.time()

        call_count = 0

        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("timeout")
            return "success"

        decorated = with_retry(
            max_attempts=3, initial_delay=0.05, exponential_base=2.0, exceptions=(ConnectionError,)
        )(flaky_func)

        result = decorated()
        elapsed = time.time() - start_time

        assert result == "success"
        assert call_count == 3
        # Should have slept approximately 0.05 + 0.1 = 0.15 seconds
        assert elapsed >= 0.15
        assert elapsed < 0.5  # Generous upper bound

    @pytest.mark.asyncio
    async def test_async_retry_with_real_timing(self):
        """Test async retry with actual sleep delays (integration test)"""
        start_time = time.time()

        call_count = 0

        async def flaky_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("timeout")
            return "success"

        decorated = with_async_retry(
            max_attempts=3, initial_delay=0.05, exponential_base=2.0, exceptions=(ConnectionError,)
        )(flaky_async_func)

        result = await decorated()
        elapsed = time.time() - start_time

        assert result == "success"
        assert call_count == 3
        # Should have slept approximately 0.05 + 0.1 = 0.15 seconds
        assert elapsed >= 0.15
        assert elapsed < 0.5  # Generous upper bound

    def test_default_parameters(self):
        """Test retry with default parameters"""
        call_count = 0

        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("connection timeout")
            return "success"

        # Use default parameters
        decorated = with_retry()(flaky_func)

        with patch("time.sleep"):
            result = decorated()

            assert result == "success"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_default_parameters(self):
        """Test async retry with default parameters"""
        call_count = 0

        async def flaky_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("connection timeout")
            return "success"

        # Use default parameters (but with short delays for fast tests)
        decorated = with_async_retry(initial_delay=0.001)(flaky_async_func)

        result = await decorated()

        assert result == "success"
        assert call_count == 2
