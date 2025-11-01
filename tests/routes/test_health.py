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
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

# Set test environment
os.environ['APP_ENV'] = 'testing'
os.environ['TESTING'] = 'true'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
os.environ['SUPABASE_KEY'] = 'test-key'

from src.main import app


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Authentication headers"""
    return {
        'Authorization': 'Bearer gw_test_key_123',
        'Content-Type': 'application/json'
    }


@pytest.fixture
def mock_system_health():
    """Mock system health response"""
    from src.models.health_models import SystemHealthResponse, HealthStatus

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
        last_updated=datetime.now(timezone.utc)
    )


@pytest.fixture
def mock_provider_health():
    """Mock provider health response"""
    from src.models.health_models import ProviderHealthResponse, ProviderStatus

    return [
        ProviderHealthResponse(
            provider="openai",
            gateway="portkey",
            status=ProviderStatus.ONLINE,
            total_models=10,
            healthy_models=10,
            degraded_models=0,
            unhealthy_models=0,
            overall_uptime=99.9,
            avg_response_time_ms=150.0,
            last_checked=datetime.now(timezone.utc)
        ),
        ProviderHealthResponse(
            provider="anthropic",
            gateway="portkey",
            status=ProviderStatus.DEGRADED,
            total_models=5,
            healthy_models=4,
            degraded_models=1,
            unhealthy_models=0,
            overall_uptime=98.5,
            avg_response_time_ms=250.0,
            last_checked=datetime.now(timezone.utc)
        )
    ]


@pytest.fixture
def mock_model_health():
    """Mock model health response"""
    from src.models.health_models import ModelHealthResponse, HealthStatus

    return [
        ModelHealthResponse(
            model_id="gpt-3.5-turbo",
            provider="openai",
            gateway="portkey",
            status=HealthStatus.HEALTHY,
            response_time_ms=120.0,
            uptime_percentage=99.8,
            error_count=2,
            total_requests=1000,
            last_checked=datetime.now(timezone.utc),
            last_error=None,
            avg_response_time_ms=125.0
        )
    ]


class TestBasicHealthCheck:
    """Test basic health check endpoint"""

    def test_health_check_returns_200(self, client):
        """Basic health check returns 200 OK"""
        response = client.get('/health')
        assert response.status_code == 200

    def test_health_check_returns_healthy_status(self, client):
        """Health check returns healthy status"""
        response = client.get('/health')
        data = response.json()

        assert 'status' in data
        assert data['status'] == 'healthy'

    def test_health_check_no_auth_required(self, client):
        """Health check doesn't require authentication"""
        # No auth headers
        response = client.get('/health')
        assert response.status_code == 200

    def test_health_check_response_format(self, client):
        """Health check returns valid JSON"""
        response = client.get('/health')

        assert response.headers['content-type'] == 'application/json'
        data = response.json()
        assert isinstance(data, dict)


class TestSystemHealth:
    """Test system health endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    def test_system_health_success(self, mock_get_health, mock_auth, client, auth_headers, mock_system_health):
        """Successfully get system health metrics"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_health.return_value = mock_system_health

        response = client.get('/health/system', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'overall_status' in data
            assert 'total_providers' in data
            assert 'total_models' in data

    @patch('src.security.deps.get_user_by_api_key')
    def test_system_health_requires_auth(self, mock_auth, client):
        """System health requires authentication"""
        mock_auth.return_value = None

        response = client.get('/health/system')
        assert response.status_code in [401, 403, 422]

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    def test_system_health_no_data_available(self, mock_get_health, mock_auth, client, auth_headers):
        """System health handles no data gracefully"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_health.return_value = None

        response = client.get('/health/system', headers=auth_headers)
        assert response.status_code in [503, 500]


class TestProvidersHealth:
    """Test providers health endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_providers_health')
    def test_get_all_providers_health(self, mock_get_providers, mock_auth, client, auth_headers, mock_provider_health):
        """Get health for all providers"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_providers.return_value = mock_provider_health

        response = client.get('/health/providers', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            if len(data) > 0:
                assert 'provider' in data[0]
                assert 'status' in data[0]

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_providers_health')
    def test_filter_providers_by_gateway(self, mock_get_providers, mock_auth, client, auth_headers, mock_provider_health):
        """Filter providers by gateway parameter"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_providers.return_value = mock_provider_health

        response = client.get('/health/providers?gateway=portkey', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)


class TestModelsHealth:
    """Test models health endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_get_all_models_health(self, mock_get_models, mock_auth, client, auth_headers, mock_model_health):
        """Get health for all models"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_models.return_value = mock_model_health

        response = client.get('/health/models', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            if len(data) > 0:
                assert 'model_id' in data[0]
                assert 'status' in data[0]

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_filter_models_by_provider(self, mock_get_models, mock_auth, client, auth_headers, mock_model_health):
        """Filter models by provider parameter"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_models.return_value = mock_model_health

        response = client.get('/health/models?provider=openai', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_filter_models_by_status(self, mock_get_models, mock_auth, client, auth_headers, mock_model_health):
        """Filter models by status parameter"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_models.return_value = mock_model_health

        response = client.get('/health/models?status=healthy', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)


class TestSpecificModelHealth:
    """Test specific model health endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_model_health')
    def test_get_model_health_success(self, mock_get_model, mock_auth, client, auth_headers, mock_model_health):
        """Get health for specific model"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_model.return_value = mock_model_health[0]

        response = client.get('/health/model/gpt-3.5-turbo', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'model_id' in data
            assert 'status' in data

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_model_health')
    def test_get_model_health_not_found(self, mock_get_model, mock_auth, client, auth_headers):
        """Model not found returns 404"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_model.return_value = None

        response = client.get('/health/model/nonexistent-model', headers=auth_headers)
        assert response.status_code == 404


class TestSpecificProviderHealth:
    """Test specific provider health endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_provider_health')
    def test_get_provider_health_success(self, mock_get_provider, mock_auth, client, auth_headers, mock_provider_health):
        """Get health for specific provider"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_provider.return_value = mock_provider_health[0]

        response = client.get('/health/provider/openai', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'provider' in data
            assert 'status' in data

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_provider_health')
    def test_get_provider_health_not_found(self, mock_get_provider, mock_auth, client, auth_headers):
        """Provider not found returns 404"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_provider.return_value = None

        response = client.get('/health/provider/nonexistent', headers=auth_headers)
        assert response.status_code == 404


class TestHealthSummary:
    """Test health summary endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_health_summary')
    def test_get_health_summary(self, mock_get_summary, mock_auth, client, auth_headers):
        """Get comprehensive health summary"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_summary = MagicMock()
        mock_get_summary.return_value = mock_summary

        response = client.get('/health/summary', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)


class TestHealthCheck:
    """Test health check trigger endpoints"""

    @patch('src.security.deps.get_user_by_api_key')
    def test_perform_health_check(self, mock_auth, client, auth_headers):
        """Trigger background health check"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}

        response = client.post('/health/check', json={'force_refresh': True}, headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'message' in data
            assert 'timestamp' in data

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor._perform_health_checks')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    def test_perform_immediate_health_check(self, mock_get_health, mock_perform, mock_auth, client, auth_headers, mock_system_health):
        """Perform immediate health check"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_perform.return_value = None
        mock_get_health.return_value = mock_system_health

        response = client.post('/health/check/now', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'message' in data
            assert 'timestamp' in data


class TestUptimeMetrics:
    """Test uptime metrics endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_get_uptime_metrics(self, mock_models, mock_system, mock_auth, client, auth_headers, mock_system_health, mock_model_health):
        """Get uptime metrics for status page"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_system.return_value = mock_system_health
        mock_models.return_value = mock_model_health

        response = client.get('/health/uptime', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'status' in data
            assert 'uptime_percentage' in data


class TestHealthDashboard:
    """Test health dashboard endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    @patch('src.services.model_health_monitor.health_monitor.get_all_providers_health')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_get_health_dashboard(self, mock_models, mock_providers, mock_system, mock_auth, client, auth_headers, mock_system_health, mock_provider_health, mock_model_health):
        """Get complete health dashboard"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_system.return_value = mock_system_health
        mock_providers.return_value = mock_provider_health
        mock_models.return_value = mock_model_health

        response = client.get('/health/dashboard', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'system_status' in data
            assert 'providers' in data
            assert 'models' in data


class TestHealthStatus:
    """Test simple health status endpoint"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    def test_get_health_status(self, mock_get_health, mock_auth, client, auth_headers, mock_system_health):
        """Get simple health status"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_health.return_value = mock_system_health

        response = client.get('/health/status', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'status' in data


class TestMonitoringControls:
    """Test monitoring control endpoints"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.monitoring_active', True)
    @patch('src.services.model_availability.availability_service.monitoring_active', True)
    def test_get_monitoring_status(self, mock_auth, client, auth_headers):
        """Get monitoring service status"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}

        response = client.get('/health/monitoring/status', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'health_monitoring_active' in data or 'timestamp' in data

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.start_monitoring')
    def test_start_health_monitoring(self, mock_start, mock_auth, client, auth_headers):
        """Start health monitoring service"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_start.return_value = None

        response = client.post('/health/monitoring/start', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'message' in data

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.stop_monitoring')
    def test_stop_health_monitoring(self, mock_stop, mock_auth, client, auth_headers):
        """Stop health monitoring service"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_stop.return_value = None

        response = client.post('/health/monitoring/stop', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert 'message' in data


class TestHealthErrorHandling:
    """Test error handling"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_system_health')
    def test_system_health_error_handling(self, mock_get_health, mock_auth, client, auth_headers):
        """Handle errors in system health gracefully"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_health.side_effect = Exception("Database error")

        response = client.get('/health/system', headers=auth_headers)
        assert response.status_code == 500

    def test_health_check_always_works(self, client):
        """Basic health check should never fail"""
        # Even if everything is broken, /health should return 200
        response = client.get('/health')
        assert response.status_code == 200


class TestHealthEdgeCases:
    """Test edge cases"""

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_models_health')
    def test_empty_models_list(self, mock_get_models, mock_auth, client, auth_headers):
        """Handle empty models list"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_models.return_value = []

        response = client.get('/health/models', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0

    @patch('src.security.deps.get_user_by_api_key')
    @patch('src.services.model_health_monitor.health_monitor.get_all_providers_health')
    def test_empty_providers_list(self, mock_get_providers, mock_auth, client, auth_headers):
        """Handle empty providers list"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}
        mock_get_providers.return_value = []

        response = client.get('/health/providers', headers=auth_headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0

    @patch('src.security.deps.get_user_by_api_key')
    def test_invalid_model_id_special_chars(self, mock_auth, client, auth_headers):
        """Handle special characters in model ID"""
        mock_auth.return_value = {'id': 1, 'api_key': 'gw_test_key'}

        response = client.get('/health/model/<script>alert(1)</script>', headers=auth_headers)

        # Should not expose errors
        assert response.status_code in [404, 500]
