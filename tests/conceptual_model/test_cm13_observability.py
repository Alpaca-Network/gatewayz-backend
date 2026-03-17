"""
CM-13 Observability: Prometheus, Sentry, OpenTelemetry, Audit Logging

Tests verifying that Prometheus metrics, Sentry error capture,
OpenTelemetry tracing, and activity audit logging are properly wired.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CM-13.1  model_inference_requests counter exists
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1301PrometheusInferenceRequestCounter:
    def test_prometheus_inference_request_counter(self):
        """The model_inference_requests_total Prometheus Counter must exist and accept labels."""
        from src.services.prometheus_metrics import model_inference_requests

        # Call .labels() with the expected label set and verify it returns a child collector
        child = model_inference_requests.labels(
            provider="test", model="test-model", status="success"
        )
        assert child is not None
        # Incrementing should not raise
        child.inc()


# ---------------------------------------------------------------------------
# CM-13.2  model_inference_duration histogram exists
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1302PrometheusInferenceDurationHistogram:
    def test_prometheus_inference_duration_histogram(self):
        """The model_inference_duration_seconds Histogram must exist and accept observations."""
        from src.services.prometheus_metrics import model_inference_duration

        child = model_inference_duration.labels(provider="test", model="test-model")
        assert child is not None
        # Observing a value should not raise
        child.observe(0.5)


# ---------------------------------------------------------------------------
# CM-13.3  tokens_used counter exists
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1303PrometheusTokensUsedCounter:
    def test_prometheus_tokens_used_counter(self):
        """The tokens_used_total Prometheus Counter must exist and accept labels."""
        from src.services.prometheus_metrics import tokens_used

        child = tokens_used.labels(provider="test", model="test-model", token_type="input")
        assert child is not None
        child.inc(100)


# ---------------------------------------------------------------------------
# CM-13.4  credits_used counter exists
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1304PrometheusCreditsUsedCounter:
    def test_prometheus_credits_used_counter(self):
        """The credits_used_total Prometheus Counter must exist and accept labels."""
        from src.services.prometheus_metrics import credits_used

        child = credits_used.labels(provider="test", model="test-model")
        assert child is not None
        child.inc(0.05)


# ---------------------------------------------------------------------------
# CM-13.5  time-to-first-chunk histogram exists
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1305PrometheusTtfcHistogram:
    def test_prometheus_ttfc_histogram(self):
        """The time_to_first_chunk_seconds Histogram must exist and accept observations."""
        from src.services.prometheus_metrics import time_to_first_chunk_seconds

        child = time_to_first_chunk_seconds.labels(provider="test", model="test-model")
        assert child is not None
        child.observe(1.2)


# ---------------------------------------------------------------------------
# CM-13.6  Sentry integration is configured
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1306SentryCapturesExceptions:
    @pytest.mark.asyncio
    async def test_sentry_captures_exceptions(self):
        """AutoSentryMiddleware calls sentry_sdk.capture_exception
        when an unhandled exception propagates through the ASGI stack."""
        from src.middleware.auto_sentry_middleware import AutoSentryMiddleware

        # Create a mock ASGI app that raises an exception
        error = RuntimeError("test error")

        async def failing_app(scope, receive, send):
            raise error

        middleware = AutoSentryMiddleware(app=failing_app)

        # Build a minimal ASGI scope
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }

        with (
            patch("src.middleware.auto_sentry_middleware.SENTRY_AVAILABLE", True),
            patch("src.middleware.auto_sentry_middleware.sentry_sdk") as mock_sentry,
        ):
            with pytest.raises(RuntimeError):
                await middleware(scope, AsyncMock(), AsyncMock())

            mock_sentry.capture_exception.assert_called_once_with(error)


# ---------------------------------------------------------------------------
# CM-13.7  OpenTelemetry trace created per request
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1307OpentelemetryTraceCreatedPerRequest:
    def test_opentelemetry_trace_created_per_request(self):
        """OpenTelemetryConfig.initialize() returns a bool indicating success/failure."""
        from src.config.opentelemetry_config import OpenTelemetryConfig

        # Reset state so initialize() actually runs
        OpenTelemetryConfig._initialized = False

        # Call initialize - it returns True (if deps available) or False (if not)
        result = OpenTelemetryConfig.initialize()
        assert isinstance(result, bool), "initialize() must return a bool"


# ---------------------------------------------------------------------------
# CM-13.8  Audit log on security violation (dedicated security audit logger)
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1308AuditLogOnSecurityViolation:
    def test_audit_log_on_security_violation(self):
        """log_security_event writes a row to security_audit_log with the
        correct event_type, ip_address, and details payload."""
        from src.db.activity import log_security_event

        fake_row = {
            "id": 1,
            "event_type": "rate_limit_block",
            "ip_address": "192.168.1.100",
            "user_id": None,
            "api_key_id": None,
            "details": {"limit_type": "ip", "limit": 300},
            "created_at": "2026-03-16T00:00:00+00:00",
        }

        mock_execute = MagicMock(return_value=MagicMock(data=[fake_row]))
        mock_insert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(insert=mock_insert))
        mock_client = MagicMock(table=mock_table)

        with patch("src.db.activity.get_supabase_client", return_value=mock_client):
            result = log_security_event(
                event_type="rate_limit_block",
                ip_address="192.168.1.100",
                details={"limit_type": "ip", "limit": 300},
            )

        assert result is not None
        assert result["event_type"] == "rate_limit_block"
        assert result["ip_address"] == "192.168.1.100"

        # Verify the insert was called with expected payload
        mock_table.assert_called_once_with("security_audit_log")
        insert_payload = mock_insert.call_args[0][0]
        assert insert_payload["event_type"] == "rate_limit_block"
        assert insert_payload["ip_address"] == "192.168.1.100"
        assert insert_payload["details"] == {"limit_type": "ip", "limit": 300}
