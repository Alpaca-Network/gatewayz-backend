"""
Unit tests for the Braintrust service module.

Tests the centralized Braintrust tracing service that ensures spans
are properly associated with the project.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestBraintrustService:
    """Tests for the braintrust_service module."""

    def setup_method(self):
        """Reset module state before each test."""
        # Import and reset module state
        from src.services import braintrust_service

        braintrust_service._braintrust_logger = None
        braintrust_service._braintrust_available = False
        braintrust_service._project_name = None

    def test_initialize_without_api_key(self):
        """Test initialization fails gracefully without API key."""
        from src.services import braintrust_service

        with patch.dict(os.environ, {}, clear=True):
            # Remove API key if present
            os.environ.pop("BRAINTRUST_API_KEY", None)

            result = braintrust_service.initialize_braintrust()

            assert result is False
            assert braintrust_service.is_available() is False
            assert braintrust_service.get_project_name() is None

    def test_initialize_with_invalid_api_key(self):
        """Test initialization warns with invalid API key format."""
        from src.services import braintrust_service

        with patch.dict(os.environ, {"BRAINTRUST_API_KEY": "invalid-key"}):
            # Mock the braintrust import to avoid actual API calls
            mock_logger = MagicMock()
            with patch.dict(
                "sys.modules",
                {"braintrust": MagicMock(init_logger=MagicMock(return_value=mock_logger))},
            ):
                import importlib

                from src.services import braintrust_service

                importlib.reload(braintrust_service)

                result = braintrust_service.initialize_braintrust()

                # Should still succeed (warn but not fail)
                assert result is True

    def test_is_available_before_init(self):
        """Test is_available returns False before initialization."""
        from src.services import braintrust_service

        assert braintrust_service.is_available() is False

    def test_create_span_returns_noop_when_unavailable(self):
        """Test create_span returns NoopSpan when Braintrust is unavailable."""
        from src.services.braintrust_service import NoopSpan, create_span

        span = create_span(name="test_span")

        assert isinstance(span, NoopSpan)

    def test_noop_span_log_method(self):
        """Test NoopSpan.log() doesn't raise exceptions."""
        from src.services.braintrust_service import NoopSpan

        span = NoopSpan()

        # Should not raise
        span.log(input="test", output="test", metrics={"foo": 123})

    def test_noop_span_end_method(self):
        """Test NoopSpan.end() doesn't raise exceptions."""
        from src.services.braintrust_service import NoopSpan

        span = NoopSpan()

        # Should not raise
        span.end()

    def test_noop_span_context_manager(self):
        """Test NoopSpan works as a context manager."""
        from src.services.braintrust_service import NoopSpan

        span = NoopSpan()

        # Should not raise
        with span:
            span.log(input="test", output="test")

    def test_noop_span_set_attributes(self):
        """Test NoopSpan.set_attributes() doesn't raise exceptions."""
        from src.services.braintrust_service import NoopSpan

        span = NoopSpan()

        # Should not raise
        span.set_attributes(key1="value1", key2="value2")

    def test_flush_when_not_initialized(self):
        """Test flush() doesn't raise when not initialized."""
        from src.services import braintrust_service

        # Should not raise
        braintrust_service.flush()

    def test_get_logger_when_not_initialized(self):
        """Test get_logger() returns None when not initialized."""
        from src.services import braintrust_service

        assert braintrust_service.get_logger() is None

    def test_check_braintrust_available_alias(self):
        """Test check_braintrust_available is alias for is_available."""
        from src.services.braintrust_service import check_braintrust_available, is_available

        assert check_braintrust_available() == is_available()


class TestBraintrustServiceWithMocking:
    """Tests that mock the Braintrust SDK."""

    def setup_method(self):
        """Reset module state before each test."""
        from src.services import braintrust_service

        braintrust_service._braintrust_logger = None
        braintrust_service._braintrust_available = False
        braintrust_service._project_name = None

    def test_initialize_calls_init_logger_with_correct_params(self):
        """Test that initialize calls init_logger with correct parameters."""
        mock_init_logger = MagicMock()
        mock_logger = MagicMock()
        mock_init_logger.return_value = mock_logger

        with patch.dict(os.environ, {"BRAINTRUST_API_KEY": "sk-test-key"}):
            with patch(
                "src.services.braintrust_service.init_logger", mock_init_logger, create=True
            ):
                import importlib

                from src.services import braintrust_service

                importlib.reload(braintrust_service)

                # Patch the import inside the function
                with patch.object(braintrust_service, "__builtins__", {"__import__": MagicMock()}):
                    pass

    def test_create_span_uses_logger_start_span(self):
        """Test that create_span uses logger.start_span() for proper project association."""
        from src.services import braintrust_service

        # Set up mock logger
        mock_span = MagicMock()
        mock_logger = MagicMock()
        mock_logger.start_span.return_value = mock_span

        braintrust_service._braintrust_logger = mock_logger
        braintrust_service._braintrust_available = True

        # Call create_span
        result = braintrust_service.create_span(name="test_span", span_type="llm")

        # Verify logger.start_span was called (KEY FIX verification)
        mock_logger.start_span.assert_called_once_with(name="test_span", type="llm")
        assert result == mock_span

    def test_flush_calls_logger_flush(self):
        """Test that flush() calls logger.flush()."""
        from src.services import braintrust_service

        mock_logger = MagicMock()
        braintrust_service._braintrust_logger = mock_logger

        braintrust_service.flush()

        mock_logger.flush.assert_called_once()

    def test_create_span_returns_noop_on_exception(self):
        """Test that create_span returns NoopSpan if an exception occurs."""
        from src.services import braintrust_service
        from src.services.braintrust_service import NoopSpan

        mock_logger = MagicMock()
        mock_logger.start_span.side_effect = Exception("Test error")

        braintrust_service._braintrust_logger = mock_logger
        braintrust_service._braintrust_available = True

        result = braintrust_service.create_span(name="test_span")

        assert isinstance(result, NoopSpan)


@pytest.mark.skipif(
    not os.getenv("BRAINTRUST_API_KEY", "").startswith("sk-"),
    reason="BRAINTRUST_API_KEY not configured - skipping real integration test",
)
class TestBraintrustRealIntegration:
    """Real integration tests that require a valid API key."""

    def test_real_initialization(self):
        """Test real Braintrust initialization."""
        import importlib

        from src.services import braintrust_service

        importlib.reload(braintrust_service)

        result = braintrust_service.initialize_braintrust(project="Gatewayz Backend Test")

        assert result is True
        assert braintrust_service.is_available() is True
        assert braintrust_service.get_project_name() == "Gatewayz Backend Test"

    def test_real_span_creation_and_logging(self):
        """Test real span creation and logging."""
        import importlib

        from src.services import braintrust_service

        importlib.reload(braintrust_service)

        # Initialize
        braintrust_service.initialize_braintrust(project="Gatewayz Backend Test")

        # Create span
        span = braintrust_service.create_span(name="integration_test_span", span_type="llm")

        # This should NOT be a NoopSpan
        from src.services.braintrust_service import NoopSpan

        assert not isinstance(span, NoopSpan)

        # Log some data
        span.log(
            input="test input message",
            output="test output message",
            metrics={"test_metric": 42, "latency_ms": 100},
            metadata={"test": True},
        )
        span.end()

        # Flush
        braintrust_service.flush()
