"""
Structured logging configuration.

Configures Python logging with console output and JSON formatting for
production, plus trace ID injection for log-to-trace correlation.

(Log shipping to Grafana Loki is handled by the deployment platform's log
scraper; the in-process Loki push handler was removed as inert. The Loki
QUERY path used by the error monitor lives in
src/services/monitoring/error_monitor.py.)
"""

import logging
import sys

from src.config.config import Config

logger = logging.getLogger(__name__)


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Formats log records as JSON with trace context and additional metadata.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: LogRecord to format

        Returns:
            str: JSON-formatted log entry
        """
        import json

        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add trace context if available
        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id
        if hasattr(record, "span_id"):
            log_data["span_id"] = record.span_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return json.dumps(log_data)


def configure_logging() -> None:
    """
    Configure application logging.

    Sets up:
    - Console handler (simple format in development, JSON in production)
    - Trace context filter for log-to-trace correlation
    """
    import os

    _env = os.getenv("ENVIRONMENT", "development").lower()
    _default_level = "WARNING" if _env == "production" else "INFO"
    _level_name = os.getenv("LOG_LEVEL", _default_level).upper()
    _level = getattr(logging, _level_name, logging.INFO)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(_level)

    # Use simple format for console in development, JSON in production
    if Config.IS_DEVELOPMENT:
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    else:
        console_formatter = StructuredFormatter()

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    logger.info("📝 Console logging configured")

    # Set log levels for noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)
