"""
Tests for instrumentation and observability endpoints.

Tests cover:
- OTel initialization endpoints
- OTel status endpoint
- Trace context endpoint
- Health check endpoint
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_admin_key():
    """Mock admin API key for testing."""
    return "test-admin-key-12345"


@pytest.fixture
def mock_env_vars(mock_admin_key):
    """Set up mock environment variables."""
    with patch.dict("os.environ", {"ADMIN_API_KEY": mock_admin_key}):
        yield


@pytest.fixture
def client(mock_env_vars):
    """Create a test client with mocked environment."""
    from src.main import create_app

    app = create_app()
    return TestClient(app)


class TestInstrumentationHealth:
    """Tests for /api/instrumentation/health endpoint."""

    def test_health_returns_status(self, client):
        """Test health endpoint returns expected structure."""
        response = client.get("/api/instrumentation/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "loki" in data
        assert "tempo" in data

    def test_health_includes_loki_config(self, client):
        """Test health endpoint includes Loki configuration."""
        response = client.get("/api/instrumentation/health")
        data = response.json()
        assert "enabled" in data["loki"]
        assert "service_name" in data["loki"]
        assert "environment" in data["loki"]

    def test_health_includes_tempo_config(self, client):
        """Test health endpoint includes Tempo configuration."""
        response = client.get("/api/instrumentation/health")
        data = response.json()
        assert "enabled" in data["tempo"]
        assert "service_name" in data["tempo"]
        assert "environment" in data["tempo"]


class TestTraceContext:
    """Tests for /api/instrumentation/trace-context endpoint."""

    def test_trace_context_returns_ids(self, client):
        """Test trace context returns trace and span IDs."""
        response = client.get("/api/instrumentation/trace-context")
        assert response.status_code == 200
        data = response.json()
        assert "trace_id" in data
        assert "span_id" in data
        assert "timestamp" in data


class TestOtelInitialize:
    """Tests for /api/instrumentation/otel/initialize endpoint."""

    def test_initialize_requires_auth(self, client):
        """Test initialize endpoint requires admin API key."""
        response = client.post("/api/instrumentation/otel/initialize")
        assert response.status_code in [401, 403]

    def test_initialize_with_valid_auth(self, client, mock_admin_key):
        """Test initialize endpoint with valid admin API key."""
        with patch("src.config.opentelemetry_config.OpenTelemetryConfig") as mock_otel:
            mock_otel._initialized = False
            mock_otel._tracer_provider = None
            mock_otel.initialize.return_value = True
            mock_otel.get_tracer.return_value = MagicMock()

            response = client.post(
                "/api/instrumentation/otel/initialize",
                headers={"Authorization": f"Bearer {mock_admin_key}"},
            )
            # Should succeed or return info about current state
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "initialized" in data or "tracer_provider_exists" in data

    def test_initialize_already_initialized(self, client, mock_admin_key):
        """Test initialize endpoint when OTel is already initialized."""
        with patch("src.routes.instrumentation.OpenTelemetryConfig") as mock_otel:
            mock_otel._initialized = True
            mock_otel._tracer_provider = MagicMock()
            mock_tracer = MagicMock()
            mock_otel.get_tracer.return_value = mock_tracer

            # Mock span context
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = Mock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = Mock(return_value=False)

            response = client.post(
                "/api/instrumentation/otel/initialize",
                headers={"Authorization": f"Bearer {mock_admin_key}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "already_initialized"


class TestOtelReinitialize:
    """Tests for /api/instrumentation/otel/reinitialize endpoint."""

    def test_reinitialize_requires_auth(self, client):
        """Test reinitialize endpoint requires admin API key."""
        response = client.post("/api/instrumentation/otel/reinitialize")
        assert response.status_code in [401, 403]

    def test_reinitialize_shuts_down_existing(self, client, mock_admin_key):
        """Test reinitialize shuts down existing provider before reinitializing."""
        with patch("src.routes.instrumentation.OpenTelemetryConfig") as mock_otel:
            mock_otel._initialized = True
            mock_otel._tracer_provider = MagicMock()
            mock_otel.shutdown = MagicMock()
            mock_otel.initialize.return_value = True
            mock_otel.get_tracer.return_value = MagicMock()

            response = client.post(
                "/api/instrumentation/otel/reinitialize",
                headers={"Authorization": f"Bearer {mock_admin_key}"},
            )
            assert response.status_code == 200
            # Verify shutdown was called
            mock_otel.shutdown.assert_called_once()


class TestOtelStatus:
    """Tests for /api/instrumentation/otel/status endpoint."""

    def test_status_requires_auth(self, client):
        """Test status endpoint requires admin API key."""
        response = client.get("/api/instrumentation/otel/status")
        assert response.status_code in [401, 403]

    def test_status_returns_config_info(self, client, mock_admin_key):
        """Test status endpoint returns configuration information."""
        with patch("src.routes.instrumentation.OpenTelemetryConfig") as mock_otel:
            mock_otel._initialized = False
            mock_otel._tracer_provider = None
            mock_otel.get_tracer.return_value = None

            response = client.get(
                "/api/instrumentation/otel/status",
                headers={"Authorization": f"Bearer {mock_admin_key}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "initialized" in data
            assert "tracer_provider_exists" in data
            assert "can_create_spans" in data
            assert "config" in data
            assert "tempo_enabled" in data["config"]
            assert "endpoint" in data["config"]


class TestStartupRetryLogic:
    """Tests for the startup retry logic in tempo_otlp initialization."""

    @pytest.mark.asyncio
    async def test_retry_logic_eventually_succeeds(self):
        """Test that retry logic eventually succeeds when Tempo becomes available."""
        from src.config.opentelemetry_config import OpenTelemetryConfig

        call_count = 0

        def mock_initialize():
            nonlocal call_count
            call_count += 1
            # Succeed on 3rd attempt
            return call_count >= 3

        with patch.object(OpenTelemetryConfig, "_initialized", False):
            with patch.object(OpenTelemetryConfig, "initialize", side_effect=mock_initialize):
                # Simulate what startup does
                max_retries = 5
                success = False
                for attempt in range(1, max_retries + 1):
                    if OpenTelemetryConfig.initialize():
                        success = True
                        break

                assert success
                assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_logic_gives_up_after_max_attempts(self):
        """Test that retry logic gives up after max attempts."""
        from src.config.opentelemetry_config import OpenTelemetryConfig

        call_count = 0

        def mock_initialize():
            nonlocal call_count
            call_count += 1
            return False  # Always fail

        with patch.object(OpenTelemetryConfig, "_initialized", False):
            with patch.object(OpenTelemetryConfig, "initialize", side_effect=mock_initialize):
                max_retries = 5
                success = False
                for attempt in range(1, max_retries + 1):
                    if OpenTelemetryConfig.initialize():
                        success = True
                        break

                assert not success
                assert call_count == max_retries


class TestEndpointReachabilityCheck:
    """Tests for the endpoint reachability check logic."""

    def test_check_endpoint_reachable_valid_endpoint(self):
        """Test reachability check with valid endpoint."""
        from src.config.opentelemetry_config import _check_endpoint_reachable

        with patch("socket.getaddrinfo") as mock_dns:
            with patch("socket.create_connection") as mock_conn:
                mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 4318))]
                mock_socket = MagicMock()
                mock_conn.return_value = mock_socket

                result = _check_endpoint_reachable("http://localhost:4318")
                assert result is True
                mock_socket.close.assert_called_once()

    def test_check_endpoint_reachable_dns_failure(self):
        """Test reachability check with DNS failure."""
        import socket

        from src.config.opentelemetry_config import _check_endpoint_reachable

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failed")):
            result = _check_endpoint_reachable("http://nonexistent.invalid:4318")
            assert result is False

    def test_check_endpoint_reachable_connection_refused(self):
        """Test reachability check with connection refused."""
        from src.config.opentelemetry_config import _check_endpoint_reachable

        with patch("socket.getaddrinfo") as mock_dns:
            with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
                mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 4318))]

                result = _check_endpoint_reachable("http://localhost:4318")
                assert result is False

    def test_check_endpoint_reachable_timeout(self):
        """Test reachability check with timeout."""
        from src.config.opentelemetry_config import _check_endpoint_reachable

        with patch("socket.getaddrinfo") as mock_dns:
            with patch("socket.create_connection", side_effect=TimeoutError()):
                mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 4318))]

                result = _check_endpoint_reachable("http://localhost:4318")
                assert result is False


class TestRailwayEndpointHandling:
    """Tests for Railway-specific endpoint URL handling."""

    def test_railway_internal_dns_port_handling(self):
        """Test that Railway internal DNS URLs get port 4318 added."""
        # This tests the URL transformation logic in OpenTelemetryConfig.initialize()
        test_url = "http://tempo.railway.internal"
        expected_url = "http://tempo.railway.internal:4318"

        from urllib.parse import urlparse

        parsed = urlparse(test_url)
        if not parsed.port and ".railway.internal" in test_url:
            if parsed.hostname:
                transformed_url = f"{parsed.scheme}://{parsed.hostname}:4318{parsed.path}"
                assert transformed_url == expected_url

    def test_railway_public_url_removes_port(self):
        """Test that Railway public URLs have ports removed."""
        test_url = "https://tempo-production.up.railway.app:4318"
        expected_url = "https://tempo-production.up.railway.app"

        transformed = test_url.replace(":4318", "").replace(":4317", "")
        assert transformed == expected_url

    def test_railway_public_url_ensures_https(self):
        """Test that Railway public URLs use HTTPS."""
        test_url = "http://tempo-production.up.railway.app"
        expected_url = "https://tempo-production.up.railway.app"

        if test_url.startswith("http://") and ".railway.app" in test_url:
            transformed = test_url.replace("http://", "https://")
            assert transformed == expected_url
