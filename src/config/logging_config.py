"""
Logging configuration with Grafana Loki integration.

This module configures Python logging to send structured logs to Grafana Loki
while maintaining console logging for local development.

Features:
- Structured JSON logging with trace correlation
- Automatic Loki push integration via loki-logger-handler (non-blocking, batched)
- Environment-aware configuration
- Trace ID injection for log-to-trace correlation
"""

import logging
import sys

from src.config.config import Config

logger = logging.getLogger(__name__)


class GatewayZLokiHandler(logging.Handler):
    """
    Loki log handler that promotes dynamic LogRecord attributes to Loki stream labels.

    Wraps loki-logger-handler's LokiHandler, adding GatewayZ-specific label
    promotion: OTel trace/span IDs and per-request context (provider, model,
    path, user_id, error_type) are read from LogRecord attributes (set by
    TraceContextFilter and middleware) and merged into the Loki stream labels
    so dashboards can filter by {provider="openai"} or {trace_id="abc"}.
    """

    # Mapping of LogRecord attribute → Loki label name
    _DYNAMIC_LABEL_ATTRS = [
        ("trace_id", "trace_id"),
        ("span_id", "span_id"),
        ("request_path", "path"),
        ("http_method", "method"),
        ("provider", "provider"),
        ("model", "model"),
        ("user_id", "user_id"),
    ]

    def __init__(self, url: str, tags: dict, timeout: int = 5, compress: bool = False):
        super().__init__()
        try:
            from loki_logger_handler.handler import LokiHandler
            self._loki = LokiHandler(
                url=url,
                tags=tags,
                timeout=timeout,
                compress=compress,
            )
            self._loki_available = True
        except Exception as e:
            logger.warning(f"loki-logger-handler unavailable: {e}")
            self._loki_available = False

        self._base_tags = tags.copy()

    def emit(self, record: logging.LogRecord) -> None:
        if not self._loki_available:
            return
        try:
            # Collect dynamic labels from the LogRecord
            extra: dict[str, str] = {}
            for attr, label in self._DYNAMIC_LABEL_ATTRS:
                val = getattr(record, attr, None)
                if val:
                    extra[label] = str(val)
            if record.exc_info and record.exc_info[0]:
                extra["error_type"] = record.exc_info[0].__name__

            # Temporarily merge dynamic labels into the inner handler's tags
            if extra:
                self._loki.tags = {**self._base_tags, **extra}
            try:
                self._loki.emit(record)
            finally:
                self._loki.tags = self._base_tags.copy()
        except Exception:
            # Loki logging is best-effort — never crash the application
            pass

    def close(self) -> None:
        if self._loki_available:
            try:
                self._loki.close()
            except Exception:
                pass
        super().close()


class TraceContextFilter(logging.Filter):
    """
    Logging filter that adds trace context to log records.

    Enriches log records with OpenTelemetry trace and span IDs, enabling
    correlation between logs and traces in Grafana.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from src.config.opentelemetry_config import get_current_span_id, get_current_trace_id

            trace_id = get_current_trace_id()
            span_id = get_current_span_id()

            if trace_id:
                record.trace_id = trace_id
            if span_id:
                record.span_id = span_id

        except Exception:
            pass

        return True


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id
        if hasattr(record, "span_id"):
            log_data["span_id"] = record.span_id
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return json.dumps(log_data)


def configure_logging() -> bool:
    """
    Configure application logging with Loki integration.

    Sets up:
    - Console handler for local development
    - Loki handler for production (if LOKI_ENABLED=true)
    - Trace context filter for log-to-trace correlation
    - JSON formatting for structured logs

    Returns:
        bool: True if Loki integration was enabled, False otherwise
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    # Trace context filter (enriches all records with OTel trace/span IDs)
    root_logger.addFilter(TraceContextFilter())

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    if Config.IS_DEVELOPMENT:
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    else:
        console_handler.setFormatter(StructuredFormatter())
    root_logger.addHandler(console_handler)

    logger.info("📝 Console logging configured")

    # Loki handler (optional — enabled by LOKI_ENABLED=true)
    loki_enabled = False
    if Config.LOKI_ENABLED:
        try:
            loki_handler = GatewayZLokiHandler(
                url=Config.LOKI_PUSH_URL,
                tags={
                    "app": Config.OTEL_SERVICE_NAME,
                    "environment": Config.APP_ENV,
                    "service": "gatewayz-api",
                },
                timeout=5,
                compress=False,
            )
            loki_handler.setLevel(logging.INFO)
            loki_handler.setFormatter(StructuredFormatter())
            root_logger.addHandler(loki_handler)

            logger.info(f"✅ Loki logging enabled: {Config.LOKI_PUSH_URL}")
            loki_enabled = True

        except Exception as e:
            logger.warning(f"⚠️  Failed to configure Loki logging: {e}")

    else:
        logger.info("⏭️  Loki logging disabled (LOKI_ENABLED=false)")

    # Suppress noisy library loggers
    for lib in ("httpx", "httpcore", "urllib3", "opentelemetry"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    return loki_enabled
