"""
Centralized Braintrust tracing service for Gatewayz Backend.

This module provides a centralized interface for Braintrust tracing that ensures
spans are properly associated with the project. The key fix is using
`logger.start_span()` instead of the standalone `start_span()` function.

Usage:
    from src.services.braintrust_service import (
        initialize_braintrust,
        create_span,
        flush,
        is_available,
    )

    # Initialize once at startup
    initialize_braintrust(project="Gatewayz Backend")

    # Create spans in request handlers
    span = create_span(name="chat_gpt-4", span_type="llm")
    span.log(input=..., output=..., metrics=...)
    span.end()
    flush()
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Global state for the Braintrust logger
_braintrust_logger = None
_braintrust_available = False
_project_name = None


def initialize_braintrust(project: str = "Gatewayz Backend") -> bool:
    """
    Initialize Braintrust logger and store it globally.

    This function MUST be called at application startup before any spans are created.
    The logger is stored globally so that `create_span()` can use `logger.start_span()`
    to ensure spans are properly associated with the project.

    Args:
        project: The Braintrust project name to log to

    Returns:
        True if initialization succeeded, False otherwise
    """
    global _braintrust_logger, _braintrust_available, _project_name

    try:
        # Validate API key before attempting initialization
        api_key = os.getenv("BRAINTRUST_API_KEY")
        if not api_key:
            logger.warning(
                "[Braintrust] BRAINTRUST_API_KEY environment variable not set. "
                "Braintrust tracing will be disabled."
            )
            _braintrust_available = False
            return False

        if not api_key.startswith("sk-"):
            logger.warning(
                "[Braintrust] BRAINTRUST_API_KEY does not start with 'sk-'. "
                "This may indicate an invalid API key."
            )

        from braintrust import init_logger

        # Initialize with async_flush=False for Railway serverless environment
        # This ensures logs are sent synchronously before the response returns
        _braintrust_logger = init_logger(
            project=project,
            async_flush=False,  # Critical for serverless environments like Railway
        )
        _braintrust_available = True
        _project_name = project

        logger.info(
            f"[Braintrust] Successfully initialized logger for project '{project}' "
            f"(async_flush=False for serverless compatibility)"
        )
        return True

    except ImportError as e:
        logger.warning(
            f"[Braintrust] Failed to import braintrust SDK: {e}. "
            "Install with: pip install braintrust"
        )
        _braintrust_available = False
        return False

    except Exception as e:
        logger.warning(
            f"[Braintrust] Initialization failed: {e}. " "Braintrust tracing will be disabled."
        )
        _braintrust_available = False
        return False


def get_logger():
    """
    Get the Braintrust logger instance.

    Returns:
        The Braintrust logger, or None if not initialized
    """
    return _braintrust_logger


def is_available() -> bool:
    """
    Check if Braintrust is available and properly configured.

    Returns:
        True if Braintrust is initialized and ready to use
    """
    return _braintrust_available and _braintrust_logger is not None


def get_project_name() -> str | None:
    """
    Get the name of the Braintrust project.

    Returns:
        The project name, or None if not initialized
    """
    return _project_name


def create_span(name: str, span_type: str = "llm", **kwargs) -> Any:
    """
    Create a span using logger.start_span() to ensure project association.

    This is the KEY FIX: Using `logger.start_span()` instead of the standalone
    `start_span()` function ensures the span is properly associated with the
    project that was initialized via `init_logger()`.

    Args:
        name: Name for the span (e.g., "chat_gpt-4")
        span_type: Type of span (default: "llm")
        **kwargs: Additional arguments passed to start_span()

    Returns:
        A Braintrust Span object, or NoopSpan if Braintrust is unavailable
    """
    global _braintrust_logger, _braintrust_available

    if not _braintrust_available or _braintrust_logger is None:
        logger.debug(f"[Braintrust] Returning NoopSpan for '{name}' - Braintrust not available")
        return NoopSpan()

    try:
        # KEY FIX: Use logger.start_span() instead of standalone start_span()
        # This ensures the span is associated with the correct project
        span = _braintrust_logger.start_span(name=name, type=span_type, **kwargs)
        logger.debug(f"[Braintrust] Created span: {name} (type={span_type})")
        return span
    except Exception as e:
        logger.warning(f"[Braintrust] Failed to create span '{name}': {e}. " "Returning NoopSpan.")
        return NoopSpan()


def flush() -> None:
    """
    Flush any pending logs to Braintrust.

    This should be called after logging span data to ensure it's sent
    to Braintrust before the request completes (especially important
    in serverless environments).
    """
    global _braintrust_logger

    if _braintrust_logger is None:
        return

    try:
        _braintrust_logger.flush()
        logger.debug("[Braintrust] Flushed pending logs")
    except Exception as e:
        logger.warning(f"[Braintrust] Failed to flush logs: {e}")


class NoopSpan:
    """
    No-operation span for when Braintrust is unavailable.

    This ensures code doesn't break when Braintrust is not configured,
    while still maintaining the same interface.
    """

    def log(self, *args, **kwargs) -> None:
        """No-op log method."""
        pass

    def end(self) -> None:
        """No-op end method."""
        pass

    def set_attributes(self, **kwargs) -> None:
        """No-op set_attributes method."""
        pass

    def __enter__(self) -> "NoopSpan":
        """Support context manager usage."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Support context manager usage."""
        return False


# Convenience function for checking availability in logging statements
def check_braintrust_available() -> bool:
    """Alias for is_available() for use in logging statements."""
    return is_available()
