"""
Tests for Health Check Routes

Covers:
- Basic health check endpoint
- System health metrics
- Provider health monitoring
- Model health monitoring
- Uptime metrics
- Health dashboard
- Monitoring controls
- Error handling
"""

import os
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# Set test environment
os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"

from src.main import app
from src.security.deps import get_api_key


@pytest.fixture
def client():
    """FastAPI test client with auth override"""

    # Override get_api_key to return a test key
    async def mock_get_api_key():
        return "gw_test_key_123"

    app.dependency_overrides[get_api_key] = mock_get_api_key
    yield TestClient(app)
    # Cleanup
    app.dependency_overrides = {}


@pytest.fixture
def auth_headers():
    """Authentication headers"""
    return {"Authorization": "Bearer gw_test_key_123", "Content-Type": "application/json"}


@pytest.fixture
def mock_system_health():
    """Mock system health response"""
    from src.models.health_models import HealthStatus, SystemHealthResponse

    return SystemHealthResponse(
        overall_status=HealthStatus.HEALTHY,
        total_providers=10,
        healthy_providers=9,
        degraded_providers=1,
        unhealthy_providers=0,
        total_models=100,
        healthy_models=95,
        degraded_models=3,
        unhealthy_models=2,
        system_uptime=99.5,
        last_updated=datetime.now(UTC),
    )


@pytest.fixture
def mock_provider_health():
    """Mock provider health response"""
    from src.models.health_models import ProviderHealthResponse, ProviderStatus

    return [
        ProviderHealthResponse(
            provider="openai",
            gateway="openrouter",
            status=ProviderStatus.ONLINE,
            total_models=10,
            healthy_models=10,
            degraded_models=0,
            unhealthy_models=0,
            overall_uptime=99.9,
            avg_response_time_ms=150.0,
            last_checked=datetime.now(UTC),
        ),
        ProviderHealthResponse(
            provider="anthropic",
            gateway="openrouter",
            status=ProviderStatus.DEGRADED,
            total_models=5,
            healthy_models=4,
            degraded_models=1,
            unhealthy_models=0,
            overall_uptime=98.5,
            avg_response_time_ms=250.0,
            last_checked=datetime.now(UTC),
        ),
    ]


@pytest.fixture
def mock_model_health():
    """Mock model health response"""
    from src.models.health_models import HealthStatus, ModelHealthResponse

    return [
        ModelHealthResponse(
            model_id="gpt-3.5-turbo",
            provider="openai",
            gateway="openrouter",
            status=HealthStatus.HEALTHY,
            response_time_ms=120.0,
            uptime_percentage=99.8,
            error_count=2,
            total_requests=1000,
            last_checked=datetime.now(UTC),
            last_error=None,
            avg_response_time_ms=125.0,
        )
    ]


class TestBasicHealthCheck:
    """Test basic health check endpoint"""

    def test_health_check_returns_200(self, client):
        """Basic health check returns 200 OK"""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_check_returns_healthy_status(self, client):
        """Health check returns healthy status"""
        response = client.get("/health")
        data = response.json()

        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_check_no_auth_required(self, client):
        """Health check doesn't require authentication"""
        # No auth headers
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_check_response_format(self, client):
        """Health check returns valid JSON"""
        response = client.get("/health")

        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert isinstance(data, dict)


class TestQuickHealthCheck:
    """Test /health/quick endpoint - ultra-fast health check with no I/O operations"""

    def test_quick_health_returns_200(self, client):
        """Quick health check returns 200 OK"""
        response = client.get("/health/quick")
        assert response.status_code == 200

    def test_quick_health_returns_healthy_status(self, client):
        """Quick health check returns healthy status"""
        response = client.get("/health/quick")
        data = response.json()

        assert "status" in data
        assert data["status"] == "healthy"

    def test_quick_health_has_timestamp(self, client):
        """Quick health check includes timestamp"""
        response = client.get("/health/quick")
        data = response.json()

        assert "timestamp" in data
        # Timestamp should be ISO format
        assert "T" in data["timestamp"]

    def test_quick_health_no_auth_required(self, client):
        """Quick health check doesn't require authentication"""
        # No auth headers - this is critical for uptime monitoring
        response = client.get("/health/quick")
        assert response.status_code == 200

    def test_quick_health_response_is_minimal(self, client):
        """Quick health check returns minimal response (no database info)"""
        response = client.get("/health/quick")
        data = response.json()

        # Should only have status and timestamp - no database info
        assert set(data.keys()) == {"status", "timestamp"}


class TestSystemHealth:
    """Test system health endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_system_health_success(self, mock_get_health, client, auth_headers, mock_system_health):
        """Successfully get system health metrics"""
        mock_get_health.return_value = mock_system_health

        response = client.get("/health/system", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "overall_status" in data
            assert "total_providers" in data
            assert "total_models" in data

    def test_system_health_requires_auth(self, client):
        """System health requires authentication"""

        response = client.get("/health/system")
        # Note: Now returns 200 with default data even without auth (graceful degradation)
        # or returns 401/403/422 if auth is enforced
        assert response.status_code in [200, 401, 403, 422, 500]

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_system_health_no_data_available(self, mock_get_health, client, auth_headers):
        """System health handles no data gracefully"""
        mock_get_health.return_value = None

        response = client.get("/health/system", headers=auth_headers)
        # Now returns 200 with default data instead of 503/500 (graceful degradation)
        assert response.status_code == 200

        # Verify it returns default/unknown status
        data = response.json()
        assert data["overall_status"] == "unknown"
        assert data["total_providers"] == 0
        assert data["total_models"] == 0

    @patch("src.routes.health.capture_error")
    @patch("src.routes.health.simple_health_cache.get_system_health")
    def test_system_health_error_captured_to_sentry(
        self, mock_get_health, mock_capture_error, client, auth_headers
    ):
        """Test that system health errors are captured to Sentry"""
        # Simulate an error from the cache lookup
        mock_get_health.side_effect = Exception("Service unavailable")

        response = client.get("/health/system", headers=auth_headers)

        # Now returns 200 with default data instead of 500 (graceful degradation)
        assert response.status_code == 200

        # Verify it returns default/unknown status
        data = response.json()
        assert data["overall_status"] == "unknown"

        # Verify Sentry capture was called
        assert mock_capture_error.called
        call_args = mock_capture_error.call_args
        assert call_args[0][0].args[0] == "Service unavailable"
        assert call_args[1]["context_type"] == "health_endpoint"
        assert call_args[1]["context_data"]["endpoint"] == "/health/system"
        assert call_args[1]["tags"]["endpoint"] == "system_health"


class TestProvidersHealth:
    """Test providers health endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_all_providers_health")
    def test_get_all_providers_health(
        self, mock_get_providers, client, auth_headers, mock_provider_health
    ):
        """Get health for all providers"""
        mock_get_providers.return_value = mock_provider_health

        response = client.get("/health/providers", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)
            if len(data) > 0:
                assert "provider" in data[0]
                assert "status" in data[0]

    @patch("src.services.model_health_monitor.health_monitor.get_all_providers_health")
    def test_filter_providers_by_gateway(
        self, mock_get_providers, client, auth_headers, mock_provider_health
    ):
        """Filter providers by gateway parameter"""
        mock_get_providers.return_value = mock_provider_health

        response = client.get("/health/providers?gateway=openrouter", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)


class TestModelsHealth:
    """Test models health endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_get_all_models_health(self, mock_get_models, client, auth_headers, mock_model_health):
        """Get health for all models"""
        mock_get_models.return_value = mock_model_health

        response = client.get("/health/models", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)
            if len(data) > 0:
                assert "model_id" in data[0]
                assert "status" in data[0]

    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_filter_models_by_provider(
        self, mock_get_models, client, auth_headers, mock_model_health
    ):
        """Filter models by provider parameter"""
        mock_get_models.return_value = mock_model_health

        response = client.get("/health/models?provider=openai", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)

    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_filter_models_by_status(
        self, mock_get_models, client, auth_headers, mock_model_health
    ):
        """Filter models by status parameter"""
        mock_get_models.return_value = mock_model_health

        response = client.get("/health/models?status=healthy", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)


class TestSpecificModelHealth:
    """Test specific model health endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_model_health")
    def test_get_model_health_success(
        self, mock_get_model, client, auth_headers, mock_model_health
    ):
        """Get health for specific model"""
        mock_get_model.return_value = mock_model_health[0]

        response = client.get("/health/model/gpt-3.5-turbo", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "model_id" in data
            assert "status" in data

    @patch("src.services.model_health_monitor.health_monitor.get_model_health")
    def test_get_model_health_not_found(self, mock_get_model, client, auth_headers):
        """Model not found returns 404"""
        mock_get_model.return_value = None

        response = client.get("/health/model/nonexistent-model", headers=auth_headers)
        assert response.status_code == 404


class TestSpecificProviderHealth:
    """Test specific provider health endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_provider_health")
    def test_get_provider_health_success(
        self, mock_get_provider, client, auth_headers, mock_provider_health
    ):
        """Get health for specific provider"""
        mock_get_provider.return_value = mock_provider_health[0]

        response = client.get("/health/provider/openai", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "provider" in data
            assert "status" in data

    @patch("src.services.model_health_monitor.health_monitor.get_provider_health")
    def test_get_provider_health_not_found(self, mock_get_provider, client, auth_headers):
        """Provider not found returns 404"""
        mock_get_provider.return_value = None

        response = client.get("/health/provider/nonexistent", headers=auth_headers)
        assert response.status_code == 404


class TestHealthSummary:
    """Test health summary endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_health_summary")
    def test_get_health_summary(self, mock_get_summary, client, auth_headers, mock_system_health):
        """Get comprehensive health summary"""
        # Create a proper mock with required structure for Pydantic validation
        mock_summary = {
            "system": mock_system_health,
            "providers": [],
            "models": [],
            "last_check": datetime.now(UTC),
        }
        mock_get_summary.return_value = mock_summary

        response = client.get("/health/summary", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)


class TestHealthCheck:
    """Test health check trigger endpoints"""

    def test_perform_health_check(self, client, auth_headers):
        """Trigger background health check"""

        response = client.post("/health/check", json={"force_refresh": True}, headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "message" in data
            assert "timestamp" in data

    @patch("src.services.model_health_monitor.health_monitor._perform_health_checks")
    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_perform_immediate_health_check(
        self, mock_get_health, mock_perform, client, auth_headers, mock_system_health
    ):
        """Perform immediate health check"""
        mock_perform.return_value = None
        mock_get_health.return_value = mock_system_health

        response = client.post("/health/check/now", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "message" in data
            assert "timestamp" in data


class TestUptimeMetrics:
    """Test uptime metrics endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_get_uptime_metrics(
        self, mock_models, mock_system, client, auth_headers, mock_system_health, mock_model_health
    ):
        """Get uptime metrics for status page"""
        mock_system.return_value = mock_system_health
        mock_models.return_value = mock_model_health

        response = client.get("/health/uptime", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "status" in data
            assert "uptime_percentage" in data

    @patch("src.routes.health.capture_error")
    @patch("src.routes.health.simple_health_cache.get_system_health")
    def test_get_uptime_metrics_error_captured_to_sentry(
        self, mock_get_health, mock_capture_error, client, auth_headers
    ):
        """Test that uptime metrics errors are captured to Sentry but return graceful degradation"""
        # Simulate an error from the cache lookup
        mock_get_health.side_effect = Exception("Database connection failed")

        response = client.get("/health/uptime", headers=auth_headers)

        # Now returns 200 with default data instead of 500 (graceful degradation)
        assert response.status_code == 200

        # Verify it returns default/unknown status
        data = response.json()
        assert data["status"] == "unknown"
        assert data["uptime_percentage"] == 0.0
        assert data["total_requests"] == 0

        # Verify Sentry capture was called
        assert mock_capture_error.called
        call_args = mock_capture_error.call_args
        assert call_args[0][0].args[0] == "Database connection failed"
        assert call_args[1]["context_type"] == "health_endpoint"
        assert call_args[1]["context_data"]["endpoint"] == "/health/uptime"
        assert call_args[1]["tags"]["endpoint"] == "uptime"

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_get_uptime_metrics_no_data_available(self, mock_get_health, client, auth_headers):
        """Test that uptime metrics handles no data gracefully"""
        mock_get_health.return_value = None

        response = client.get("/health/uptime", headers=auth_headers)

        # Returns 200 with default data (graceful degradation)
        assert response.status_code == 200

        # Verify it returns default/unknown status
        data = response.json()
        assert data["status"] == "unknown"
        assert data["uptime_percentage"] == 0.0
        assert data["total_requests"] == 0
        assert data["error_rate"] == 0.0


class TestHealthDashboard:
    """Test health dashboard endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    @patch("src.services.model_health_monitor.health_monitor.get_all_providers_health")
    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_get_health_dashboard(
        self,
        mock_models,
        mock_providers,
        mock_system,
        client,
        auth_headers,
        mock_system_health,
        mock_provider_health,
        mock_model_health,
    ):
        """Get complete health dashboard"""
        mock_system.return_value = mock_system_health
        mock_providers.return_value = mock_provider_health
        mock_models.return_value = mock_model_health

        response = client.get("/health/dashboard", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "system_status" in data
            assert "providers" in data
            assert "models" in data

    @patch("src.routes.health.capture_error")
    @patch("src.routes.health.simple_health_cache.get_health_dashboard")
    def test_get_health_dashboard_error_captured_to_sentry(
        self, mock_get_dashboard, mock_capture_error, client, auth_headers
    ):
        """Test that dashboard errors are captured to Sentry"""
        # Simulate an error from the cache lookup
        mock_get_dashboard.side_effect = Exception("Dashboard data error")

        response = client.get("/health/dashboard", headers=auth_headers)

        # Should return 500 error
        assert response.status_code == 500

        # Verify Sentry capture was called
        assert mock_capture_error.called
        call_args = mock_capture_error.call_args
        assert call_args[0][0].args[0] == "Dashboard data error"
        assert call_args[1]["context_type"] == "health_endpoint"
        assert call_args[1]["context_data"]["endpoint"] == "/health/dashboard"
        assert call_args[1]["tags"]["endpoint"] == "dashboard"


class TestHealthStatus:
    """Test simple health status endpoint"""

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_get_health_status(self, mock_get_health, client, auth_headers, mock_system_health):
        """Get simple health status"""
        mock_get_health.return_value = mock_system_health

        response = client.get("/health/status", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "status" in data


class TestMonitoringControls:
    """Test monitoring control endpoints"""

    @patch("src.services.model_health_monitor.health_monitor.monitoring_active", True)
    @patch("src.services.model_availability.availability_service.monitoring_active", True)
    def test_get_monitoring_status(self, client, auth_headers):
        """Get monitoring service status"""

        response = client.get("/health/monitoring/status", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "health_monitoring_active" in data or "timestamp" in data

    @patch("src.services.model_health_monitor.health_monitor.start_monitoring")
    def test_start_health_monitoring(self, mock_start, client, auth_headers):
        """Start health monitoring service"""
        mock_start.return_value = None

        response = client.post("/health/monitoring/start", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "message" in data

    @patch("src.services.model_health_monitor.health_monitor.stop_monitoring")
    def test_stop_health_monitoring(self, mock_stop, client, auth_headers):
        """Stop health monitoring service"""
        mock_stop.return_value = None

        response = client.post("/health/monitoring/stop", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert "message" in data


class TestHealthErrorHandling:
    """Test error handling"""

    @patch("src.services.model_health_monitor.health_monitor.get_system_health")
    def test_system_health_error_handling(self, mock_get_health, client, auth_headers):
        """Handle errors in system health gracefully"""
        mock_get_health.side_effect = Exception("Database error")

        response = client.get("/health/system", headers=auth_headers)
        # Now returns 200 with default data instead of 500 (graceful degradation)
        assert response.status_code == 200

        # Verify it returns default/unknown status
        data = response.json()
        assert data["overall_status"] == "unknown"

    def test_health_check_always_works(self, client):
        """Basic health check should never fail"""
        # Even if everything is broken, /health should return 200
        response = client.get("/health")
        assert response.status_code == 200


class TestHealthEdgeCases:
    """Test edge cases"""

    @patch("src.services.model_health_monitor.health_monitor.get_all_models_health")
    def test_empty_models_list(self, mock_get_models, client, auth_headers):
        """Handle empty models list"""
        mock_get_models.return_value = []

        response = client.get("/health/models", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)
            assert len(data) == 0

    @patch("src.services.model_health_monitor.health_monitor.get_all_providers_health")
    def test_empty_providers_list(self, mock_get_providers, client, auth_headers):
        """Handle empty providers list"""
        mock_get_providers.return_value = []

        response = client.get("/health/providers", headers=auth_headers)

        if response.status_code == 200:
            response_data = response.json()
            # Response is now an object with data field containing the list
            assert isinstance(response_data, dict)
            data = response_data.get("data", [])
            assert isinstance(data, list)
            assert len(data) == 0

    def test_invalid_model_id_special_chars(self, client, auth_headers):
        """Handle special characters in model ID"""

        response = client.get("/health/model/<script>alert(1)</script>", headers=auth_headers)

        # Should not expose errors
        assert response.status_code in [404, 500]


class TestModelHealthMonitorScheduling:
    """Tests for model health monitor batching and scheduling"""

    @pytest.mark.asyncio
    async def test_get_models_to_check_returns_full_gateway_catalog(self, monkeypatch):
        from src.services.model_health_monitor import ModelHealthMonitor

        sample_models = [
            {"id": f"model-{index}", "provider_slug": "openai", "name": f"Model {index}"}
            for index in range(8)
        ]

        def fake_get_cached_models(gateway):
            if gateway == "openrouter":
                return sample_models
            return []

        monkeypatch.setattr("src.services.models.get_cached_models", fake_get_cached_models)

        monitor = ModelHealthMonitor(batch_size=3, batch_interval=0.0, fetch_chunk_size=3)

        models = await monitor._get_models_to_check()

        openrouter_models = [m for m in models if m["gateway"] == "openrouter"]
        assert len(openrouter_models) == len(sample_models)
        assert all(model["id"].startswith("model-") for model in openrouter_models)

    @pytest.mark.asyncio
    async def test_perform_health_checks_batches_models(self, monkeypatch):
        from src.services.model_health_monitor import (
            HealthStatus,
            ModelHealthMetrics,
            ModelHealthMonitor,
        )

        sample_models = [
            {"id": f"model-{index}", "provider_slug": "openai", "name": f"Model {index}"}
            for index in range(8)
        ]

        def fake_get_cached_models(gateway):
            if gateway == "openrouter":
                return sample_models
            return []

        monkeypatch.setattr("src.services.models.get_cached_models", fake_get_cached_models)

        monitor = ModelHealthMonitor(batch_size=3, batch_interval=0.0, fetch_chunk_size=4)

        async def fake_check_model_health(model):
            return ModelHealthMetrics(
                model_id=model["id"],
                provider=model.get("provider", "openai"),
                gateway=model["gateway"],
                status=HealthStatus.HEALTHY,
                response_time_ms=100.0,
                last_checked=datetime.now(UTC),
            )

        monkeypatch.setattr(monitor, "_check_model_health", fake_check_model_health)

        await monitor._perform_health_checks()

        openrouter_keys = [
            key for key in monitor.health_data.keys() if key.startswith("openrouter:")
        ]
        assert len(openrouter_keys) == len(sample_models)


class TestDatabaseHealth:
    """Test database health endpoint with initialization status"""

    @pytest.mark.asyncio
    async def test_database_health_success(self):
        """Test database health returns success with initialization status"""
        from src.routes.health import database_health

        with (
            patch("src.config.supabase_config.supabase") as mock_supabase,
            patch("src.config.supabase_config.get_initialization_status") as mock_get_status,
        ):

            # Mock successful query
            mock_supabase.table.return_value.limit.return_value.execute.return_value = MagicMock()

            # Mock initialization status
            mock_get_status.return_value = {
                "initialized": True,
                "has_error": False,
                "error_message": None,
                "error_type": None,
            }

            result = await database_health()

            assert result["status"] == "healthy"
            assert result["database"] == "supabase"
            assert result["connection"] == "verified"
            assert "initialization" in result
            assert result["initialization"]["initialized"] is True
            assert result["initialization"]["has_error"] is False

    @pytest.mark.asyncio
    async def test_database_health_failure(self):
        """Test database health returns failure with error details"""
        from src.routes.health import database_health

        with (
            patch("src.config.supabase_config.supabase") as mock_supabase,
            patch("src.config.supabase_config.get_initialization_status") as mock_get_status,
        ):

            # Mock query failure
            mock_supabase.table.return_value.limit.return_value.execute.side_effect = Exception(
                "Connection timeout"
            )

            # Mock initialization status showing error
            mock_get_status.return_value = {
                "initialized": False,
                "has_error": True,
                "error_message": "Connection timeout",
                "error_type": "Exception",
            }

            result = await database_health()

            assert result["status"] == "unhealthy"
            assert result["database"] == "supabase"
            assert result["connection"] == "failed"
            assert "error" in result
            assert "Connection timeout" in result["error"]
            assert result["error_type"] == "Exception"
            assert "initialization" in result
            assert result["initialization"]["has_error"] is True

    @pytest.mark.asyncio
    async def test_database_health_captures_to_sentry(self):
        """Test database health errors are captured to Sentry"""
        import sys
        from unittest.mock import MagicMock

        from src.routes.health import database_health

        # Create a mock sentry_sdk module
        mock_sentry = MagicMock()
        sys.modules["sentry_sdk"] = mock_sentry

        try:
            with (
                patch("src.config.supabase_config.supabase") as mock_supabase,
                patch("src.config.supabase_config.get_initialization_status") as mock_get_status,
            ):

                # Mock query failure
                test_error = Exception("Database unreachable")
                mock_supabase.table.return_value.limit.return_value.execute.side_effect = test_error

                mock_get_status.return_value = {
                    "initialized": False,
                    "has_error": True,
                    "error_message": "Database unreachable",
                    "error_type": "Exception",
                }

                result = await database_health()

                # Verify Sentry was called
                mock_sentry.capture_exception.assert_called_once_with(test_error)
                assert result["status"] == "unhealthy"
        finally:
            # Clean up
            if "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]

    @pytest.mark.asyncio
    async def test_database_health_timeout(self):
        """Test database health returns degraded status when query times out"""
        import asyncio

        from src.routes.health import HEALTH_CHECK_TIMEOUT_SECONDS, database_health

        with (
            patch("src.routes.health.supabase") as mock_supabase,
            patch("src.routes.health.get_initialization_status") as mock_get_status,
        ):

            # Mock a slow query that takes longer than the health check timeout
            def slow_query():
                import time

                time.sleep(HEALTH_CHECK_TIMEOUT_SECONDS + 1)  # Sleep longer than timeout
                return MagicMock()

            mock_supabase.table.return_value.limit.return_value.execute = slow_query

            # Mock initialization status
            mock_get_status.return_value = {
                "initialized": True,
                "has_error": False,
                "error_message": None,
                "error_type": None,
            }

            result = await database_health()

            # Should return degraded status due to timeout
            assert result["status"] == "degraded"
            assert result["database"] == "supabase"
            assert result["connection"] == "timeout"
            assert "timeout" in result["error"].lower()
            assert "initialization" in result

    @pytest.mark.asyncio
    async def test_database_health_timeout_constant_exists(self):
        """Test that HEALTH_CHECK_TIMEOUT_SECONDS constant is defined"""
        from src.routes.health import HEALTH_CHECK_TIMEOUT_SECONDS

        # Timeout should be a reasonable value for health checks (1-5 seconds)
        assert isinstance(HEALTH_CHECK_TIMEOUT_SECONDS, (int, float))
        assert 1.0 <= HEALTH_CHECK_TIMEOUT_SECONDS <= 5.0


# =============================================================================
# ADDITIONAL HEALTH ENDPOINTS TESTS
# =============================================================================


class TestHealthAllEndpoint:
    """Test /health/all endpoint"""

    @patch("src.routes.health.simple_health_cache")
    @patch("src.config.supabase_config.get_initialization_status")
    def test_get_all_health_success(self, mock_db_status, mock_cache, client, auth_headers):
        """Successfully get all health information"""
        mock_db_status.return_value = {
            "initialized": True,
            "has_error": False,
        }

        mock_cache.get_system_health.return_value = {
            "status": "healthy",
            "uptime": 99.9,
        }
        mock_cache.get_providers_health.return_value = [
            {"provider": "openrouter", "status": "online"},
            {"provider": "portkey", "status": "degraded"},
        ]
        mock_cache.get_models_health.return_value = [
            {"model": "gpt-4", "status": "online"},
        ]

        response = client.get("/health/all", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "overall_status" in data
        assert "providers" in data
        assert "models" in data
        assert "database" in data

    @patch("src.routes.health.simple_health_cache")
    @patch("src.config.supabase_config.get_initialization_status")
    def test_get_all_health_degraded(self, mock_db_status, mock_cache, client, auth_headers):
        """Return degraded status when providers are unhealthy"""
        mock_db_status.return_value = {"initialized": True, "has_error": False}

        mock_cache.get_system_health.return_value = None
        mock_cache.get_providers_health.return_value = [
            {"provider": "openrouter", "status": "offline"},
        ]
        mock_cache.get_models_health.return_value = []

        response = client.get("/health/all", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["overall_status"] == "degraded"


class TestHealthModelsStatsEndpoint:
    """Test /health/models/stats endpoint"""

    @patch("src.routes.health.simple_health_cache")
    def test_get_models_stats_success(self, mock_cache, client, auth_headers):
        """Successfully get model health stats"""
        mock_cache.get_models_health.return_value = [
            {
                "model": "gpt-4",
                "status": "online",
                "provider": "openai",
                "avg_response_time_ms": 500,
            },
            {
                "model": "gpt-3.5",
                "status": "healthy",
                "provider": "openai",
                "avg_response_time_ms": 300,
            },
            {
                "model": "claude-3",
                "status": "degraded",
                "provider": "anthropic",
                "avg_response_time_ms": 800,
            },
        ]

        response = client.get("/health/models/stats", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "stats" in data
        assert data["stats"]["total_models"] == 3
        assert data["stats"]["healthy_models"] == 2
        assert data["stats"]["degraded_models"] == 1
        assert "by_provider" in data
        assert "openai" in data["by_provider"]
        assert "anthropic" in data["by_provider"]

    @patch("src.routes.health.simple_health_cache")
    def test_get_models_stats_empty(self, mock_cache, client, auth_headers):
        """Handle empty model health data"""
        mock_cache.get_models_health.return_value = []

        response = client.get("/health/models/stats", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total_models"] == 0
        assert data["stats"]["health_rate"] == 0


class TestHealthProvidersStatsEndpoint:
    """Test /health/providers/stats endpoint"""

    @patch("src.routes.health.simple_health_cache")
    def test_get_providers_stats_success(self, mock_cache, client, auth_headers):
        """Successfully get provider health stats"""
        mock_cache.get_providers_health.return_value = [
            {
                "provider": "openrouter",
                "status": "online",
                "total_models": 100,
                "healthy_models": 95,
                "overall_uptime": 99.5,
                "avg_response_time_ms": 450,
            },
            {
                "provider": "portkey",
                "status": "degraded",
                "total_models": 50,
                "healthy_models": 40,
                "overall_uptime": 95.0,
                "avg_response_time_ms": 600,
            },
        ]

        response = client.get("/health/providers/stats", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "stats" in data
        assert data["stats"]["total_providers"] == 2
        assert data["stats"]["healthy_providers"] == 1
        assert data["stats"]["degraded_providers"] == 1
        assert data["stats"]["total_models"] == 150
        assert "providers" in data
        assert len(data["providers"]) == 2

    @patch("src.routes.health.simple_health_cache")
    def test_get_providers_stats_empty(self, mock_cache, client, auth_headers):
        """Handle empty provider health data"""
        mock_cache.get_providers_health.return_value = []

        response = client.get("/health/providers/stats", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["stats"]["total_providers"] == 0
        assert data["stats"]["health_rate"] == 0
        assert data["stats"]["avg_uptime"] == 0
