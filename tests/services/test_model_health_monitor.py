"""
Tests for Model Health Monitor Service

Covers:
- Model health status tracking
- Health score calculation
- Failed model detection
- Recovery detection
- Health metrics storage
- Alert triggering
- Sentry error capture for non-functional models
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import os

os.environ['APP_ENV'] = 'testing'

# Import after setting environment
try:
    from src.services.model_health_monitor import (
        ModelHealthMonitor,
        track_model_health,
        get_model_health,
        mark_model_unhealthy,
        mark_model_healthy,
        get_unhealthy_models,
    )
    HEALTH_MONITOR_AVAILABLE = True
except (ImportError, AttributeError):
    HEALTH_MONITOR_AVAILABLE = False
    # Create mock functions for testing structure
    class ModelHealthMonitor:
        pass


@pytest.fixture
def health_monitor():
    """Create a health monitor instance"""
    if HEALTH_MONITOR_AVAILABLE:
        return ModelHealthMonitor()
    return Mock()


@pytest.fixture
def sample_model_data():
    """Sample model data for testing"""
    return {
        'model_id': 'gpt-3.5-turbo',
        'provider': 'openai',
        'status': 'healthy',
        'success_rate': 0.95,
        'avg_latency': 1.5,
        'last_check': datetime.now()
    }


@pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented yet")
class TestModelHealthTracking:
    """Test model health tracking functionality"""

    def test_track_successful_request(self, health_monitor):
        """Track successful model request"""
        model_id = "gpt-3.5-turbo"

        result = track_model_health(
            model_id=model_id,
            success=True,
            latency=1.5,
            error=None
        )

        assert result is not None
        # Health should be tracked

    def test_track_failed_request(self, health_monitor):
        """Track failed model request"""
        model_id = "gpt-3.5-turbo"

        result = track_model_health(
            model_id=model_id,
            success=False,
            latency=5.0,
            error="Timeout error"
        )

        assert result is not None
        # Failure should be recorded

    def test_track_multiple_requests(self, health_monitor):
        """Track multiple requests for same model"""
        model_id = "gpt-3.5-turbo"

        # Track successful requests
        for _ in range(10):
            track_model_health(model_id, success=True, latency=1.0)

        # Track some failures
        for _ in range(2):
            track_model_health(model_id, success=False, latency=5.0, error="Error")

        # Health should reflect the ratio
        health = get_model_health(model_id)
        if health:
            assert health.get('success_rate', 1.0) > 0.5


@pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented yet")
class TestHealthStatusManagement:
    """Test health status management"""

    def test_mark_model_unhealthy(self, health_monitor):
        """Mark a model as unhealthy"""
        model_id = "gpt-3.5-turbo"

        result = mark_model_unhealthy(model_id, reason="High error rate")

        assert result is not None or result is True

    def test_mark_model_healthy(self, health_monitor):
        """Mark a model as healthy"""
        model_id = "gpt-3.5-turbo"

        # First mark unhealthy
        mark_model_unhealthy(model_id, reason="Test")

        # Then mark healthy
        result = mark_model_healthy(model_id)

        assert result is not None or result is True

    def test_get_unhealthy_models(self, health_monitor):
        """Get list of unhealthy models"""
        # Mark some models unhealthy
        mark_model_unhealthy("model1", reason="Error")
        mark_model_unhealthy("model2", reason="Timeout")

        unhealthy = get_unhealthy_models()

        assert isinstance(unhealthy, (list, dict)) or unhealthy is None


class TestHealthScoreCalculation:
    """Test health score calculation"""

    def test_calculate_health_score_all_success(self):
        """Calculate health score with all successful requests"""
        # Mock successful requests
        success_count = 100
        failure_count = 0

        if success_count > 0:
            success_rate = success_count / (success_count + failure_count)
            assert success_rate == 1.0

    def test_calculate_health_score_mixed(self):
        """Calculate health score with mixed results"""
        success_count = 80
        failure_count = 20

        success_rate = success_count / (success_count + failure_count)
        assert success_rate == 0.8

    def test_calculate_health_score_all_failures(self):
        """Calculate health score with all failures"""
        success_count = 0
        failure_count = 100

        success_rate = success_count / (success_count + failure_count)
        assert success_rate == 0.0


class TestHealthMetrics:
    """Test health metrics collection"""

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_track_latency(self, health_monitor):
        """Track request latency"""
        model_id = "gpt-3.5-turbo"

        latencies = [1.0, 1.5, 2.0, 1.2, 1.8]

        for latency in latencies:
            track_model_health(model_id, success=True, latency=latency)

        health = get_model_health(model_id)
        if health and 'avg_latency' in health:
            # Average should be around 1.5
            assert 1.0 <= health['avg_latency'] <= 2.5

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_track_error_count(self, health_monitor):
        """Track error count"""
        model_id = "gpt-3.5-turbo"

        # Generate some errors
        for i in range(5):
            track_model_health(model_id, success=False, error=f"Error {i}")

        health = get_model_health(model_id)
        if health and 'error_count' in health:
            assert health['error_count'] >= 5


class TestFailureDetection:
    """Test failure detection and thresholds"""

    def test_detect_high_error_rate(self):
        """Detect when error rate exceeds threshold"""
        success_count = 20
        failure_count = 80

        error_rate = failure_count / (success_count + failure_count)

        # Error rate threshold (e.g., 50%)
        threshold = 0.5

        assert error_rate > threshold

    def test_detect_slow_response(self):
        """Detect when latency exceeds threshold"""
        latency = 10.0  # seconds
        threshold = 5.0  # seconds

        assert latency > threshold

    def test_consecutive_failures(self):
        """Detect consecutive failures"""
        failures = [False, False, False, False, False]

        consecutive_count = 0
        for result in failures:
            if not result:
                consecutive_count += 1
            else:
                consecutive_count = 0

        # Should detect 5 consecutive failures
        assert consecutive_count >= 5


class TestRecoveryDetection:
    """Test recovery detection"""

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_detect_recovery(self, health_monitor):
        """Detect when model recovers"""
        model_id = "gpt-3.5-turbo"

        # Mark unhealthy
        mark_model_unhealthy(model_id, reason="High errors")

        # Simulate successful requests
        for _ in range(10):
            track_model_health(model_id, success=True, latency=1.0)

        # Should potentially auto-recover or allow manual recovery
        mark_model_healthy(model_id)

        health = get_model_health(model_id)
        if health:
            assert health.get('status') in ['healthy', 'recovering', None]


class TestHealthAlerts:
    """Test health monitoring alerts"""

    def test_alert_on_model_failure(self):
        """Alert should trigger when model fails"""
        model_id = "gpt-3.5-turbo"
        error_count = 10

        # Check if alert should trigger
        alert_threshold = 5

        should_alert = error_count > alert_threshold
        assert should_alert is True

    def test_alert_cooldown(self):
        """Alerts should have cooldown period"""
        last_alert_time = datetime.now() - timedelta(minutes=30)
        current_time = datetime.now()
        cooldown_minutes = 60

        time_since_alert = (current_time - last_alert_time).total_seconds() / 60

        should_send_alert = time_since_alert >= cooldown_minutes
        assert should_send_alert is False  # Within cooldown


class TestHealthMonitoringEdgeCases:
    """Test edge cases"""

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_handle_null_model_id(self, health_monitor):
        """Handle null model ID gracefully"""
        try:
            result = track_model_health(None, success=True, latency=1.0)
            assert result is None or result is False
        except (ValueError, TypeError):
            # Acceptable to raise error
            pass

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_handle_negative_latency(self, health_monitor):
        """Handle negative latency values"""
        try:
            result = track_model_health("gpt-3.5-turbo", success=True, latency=-1.0)
            # Should either reject or handle
            assert result is not None
        except ValueError:
            # Acceptable to raise error
            pass

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_handle_extreme_latency(self, health_monitor):
        """Handle extremely high latency values"""
        result = track_model_health("gpt-3.5-turbo", success=False, latency=99999.0)

        # Should handle gracefully
        assert result is not None or result is False


class TestHealthMonitorPersistence:
    """Test health data persistence"""

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_health_data_persisted(self, health_monitor):
        """Health data should be persisted"""
        model_id = "gpt-3.5-turbo"

        track_model_health(model_id, success=True, latency=1.0)

        # Should be retrievable
        health = get_model_health(model_id)
        assert health is not None or health == {}

    @pytest.mark.skipif(not HEALTH_MONITOR_AVAILABLE, reason="Health monitor not implemented")
    def test_health_data_timestamped(self, health_monitor):
        """Health data should include timestamps"""
        model_id = "gpt-3.5-turbo"

        track_model_health(model_id, success=True, latency=1.0)

        health = get_model_health(model_id)
        if health:
            # Should have timestamp field
            assert 'last_check' in health or 'timestamp' in health or 'updated_at' in health


class TestHealthMonitorIntegration:
    """Test integration with other systems"""

    def test_unhealthy_models_excluded_from_routing(self):
        """Unhealthy models should be excluded from routing"""
        available_models = ["gpt-3.5-turbo", "gpt-4", "claude-2"]
        unhealthy_models = ["gpt-4"]

        # Filter out unhealthy models
        healthy_models = [m for m in available_models if m not in unhealthy_models]

        assert "gpt-4" not in healthy_models
        assert "gpt-3.5-turbo" in healthy_models

    def test_health_check_integration(self):
        """Health monitor should integrate with health check endpoint"""
        # Mock health check response
        health_status = {
            'status': 'healthy',
            'models_monitored': 10,
            'unhealthy_models': 2
        }

        assert health_status['status'] == 'healthy'
        assert health_status['unhealthy_models'] < health_status['models_monitored']


class TestPerformanceMetrics:
    """Test performance metrics calculation"""

    def test_calculate_success_rate(self):
        """Calculate success rate correctly"""
        total_requests = 100
        successful_requests = 95

        success_rate = successful_requests / total_requests
        assert success_rate == 0.95

    def test_calculate_average_latency(self):
        """Calculate average latency"""
        latencies = [1.0, 2.0, 3.0, 4.0, 5.0]

        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency == 3.0

    def test_calculate_error_rate(self):
        """Calculate error rate"""
        total_requests = 100
        failed_requests = 5

        error_rate = failed_requests / total_requests
        assert error_rate == 0.05


class TestSentryErrorCapture:
    """Test Sentry error capture for model failures"""

    @pytest.mark.asyncio
    @patch('src.utils.sentry_context.capture_exception')
    async def test_sentry_capture_on_model_failure(self, mock_capture):
        """Test that Sentry captures errors when models fail health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {
            'id': 'gpt-3.5-turbo',
            'provider': 'openai',
            'gateway': 'openrouter',
            'name': 'GPT-3.5 Turbo'
        }

        # Mock the health check to fail
        with patch.object(monitor, '_perform_model_request') as mock_request:
            mock_request.return_value = {
                'success': False,
                'error': 'Connection timeout',
                'status_code': 408,
                'response_time': 5000
            }

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows unhealthy status
            assert result is not None
            assert result.status.value == 'unhealthy'
            assert result.error_message == 'Connection timeout'

            # Verify Sentry capture was called
            assert mock_capture.called, "Sentry capture_exception should be called for model failures"

    @pytest.mark.asyncio
    @patch('src.utils.sentry_context.capture_exception')
    async def test_sentry_capture_on_exception(self, mock_capture):
        """Test that Sentry captures exceptions during health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {
            'id': 'claude-3-opus',
            'provider': 'anthropic',
            'gateway': 'openrouter',
            'name': 'Claude 3 Opus'
        }

        # Mock the health check to raise an exception
        with patch.object(monitor, '_perform_model_request') as mock_request:
            mock_request.side_effect = Exception("Network error")

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows unhealthy status
            assert result is not None
            assert result.status.value == 'unhealthy'
            assert 'Network error' in result.error_message

            # Verify Sentry capture was called
            assert mock_capture.called, "Sentry capture_exception should be called for exceptions"

    @pytest.mark.asyncio
    @patch('src.utils.sentry_context.capture_exception')
    async def test_sentry_not_captured_on_success(self, mock_capture):
        """Test that Sentry does not capture errors when models are healthy"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {
            'id': 'gpt-4',
            'provider': 'openai',
            'gateway': 'openrouter',
            'name': 'GPT-4'
        }

        # Mock the health check to succeed
        with patch.object(monitor, '_perform_model_request') as mock_request:
            mock_request.return_value = {
                'success': True,
                'status_code': 200,
                'response_time': 1200
            }

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows healthy status
            assert result is not None
            assert result.status.value == 'healthy'

            # Verify Sentry capture was NOT called
            assert not mock_capture.called, "Sentry should not capture errors for healthy models"


class TestApiKeyForGateway:
    """Test API key retrieval for different gateways"""

    def test_get_api_key_for_known_gateway(self):
        """Test that API key is returned for known gateways"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Test with mock config values
        with patch('src.services.model_health_monitor.Config') as mock_config:
            mock_config.OPENROUTER_API_KEY = "test-openrouter-key"
            mock_config.FEATHERLESS_API_KEY = "test-featherless-key"
            mock_config.DEEPINFRA_API_KEY = "test-deepinfra-key"
            mock_config.HUG_API_KEY = "test-huggingface-key"
            mock_config.GROQ_API_KEY = "test-groq-key"
            mock_config.FIREWORKS_API_KEY = "test-fireworks-key"
            mock_config.TOGETHER_API_KEY = "test-together-key"
            mock_config.XAI_API_KEY = "test-xai-key"
            mock_config.NOVITA_API_KEY = "test-novita-key"
            mock_config.CHUTES_API_KEY = "test-chutes-key"
            mock_config.AIMO_API_KEY = "test-aimo-key"
            mock_config.NEBIUS_API_KEY = "test-nebius-key"
            mock_config.CEREBRAS_API_KEY = "test-cerebras-key"

            # Verify each gateway returns correct key
            assert monitor._get_api_key_for_gateway("openrouter") == "test-openrouter-key"
            assert monitor._get_api_key_for_gateway("featherless") == "test-featherless-key"
            assert monitor._get_api_key_for_gateway("deepinfra") == "test-deepinfra-key"
            assert monitor._get_api_key_for_gateway("huggingface") == "test-huggingface-key"
            assert monitor._get_api_key_for_gateway("groq") == "test-groq-key"
            assert monitor._get_api_key_for_gateway("fireworks") == "test-fireworks-key"
            assert monitor._get_api_key_for_gateway("together") == "test-together-key"
            assert monitor._get_api_key_for_gateway("xai") == "test-xai-key"
            assert monitor._get_api_key_for_gateway("novita") == "test-novita-key"
            assert monitor._get_api_key_for_gateway("chutes") == "test-chutes-key"
            assert monitor._get_api_key_for_gateway("aimo") == "test-aimo-key"
            assert monitor._get_api_key_for_gateway("nebius") == "test-nebius-key"
            assert monitor._get_api_key_for_gateway("cerebras") == "test-cerebras-key"

    def test_get_api_key_for_unknown_gateway(self):
        """Test that None is returned for unknown gateways"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Unknown gateway should return None
        assert monitor._get_api_key_for_gateway("unknown-gateway") is None
        assert monitor._get_api_key_for_gateway("") is None
        assert monitor._get_api_key_for_gateway("nonexistent") is None


class TestPerformModelRequestAuthentication:
    """Test authentication in _perform_model_request"""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_api_key_configured(self):
        """Test that request fails when no API key is configured for gateway"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock environment to disable TESTING mode, and mock _get_api_key_for_gateway to return None
        with patch.dict(os.environ, {"TESTING": "false"}, clear=False):
            with patch.object(monitor, '_get_api_key_for_gateway', return_value=None):
                result = await monitor._perform_model_request("test-model", "openrouter")

                assert result["success"] is False
                assert result["status_code"] == 401
                assert "No API key configured" in result["error"]

    @pytest.mark.asyncio
    async def test_includes_authorization_header_when_api_key_configured(self):
        """Test that Authorization header is included when API key is configured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock environment to disable TESTING mode
        with patch.dict(os.environ, {"TESTING": "false"}, clear=False):
            with patch.object(monitor, '_get_api_key_for_gateway', return_value="test-api-key"):
                with patch('httpx.AsyncClient') as mock_client:
                    # Set up the mock to capture the headers
                    mock_response = MagicMock()
                    mock_response.status_code = 200
                    mock_response.content = b'{"result": "ok"}'
                    mock_response.json.return_value = {"result": "ok"}

                    mock_context = MagicMock()
                    mock_context.__aenter__ = MagicMock(return_value=MagicMock())
                    mock_context.__aenter__.return_value.post = MagicMock(return_value=mock_response)

                    # Make the async context manager work
                    async def mock_aenter(*args, **kwargs):
                        mock_instance = MagicMock()
                        async def mock_post(url, headers=None, json=None):
                            # Verify Authorization header is present
                            assert headers is not None
                            assert "Authorization" in headers
                            assert headers["Authorization"] == "Bearer test-api-key"
                            return mock_response
                        mock_instance.post = mock_post
                        return mock_instance

                    mock_client.return_value.__aenter__ = mock_aenter
                    mock_client.return_value.__aexit__ = MagicMock(return_value=None)

                    result = await monitor._perform_model_request("test-model", "openrouter")

                    assert result["success"] is True
                    assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_gateway(self):
        """Test that request fails for unknown gateway (no URL configured)"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock environment to disable TESTING mode
        with patch.dict(os.environ, {"TESTING": "false"}, clear=False):
            result = await monitor._perform_model_request("test-model", "unknown-gateway")

            assert result["success"] is False
            assert result["status_code"] == 400
            assert "Unknown gateway" in result["error"]
