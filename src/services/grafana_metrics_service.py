"""
Grafana Metrics Service with Synthetic Data Fallback.

This service provides metrics for Grafana dashboards (Prometheus, Loki, Tempo)
with automatic synthetic data generation when Supabase is unavailable.

Features:
- Real metrics from application when available
- Synthetic data fallback for development/testing
- Compatible with Grafana FastAPI Dashboard
- Structured JSON logging for Loki
- OpenTelemetry trace integration for Tempo
"""

import logging
import os
import random
import time
from datetime import UTC, datetime
from typing import Any

from prometheus_client import (
    REGISTRY,
    generate_latest,
)

logger = logging.getLogger(__name__)

# Environment configuration
APP_NAME = os.environ.get("APP_NAME", "gatewayz")
ENVIRONMENT = os.environ.get("ENVIRONMENT", os.environ.get("APP_ENV", "development"))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "gatewayz-api")


class GrafanaMetricsService:
    """
    Service for managing Grafana-compatible metrics with synthetic data fallback.
    """

    def __init__(self):
        self._supabase_available = None
        self._last_supabase_check = 0
        self._supabase_check_interval = 60  # Check every 60 seconds
        self._synthetic_mode = False

    def _check_supabase_availability(self) -> bool:
        """
        Check if Supabase is available for real metrics.
        Caches result for performance.
        """
        current_time = time.time()

        # Use cached result if recent
        if (
            self._supabase_available is not None
            and current_time - self._last_supabase_check < self._supabase_check_interval
        ):
            return self._supabase_available

        try:
            from src.config.supabase_config import get_supabase_client

            client = get_supabase_client()
            # Simple health check query
            client.table("users").select("id").limit(1).execute()
            self._supabase_available = True
            self._synthetic_mode = False
            logger.debug("Supabase connection verified")
        except Exception as e:
            logger.warning(f"Supabase unavailable, using synthetic data: {e}")
            self._supabase_available = False
            self._synthetic_mode = True

        self._last_supabase_check = current_time
        return self._supabase_available

    def is_synthetic_mode(self) -> bool:
        """Check if service is running in synthetic data mode."""
        self._check_supabase_availability()
        return self._synthetic_mode

    def get_prometheus_metrics(self) -> bytes:
        """
        Get Prometheus metrics in text format.

        Returns:
            bytes: Prometheus metrics in text exposition format
        """
        # Inject synthetic data if needed
        if self.is_synthetic_mode():
            self._inject_synthetic_metrics()

        return generate_latest(REGISTRY)

    def _inject_synthetic_metrics(self):
        """
        Inject synthetic metrics for development/testing when Supabase is unavailable.
        This ensures Grafana dashboards have data to display.
        """
        from src.services.prometheus_metrics import (
            active_connections,
            cache_hits,
            cache_misses,
            fastapi_requests_duration_seconds,
            fastapi_requests_in_progress,
            fastapi_requests_total,
            model_inference_duration,
            model_inference_requests,
            provider_availability,
            provider_error_rate,
            tokens_used,
        )

        # Synthetic request data for common endpoints
        endpoints = [
            ("/v1/chat/completions", "POST"),
            ("/v1/models", "GET"),
            ("/health", "GET"),
            ("/api/users", "GET"),
            ("/metrics", "GET"),
        ]

        status_codes = [200, 200, 200, 200, 201, 400, 500]  # Weighted towards success

        for path, method in endpoints:
            # Increment request counter with random status
            status = random.choice(status_codes)
            try:
                if 200 <= status < 300:
                    _status_class = "2xx"
                elif 400 <= status < 500:
                    _status_class = "4xx"
                elif 500 <= status < 600:
                    _status_class = "5xx"
                else:
                    _status_class = "other"
                fastapi_requests_total.labels(
                    app_name=APP_NAME,
                    method=method,
                    path=path,
                    status_code=status,
                    status_class=_status_class,
                ).inc(random.randint(1, 10))
            except Exception:
                pass

            # Record duration histogram
            try:
                duration = random.uniform(0.01, 2.0)
                fastapi_requests_duration_seconds.labels(
                    app_name=APP_NAME, method=method, path=path
                ).observe(duration)
            except Exception:
                pass

        # Synthetic in-progress requests
        try:
            fastapi_requests_in_progress.labels(
                app_name=APP_NAME, method="POST", path="/v1/chat/completions"
            ).set(random.randint(0, 5))
        except Exception:
            pass

        # Synthetic model inference metrics
        providers = ["openai", "anthropic", "google", "openrouter", "fireworks"]
        models = ["gpt-4", "claude-3", "gemini-pro", "llama-3", "mixtral"]

        for provider in providers:
            for model in models:
                try:
                    # Inference requests
                    model_inference_requests.labels(
                        provider=provider, model=model, status="success"
                    ).inc(random.randint(0, 5))

                    # Inference duration
                    model_inference_duration.labels(provider=provider, model=model).observe(
                        random.uniform(0.5, 10.0)
                    )

                    # Token usage
                    tokens_used.labels(provider=provider, model=model, token_type="input").inc(
                        random.randint(100, 2000)
                    )
                    tokens_used.labels(provider=provider, model=model, token_type="output").inc(
                        random.randint(50, 1000)
                    )
                except Exception:
                    pass

        # Synthetic provider health
        for provider in providers:
            try:
                provider_availability.labels(provider=provider).set(
                    1 if random.random() > 0.1 else 0
                )
                provider_error_rate.labels(provider=provider).set(random.uniform(0, 0.1))
            except Exception:
                pass

        # Synthetic cache metrics
        cache_names = ["response_cache", "model_catalog", "health_cache"]
        for cache_name in cache_names:
            try:
                cache_hits.labels(cache_name=cache_name).inc(random.randint(10, 100))
                cache_misses.labels(cache_name=cache_name).inc(random.randint(1, 20))
            except Exception:
                pass

        # Synthetic connection metrics
        try:
            active_connections.labels(connection_type="http").set(random.randint(5, 50))
            active_connections.labels(connection_type="websocket").set(random.randint(0, 10))
        except Exception:
            pass

        logger.debug("Synthetic metrics injected")

    def get_metrics_summary(self) -> dict[str, Any]:
        """
        Get a summary of current metrics state.

        Returns:
            dict: Summary of metrics including mode and key values
        """
        return {
            "status": "healthy",
            "mode": "synthetic" if self.is_synthetic_mode() else "live",
            "supabase_available": self._supabase_available,
            "app_name": APP_NAME,
            "environment": ENVIRONMENT,
            "service_name": SERVICE_NAME,
            "timestamp": datetime.now(UTC).isoformat(),
            "metrics_endpoint": "/metrics",
            "grafana_compatible": True,
            "supported_dashboards": [
                "FastAPI Observability (ID: 16110)",
                "Custom GatewayZ Dashboard",
            ],
        }

    def get_structured_log_entry(
        self,
        level: str,
        message: str,
        endpoint: str = "",
        method: str = "",
        status_code: int = 0,
        duration_ms: int = 0,
        extra: dict | None = None,
    ) -> dict[str, Any]:
        """
        Create a structured log entry for Loki ingestion.

        Args:
            level: Log level (INFO, ERROR, WARNING, DEBUG)
            message: Log message
            endpoint: Request endpoint path
            method: HTTP method
            status_code: Response status code
            duration_ms: Request duration in milliseconds
            extra: Additional fields to include

        Returns:
            dict: Structured log entry compatible with Loki
        """
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level.upper(),
            "service": SERVICE_NAME,
            "environment": ENVIRONMENT,
            "message": message,
        }

        if endpoint:
            log_entry["endpoint"] = endpoint
        if method:
            log_entry["method"] = method
        if status_code:
            log_entry["status_code"] = status_code
        if duration_ms:
            log_entry["duration_ms"] = duration_ms

        # Add trace context if available
        try:
            from src.config.opentelemetry_config import (
                get_current_span_id,
                get_current_trace_id,
            )

            trace_id = get_current_trace_id()
            span_id = get_current_span_id()
            if trace_id:
                log_entry["trace_id"] = trace_id
            if span_id:
                log_entry["span_id"] = span_id
        except Exception:
            pass

        if extra:
            log_entry.update(extra)

        return log_entry


# Singleton instance
grafana_metrics_service = GrafanaMetricsService()
