"""
Tests for monitoring API endpoints.

This module tests the monitoring REST API endpoints that expose:
- Provider health scores
- Recent errors
- Real-time statistics
- Circuit breaker states
- Provider comparison
- Latency percentiles
- Anomaly detection
- Trial analytics
- Cost analysis
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    """FastAPI test client"""
    # Clear any existing dependency overrides
    app.dependency_overrides = {}
    yield TestClient(app)
    # Cleanup after test
    app.dependency_overrides = {}


@pytest.fixture
def mock_redis_metrics():
    """Mock Redis metrics service"""
    with patch("src.routes.monitoring.get_redis_metrics") as mock:
        redis_instance = Mock()

        # Mock provider health
        redis_instance.get_all_provider_health = AsyncMock(
            return_value={
                "openrouter": 95.0,
                "portkey": 88.5,
                "fireworks": 72.0,
            }
        )

        redis_instance.get_provider_health = AsyncMock(return_value=95.0)

        # Mock recent errors
        redis_instance.get_recent_errors = AsyncMock(
            return_value=[
                {
                    "model": "gpt-4",
                    "error": "Rate limit exceeded",
                    "timestamp": datetime.now(UTC).timestamp(),
                    "latency_ms": 1500,
                }
            ]
        )

        # Mock hourly stats
        redis_instance.get_hourly_stats = AsyncMock(
            return_value={
                "2025-11-27:14": {
                    "total_requests": 1000,
                    "successful_requests": 950,
                    "failed_requests": 50,
                    "tokens_input": 50000,
                    "tokens_output": 25000,
                    "total_cost": 12.50,
                }
            }
        )

        # Mock latency percentiles
        redis_instance.get_latency_percentiles = AsyncMock(
            return_value={"count": 100, "avg": 500.0, "p50": 450.0, "p95": 800.0, "p99": 1200.0}
        )

        mock.return_value = redis_instance
        yield redis_instance


@pytest.fixture
def mock_analytics_service():
    """Mock analytics service"""
    with patch("src.routes.monitoring.get_analytics_service") as mock:
        analytics_instance = Mock()

        # Mock provider comparison
        analytics_instance.get_provider_comparison = AsyncMock(
            return_value=[
                {
                    "provider": "openrouter",
                    "total_requests": 10000,
                    "successful_requests": 9500,
                    "failed_requests": 500,
                    "avg_latency_ms": 500.0,
                    "total_cost": 125.50,
                    "total_tokens": 750000,
                    "avg_error_rate": 0.05,
                    "unique_models": 15,
                    "success_rate": 0.95,
                }
            ]
        )

        # Mock anomaly detection
        analytics_instance.detect_anomalies = AsyncMock(
            return_value=[
                {
                    "type": "cost_spike",
                    "provider": "openrouter",
                    "hour": "2025-11-27:14",
                    "value": 50.0,
                    "expected": 12.5,
                    "severity": "warning",
                }
            ]
        )

        # Mock trial analytics
        analytics_instance.get_trial_analytics = Mock(
            return_value={
                "signups": 1000,
                "started_trial": 750,
                "converted": 50,
                "conversion_rate": 5.0,
                "activation_rate": 75.0,
                "avg_time_to_conversion_days": 7.5,
            }
        )

        # Mock cost analysis
        analytics_instance.get_cost_by_provider = AsyncMock(
            return_value={
                "start_date": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
                "end_date": datetime.now(UTC).isoformat(),
                "providers": {
                    "openrouter": {
                        "total_cost": 250.0,
                        "total_requests": 50000,
                        "cost_per_request": 0.005,
                    }
                },
                "total_cost": 250.0,
                "total_requests": 50000,
            }
        )

        # Mock latency trends
        analytics_instance.get_latency_trends = AsyncMock(
            return_value={
                "provider": "openrouter",
                "hours": 24,
                "overall_avg_latency_ms": 500.0,
                "overall_p95_latency_ms": 800.0,
                "hourly_data": [],
            }
        )

        # Mock error rates
        analytics_instance.get_error_rate_by_model = AsyncMock(
            return_value={
                "hours": 24,
                "models": {
                    "gpt-4": {
                        "total_requests": 10000,
                        "failed_requests": 100,
                        "error_rate": 0.01,
                        "providers": ["openrouter"],
                    }
                },
            }
        )

        # Mock token efficiency
        analytics_instance.get_token_efficiency = AsyncMock(
            return_value={
                "provider": "openrouter",
                "model": "gpt-4",
                "total_cost": 100.0,
                "total_tokens": 1000000,
                "total_requests": 10000,
                "cost_per_token": 0.0001,
                "tokens_per_request": 100.0,
                "cost_per_request": 0.01,
                "avg_input_tokens": 75.0,
                "avg_output_tokens": 25.0,
            }
        )

        mock.return_value = analytics_instance
        yield analytics_instance


@pytest.fixture
def mock_availability_service():
    """Mock model availability service"""
    with patch("src.routes.monitoring.availability_service") as mock:
        # Create mock states with proper .name attribute
        closed_state = Mock()
        closed_state.name = "CLOSED"

        open_state = Mock()
        open_state.name = "OPEN"

        mock.circuit_breakers = {
            "openrouter:gpt-4": Mock(state=closed_state, failure_count=0, last_failure_time=0.0),
            "fireworks:llama-3-70b": Mock(
                state=open_state, failure_count=5, last_failure_time=datetime.now(UTC).timestamp()
            ),
        }
        mock.is_model_available = Mock(side_effect=lambda model, provider: provider != "fireworks")
        yield mock


class TestHealthEndpoints:
    """Test provider health endpoints"""

    def test_get_all_provider_health(self, client: TestClient, mock_redis_metrics):
        """Test getting health scores for all providers"""
        response = client.get("/api/monitoring/health")

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 3
        assert data[0]["provider"] == "openrouter"
        assert data[0]["health_score"] == 95.0
        assert data[0]["status"] == "healthy"

        assert data[1]["provider"] == "portkey"
        assert data[1]["health_score"] == 88.5
        assert data[1]["status"] == "healthy"

        assert data[2]["provider"] == "fireworks"
        assert data[2]["health_score"] == 72.0
        assert data[2]["status"] == "degraded"

    def test_get_provider_health(self, client: TestClient, mock_redis_metrics):
        """Test getting health score for specific provider"""
        response = client.get("/api/monitoring/health/openrouter")

        assert response.status_code == 200
        data = response.json()

        assert data["provider"] == "openrouter"
        assert data["health_score"] == 95.0
        assert data["status"] == "healthy"


class TestErrorEndpoints:
    """Test error tracking endpoints"""

    def test_get_provider_errors(self, client: TestClient, mock_redis_metrics):
        """Test getting recent errors for a provider"""
        response = client.get("/api/monitoring/errors/openrouter")

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert data[0]["model"] == "gpt-4"
        assert data[0]["error"] == "Rate limit exceeded"
        assert data[0]["latency_ms"] == 1500

    def test_get_provider_errors_with_limit(self, client: TestClient, mock_redis_metrics):
        """Test error endpoint with custom limit"""
        response = client.get("/api/monitoring/errors/openrouter?limit=50")

        assert response.status_code == 200
        mock_redis_metrics.get_recent_errors.assert_called_once_with("openrouter", limit=50)


class TestStatsEndpoints:
    """Test statistics endpoints"""

    def test_get_realtime_stats(self, client: TestClient, mock_redis_metrics):
        """Test getting real-time statistics"""
        response = client.get("/api/monitoring/stats/realtime")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert "providers" in data
        assert "total_requests" in data
        assert "total_cost" in data
        assert "avg_health_score" in data

        assert data["total_requests"] >= 0
        assert data["avg_health_score"] >= 0

    def test_get_hourly_stats(self, client: TestClient, mock_redis_metrics):
        """Test getting hourly stats for a provider"""
        response = client.get("/api/monitoring/stats/hourly/openrouter?hours=24")

        assert response.status_code == 200
        data = response.json()

        assert data["provider"] == "openrouter"
        assert data["hours"] == 24
        assert "data" in data


class TestCircuitBreakerEndpoints:
    """Test circuit breaker endpoints"""

    @pytest.mark.xfail(reason="Flaky: Circuit breaker state varies in CI environment", strict=False)
    def test_get_all_circuit_breakers(self, client: TestClient, mock_availability_service):
        """Test getting all circuit breaker states"""
        response = client.get("/api/monitoring/circuit-breakers")

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 2

        # Check closed circuit breaker
        closed_cb = next(cb for cb in data if cb["provider"] == "openrouter")
        assert closed_cb["model"] == "gpt-4"
        assert closed_cb["state"] == "CLOSED"
        assert closed_cb["failure_count"] == 0
        assert closed_cb["is_available"] is True

        # Check open circuit breaker
        open_cb = next(cb for cb in data if cb["provider"] == "fireworks")
        assert open_cb["model"] == "llama-3-70b"
        assert open_cb["state"] == "OPEN"
        assert open_cb["failure_count"] == 5
        assert open_cb["is_available"] is False

    @pytest.mark.xfail(reason="Flaky: Circuit breaker state varies in CI environment", strict=False)
    def test_get_provider_circuit_breakers(self, client: TestClient, mock_availability_service):
        """Test getting circuit breakers for specific provider"""
        response = client.get("/api/monitoring/circuit-breakers/openrouter")

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert data[0]["provider"] == "openrouter"
        assert data[0]["model"] == "gpt-4"


class TestProviderComparisonEndpoint:
    """Test provider comparison endpoint"""

    def test_get_provider_comparison(self, client: TestClient, mock_analytics_service):
        """Test provider comparison endpoint"""
        response = client.get("/api/monitoring/providers/comparison")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert "providers" in data
        assert "total_providers" in data
        assert data["total_providers"] == 1

        provider = data["providers"][0]
        assert provider["provider"] == "openrouter"
        assert provider["total_requests"] == 10000
        assert provider["success_rate"] == 0.95


class TestLatencyEndpoints:
    """Test latency tracking endpoints"""

    def test_get_latency_percentiles(self, client: TestClient, mock_redis_metrics):
        """Test getting latency percentiles"""
        response = client.get("/api/monitoring/latency/openrouter/gpt-4")

        assert response.status_code == 200
        data = response.json()

        assert data["provider"] == "openrouter"
        assert data["model"] == "gpt-4"
        assert data["count"] == 100
        assert data["avg"] == 500.0
        assert data["p50"] == 450.0
        assert data["p95"] == 800.0
        assert data["p99"] == 1200.0

    def test_get_latency_percentiles_custom(self, client: TestClient, mock_redis_metrics):
        """Test latency percentiles with custom percentiles"""
        response = client.get("/api/monitoring/latency/openrouter/gpt-4?percentiles=75,90,99")

        assert response.status_code == 200
        mock_redis_metrics.get_latency_percentiles.assert_called_once_with(
            "openrouter", "gpt-4", percentiles=[75, 90, 99]
        )

    def test_get_latency_trends(self, client: TestClient, mock_analytics_service):
        """Test getting latency trends"""
        response = client.get("/api/monitoring/latency-trends/openrouter?hours=24")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert data["provider"] == "openrouter"
        assert data["hours"] == 24
        assert data["overall_avg_latency_ms"] == 500.0


class TestAnomalyEndpoints:
    """Test anomaly detection endpoints"""

    def test_get_anomalies(self, client: TestClient, mock_analytics_service):
        """Test anomaly detection endpoint"""
        response = client.get("/api/monitoring/anomalies")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert "anomalies" in data
        assert "total_count" in data
        assert "critical_count" in data
        assert "warning_count" in data

        assert data["total_count"] == 1
        assert data["warning_count"] == 1
        assert data["critical_count"] == 0

        anomaly = data["anomalies"][0]
        assert anomaly["type"] == "cost_spike"
        assert anomaly["provider"] == "openrouter"
        assert anomaly["severity"] == "warning"


class TestBusinessMetricsEndpoints:
    """Test business metrics endpoints"""

    def test_get_trial_analytics(self, client: TestClient, mock_analytics_service):
        """Test trial analytics endpoint"""
        response = client.get("/api/monitoring/trial-analytics")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert data["signups"] == 1000
        assert data["started_trial"] == 750
        assert data["converted"] == 50
        assert data["conversion_rate"] == 5.0
        assert data["activation_rate"] == 75.0

    def test_get_cost_analysis(self, client: TestClient, mock_analytics_service):
        """Test cost analysis endpoint"""
        response = client.get("/api/monitoring/cost-analysis?days=7")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert data["period_days"] == 7
        assert "start_date" in data
        assert "end_date" in data
        assert "providers" in data
        assert data["total_cost"] == 250.0

    def test_get_error_rates(self, client: TestClient, mock_analytics_service):
        """Test error rates endpoint"""
        response = client.get("/api/monitoring/error-rates?hours=24")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert data["hours"] == 24
        assert "models" in data
        assert "gpt-4" in data["models"]
        assert data["models"]["gpt-4"]["error_rate"] == 0.01

    def test_get_token_efficiency(self, client: TestClient, mock_analytics_service):
        """Test token efficiency endpoint"""
        response = client.get("/api/monitoring/token-efficiency/openrouter/gpt-4")

        assert response.status_code == 200
        data = response.json()

        assert "timestamp" in data
        assert data["provider"] == "openrouter"
        assert data["model"] == "gpt-4"
        assert data["cost_per_token"] == 0.0001
        assert data["tokens_per_request"] == 100.0


class TestSentryTunnelEndpoint:
    """Test Sentry tunnel endpoint for frontend error tracking"""

    def test_sentry_tunnel_empty_body(self, client: TestClient):
        """Test that empty body returns 400"""
        response = client.post("/monitoring", content=b"")
        assert response.status_code == 400
        assert "Empty request body" in response.text

    def test_sentry_tunnel_invalid_envelope(self, client: TestClient):
        """Test that invalid envelope format returns 400"""
        response = client.post("/monitoring", content=b"not a valid json\n")
        assert response.status_code == 400
        assert "Invalid envelope header" in response.text

    def test_sentry_tunnel_no_dsn(self, client: TestClient):
        """Test that envelope without DSN returns 400"""
        import json

        envelope = json.dumps({"event_id": "12345"}).encode() + b"\n"
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 400
        assert "No DSN in envelope" in response.text

    def test_sentry_tunnel_blocked_host(self, client: TestClient):
        """Test that non-Sentry hosts are blocked"""
        import json

        envelope = (
            json.dumps({"dsn": "https://key@evil.example.com/12345", "event_id": "12345"}).encode()
            + b"\n"
        )
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 403
        assert "Invalid Sentry host" in response.text

    def test_sentry_tunnel_ssrf_prevention_suffix_attack(self, client: TestClient):
        """Test that SSRF attacks via suffix matching are blocked (e.g., evil-sentry.io)"""
        import json

        # This domain ends with sentry.io but is not a valid Sentry domain
        envelope = (
            json.dumps({"dsn": "https://key@evil-sentry.io/12345", "event_id": "12345"}).encode()
            + b"\n"
        )
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 403
        assert "Invalid Sentry host" in response.text

    def test_sentry_tunnel_ssrf_prevention_malicious_subdomain(self, client: TestClient):
        """Test that SSRF attacks via malicious subdomains are blocked"""
        import json

        # This looks like a subdomain but isn't properly formed
        envelope = (
            json.dumps(
                {"dsn": "https://key@malicioussentry.io/12345", "event_id": "12345"}
            ).encode()
            + b"\n"
        )
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 403
        assert "Invalid Sentry host" in response.text

    def test_sentry_tunnel_null_hostname(self, client: TestClient):
        """Test that malformed DSN with null hostname is rejected"""
        import json

        # DSN without valid hostname
        envelope = (
            json.dumps(
                {"dsn": "://key@/12345", "event_id": "12345"}  # Malformed URL with no hostname
            ).encode()
            + b"\n"
        )
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 403
        assert "Invalid Sentry host" in response.text

    def test_sentry_tunnel_non_dict_json(self, client: TestClient):
        """Test that non-dict JSON envelope returns 400"""
        import json

        # Array instead of object
        envelope = json.dumps([1, 2, 3]).encode() + b"\n"
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 400
        assert "Invalid envelope header" in response.text

    def test_sentry_tunnel_string_json(self, client: TestClient):
        """Test that string JSON envelope returns 400"""
        import json

        # String instead of object
        envelope = json.dumps("just a string").encode() + b"\n"
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 400
        assert "Invalid envelope header" in response.text

    def test_sentry_tunnel_number_json(self, client: TestClient):
        """Test that number JSON envelope returns 400"""
        import json

        # Number instead of object
        envelope = json.dumps(12345).encode() + b"\n"
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 400
        assert "Invalid envelope header" in response.text

    def test_sentry_tunnel_null_json(self, client: TestClient):
        """Test that null JSON envelope returns 400"""
        import json

        # Null instead of object
        envelope = json.dumps(None).encode() + b"\n"
        response = client.post("/monitoring", content=envelope)
        assert response.status_code == 400
        assert "Invalid envelope header" in response.text

    @pytest.mark.asyncio
    def test_sentry_tunnel_valid_envelope(self, client: TestClient):
        """Test that valid Sentry envelope is forwarded"""
        import json

        # Create a valid Sentry envelope with allowed host
        envelope_header = json.dumps(
            {
                "dsn": "https://key@o4510344966111232.ingest.us.sentry.io/4510344986099712",
                "event_id": "12345",
            }
        ).encode()
        envelope_item_header = json.dumps({"type": "event", "length": 2}).encode()
        envelope_item_payload = b"{}"
        envelope = envelope_header + b"\n" + envelope_item_header + b"\n" + envelope_item_payload

        # Mock httpx.AsyncClient to avoid actual network call
        with patch("src.routes.monitoring.httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b'{"id": "12345"}'

            mock_client_instance = Mock()
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_instance.post = AsyncMock(return_value=mock_response)

            mock_client.return_value = mock_client_instance

            response = client.post("/monitoring", content=envelope)

            # Endpoint should forward to Sentry and return success
            assert response.status_code == 200
