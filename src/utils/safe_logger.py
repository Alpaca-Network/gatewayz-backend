"""
Safe Logger - Auto-sanitizing logger wrapper

Automatically sanitizes sensitive data (API keys, user IDs, etc.) in log messages.
Eliminates 339+ manual sanitize_for_logging() calls across the codebase.

Usage:
    from src.utils.safe_logger import SafeLogger

    logger = SafeLogger(__name__)

    # Instead of:
    logger.info(f"User {sanitize_for_logging(user_id)} action {sanitize_for_logging(action)}")

    # Use:
    logger.info_safe("User action", user_id=user_id, action=action)  # Auto-sanitized!
"""

import logging
from typing import Any

from src.utils.security_validators import sanitize_for_logging


class SafeLogger:
    """
    Logger wrapper that automatically sanitizes sensitive data.

    All logging methods accept keyword arguments that are automatically
    sanitized before being logged.
    """

    def __init__(self, name: str):
        """
        Initialize safe logger.

        Args:
            name: Logger name (usually __name__)
        """
        self.logger = logging.getLogger(name)
        self._name = name

    def _sanitize_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize all keyword arguments for safe logging.

        Args:
            kwargs: Dictionary of values to sanitize

        Returns:
            Dictionary with sanitized values
        """
        return {
            key: sanitize_for_logging(value)
            for key, value in kwargs.items()
        }

    def debug(self, message: str, **kwargs):
        """
        Log debug message with auto-sanitized kwargs.

        Args:
            message: Log message
            **kwargs: Additional context (auto-sanitized)
        """
        safe_kwargs = self._sanitize_kwargs(kwargs)
        self.logger.debug(message, extra=safe_kwargs)

    def info(self, message: str, **kwargs):
        """
        Log info message with auto-sanitized kwargs.

        Args:
            message: Log message
            **kwargs: Additional context (auto-sanitized)
        """
        safe_kwargs = self._sanitize_kwargs(kwargs)
        self.logger.info(message, extra=safe_kwargs)

    def warning(self, message: str, **kwargs):
        """
        Log warning message with auto-sanitized kwargs.

        Args:
            message: Log message
            **kwargs: Additional context (auto-sanitized)
        """
        safe_kwargs = self._sanitize_kwargs(kwargs)
        self.logger.warning(message, extra=safe_kwargs)

    def error(self, message: str, exc: Exception | None = None, **kwargs):
        """
        Log error message with auto-sanitized kwargs.

        Args:
            message: Log message
            exc: Optional exception for exc_info
            **kwargs: Additional context (auto-sanitized)
        """
        safe_kwargs = self._sanitize_kwargs(kwargs)
        self.logger.error(message, exc_info=exc, extra=safe_kwargs)

    def critical(self, message: str, exc: Exception | None = None, **kwargs):
        """
        Log critical message with auto-sanitized kwargs.

        Args:
            message: Log message
            exc: Optional exception for exc_info
            **kwargs: Additional context (auto-sanitized)
        """
        safe_kwargs = self._sanitize_kwargs(kwargs)
        self.logger.critical(message, exc_info=exc, extra=safe_kwargs)

    # Convenience methods with explicit sanitization naming
    def debug_safe(self, message: str, **kwargs):
        """Alias for debug() - explicitly shows sanitization."""
        return self.debug(message, **kwargs)

    def info_safe(self, message: str, **kwargs):
        """Alias for info() - explicitly shows sanitization."""
        return self.info(message, **kwargs)

    def warning_safe(self, message: str, **kwargs):
        """Alias for warning() - explicitly shows sanitization."""
        return self.warning(message, **kwargs)

    def error_safe(self, message: str, exc: Exception | None = None, **kwargs):
        """Alias for error() - explicitly shows sanitization."""
        return self.error(message, exc=exc, **kwargs)

    # Standard logging interface (pass-through without sanitization)
    # Use these when you've already sanitized or for non-sensitive data
    def debug_raw(self, message: str, *args, **kwargs):
        """Debug without sanitization (use when already sanitized)."""
        self.logger.debug(message, *args, **kwargs)

    def info_raw(self, message: str, *args, **kwargs):
        """Info without sanitization (use when already sanitized)."""
        self.logger.info(message, *args, **kwargs)

    def warning_raw(self, message: str, *args, **kwargs):
        """Warning without sanitization (use when already sanitized)."""
        self.logger.warning(message, *args, **kwargs)

    def error_raw(self, message: str, *args, **kwargs):
        """Error without sanitization (use when already sanitized)."""
        self.logger.error(message, *args, **kwargs)

    # Property access to underlying logger
    @property
    def name(self) -> str:
        """Get logger name."""
        return self._name

    @property
    def level(self) -> int:
        """Get logger level."""
        return self.logger.level

    def setLevel(self, level: int):
        """Set logger level."""
        self.logger.setLevel(level)

    def addHandler(self, handler: logging.Handler):
        """Add handler to logger."""
        self.logger.addHandler(handler)

    def removeHandler(self, handler: logging.Handler):
        """Remove handler from logger."""
        self.logger.removeHandler(handler)


def get_safe_logger(name: str) -> SafeLogger:
    """
    Factory function to get a safe logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        SafeLogger instance
    """
    return SafeLogger(name)


# Example usage:
if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.DEBUG)

    # Create safe logger
    logger = SafeLogger(__name__)

    # These automatically sanitize sensitive data
    logger.info_safe("User login", user_id="12345", api_key="sk-1234567890")
    logger.debug_safe("Database query", table="users", query="SELECT * FROM users WHERE id = 1")
    logger.error_safe("Operation failed", error="Connection timeout", user="admin@example.com")

    # Output will have sanitized values (masked/truncated)
