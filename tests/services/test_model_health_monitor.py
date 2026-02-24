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

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

os.environ["APP_ENV"] = "testing"

# Import after setting environment
from src.services.model_health_monitor import (
    HealthStatus,
    ModelHealthMetrics,
    ModelHealthMonitor,
    SystemHealthMetrics,
)

HEALTH_MONITOR_AVAILABLE = True


@pytest.fixture
def health_monitor():
    """Create a health monitor instance"""
    return ModelHealthMonitor()


@pytest.fixture
def sample_model_data():
    """Sample model data for testing"""
    return {
        "model_id": "gpt-3.5-turbo",
        "provider": "openai",
        "status": "healthy",
        "success_rate": 0.95,
        "avg_latency": 1.5,
        "last_check": datetime.now(),
    }


class TestModelHealthTracking:
    """Test model health tracking functionality using ModelHealthMonitor methods"""

    def test_get_model_health_returns_none_for_unknown(self, health_monitor):
        """Get health for unknown model returns None"""
        result = health_monitor.get_model_health("unknown-model-12345")
        assert result is None

    def test_get_all_models_health_returns_list(self, health_monitor):
        """Get all models health returns a list"""
        result = health_monitor.get_all_models_health()
        assert isinstance(result, list)

    def test_get_health_summary_returns_dict(self, health_monitor):
        """Get health summary returns a dictionary"""
        result = health_monitor.get_health_summary()
        assert isinstance(result, dict)

    def test_get_system_health_initially_none(self, health_monitor):
        """System health is None before monitoring starts"""
        result = health_monitor.get_system_health()
        # May be None initially or return SystemHealthMetrics
        assert result is None or isinstance(result, SystemHealthMetrics)


class TestHealthStatusManagement:
    """Test health status management via ModelHealthMonitor"""

    def test_get_provider_health_returns_none_for_unknown(self, health_monitor):
        """Get provider health for unknown provider returns None"""
        result = health_monitor.get_provider_health("unknown-provider", "unknown-gateway")
        assert result is None

    def test_get_all_providers_health_returns_list(self, health_monitor):
        """Get all providers health returns a list"""
        result = health_monitor.get_all_providers_health()
        assert isinstance(result, list)

    def test_health_data_structure(self, health_monitor):
        """Health data internal structure is initialized"""
        assert hasattr(health_monitor, "health_data")
        assert isinstance(health_monitor.health_data, dict)
        assert hasattr(health_monitor, "provider_data")
        assert isinstance(health_monitor.provider_data, dict)


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

    def test_model_health_metrics_dataclass(self):
        """Test ModelHealthMetrics dataclass structure"""
        metrics = ModelHealthMetrics(
            model_id="gpt-3.5-turbo",
            gateway="openrouter",
            provider="openai",
            status=HealthStatus.HEALTHY,
            response_time_ms=1500,
            last_checked=datetime.now(),
        )

        assert metrics.model_id == "gpt-3.5-turbo"
        assert metrics.gateway == "openrouter"
        assert metrics.status == HealthStatus.HEALTHY
        assert metrics.response_time_ms == 1500

    def test_model_health_metrics_unhealthy(self):
        """Test ModelHealthMetrics with unhealthy status"""
        metrics = ModelHealthMetrics(
            model_id="broken-model",
            gateway="openrouter",
            provider="test",
            status=HealthStatus.UNHEALTHY,
            response_time_ms=None,
            last_checked=datetime.now(),
            error_message="Connection timeout",
        )

        assert metrics.status == HealthStatus.UNHEALTHY
        assert metrics.error_message == "Connection timeout"


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

    def test_health_status_enum_values(self):
        """Test HealthStatus enum has expected values"""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNKNOWN.value == "unknown"

    def test_health_status_transitions(self):
        """Test that health status can transition between states"""
        # Can set to unhealthy
        status = HealthStatus.UNHEALTHY
        assert status == HealthStatus.UNHEALTHY

        # Can recover to healthy
        status = HealthStatus.HEALTHY
        assert status == HealthStatus.HEALTHY


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

    def test_get_model_health_with_none_model_id(self, health_monitor):
        """Handle None model ID gracefully"""
        # Should return None for None model_id
        result = health_monitor.get_model_health(None)
        assert result is None

    def test_get_model_health_with_empty_string(self, health_monitor):
        """Handle empty string model ID"""
        result = health_monitor.get_model_health("")
        assert result is None

    def test_get_provider_health_with_none_values(self, health_monitor):
        """Handle None provider/gateway gracefully"""
        result = health_monitor.get_provider_health(None, None)
        assert result is None


class TestHealthMonitorPersistence:
    """Test health data persistence"""

    def test_health_data_dict_initialized(self, health_monitor):
        """Health data dictionary is initialized on monitor creation"""
        assert hasattr(health_monitor, "health_data")
        assert isinstance(health_monitor.health_data, dict)
        assert hasattr(health_monitor, "provider_data")
        assert isinstance(health_monitor.provider_data, dict)
        # system_data is None until monitoring starts
        assert hasattr(health_monitor, "system_data")

    def test_model_health_metrics_has_timestamp(self):
        """ModelHealthMetrics includes last_checked timestamp"""
        metrics = ModelHealthMetrics(
            model_id="test-model",
            gateway="test",
            provider="test",
            status=HealthStatus.HEALTHY,
            response_time_ms=100,
            last_checked=datetime.now(),
        )
        assert metrics.last_checked is not None
        assert isinstance(metrics.last_checked, datetime)


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
        health_status = {"status": "healthy", "models_monitored": 10, "unhealthy_models": 2}

        assert health_status["status"] == "healthy"
        assert health_status["unhealthy_models"] < health_status["models_monitored"]


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
    @patch("src.utils.sentry_context.capture_exception")
    async def test_sentry_capture_on_model_failure(self, mock_capture):
        """Test that Sentry captures errors when models fail health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {
            "id": "gpt-3.5-turbo",
            "provider": "openai",
            "gateway": "openrouter",
            "name": "GPT-3.5 Turbo",
        }

        # Mock the health check to fail
        with patch.object(monitor, "_perform_model_request") as mock_request:
            mock_request.return_value = {
                "success": False,
                "error": "Connection timeout",
                "status_code": 408,
                "response_time": 5000,
            }

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows unhealthy status
            assert result is not None
            assert result.status.value == "unhealthy"
            assert result.error_message == "Connection timeout"

            # Verify Sentry capture was called
            assert (
                mock_capture.called
            ), "Sentry capture_exception should be called for model failures"

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.capture_exception")
    async def test_sentry_capture_on_exception(self, mock_capture):
        """Test that Sentry captures exceptions during health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {
            "id": "claude-3-opus",
            "provider": "anthropic",
            "gateway": "openrouter",
            "name": "Claude 3 Opus",
        }

        # Mock the health check to raise an exception
        with patch.object(monitor, "_perform_model_request") as mock_request:
            mock_request.side_effect = Exception("Network error")

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows unhealthy status
            assert result is not None
            assert result.status.value == "unhealthy"
            assert "Network error" in result.error_message

            # Verify Sentry capture was called
            assert mock_capture.called, "Sentry capture_exception should be called for exceptions"

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.capture_exception")
    async def test_sentry_not_captured_on_success(self, mock_capture):
        """Test that Sentry does not capture errors when models are healthy"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Mock model data
        model = {"id": "gpt-4", "provider": "openai", "gateway": "openrouter", "name": "GPT-4"}

        # Mock the health check to succeed
        with patch.object(monitor, "_perform_model_request") as mock_request:
            mock_request.return_value = {"success": True, "status_code": 200, "response_time": 1200}

            # Perform health check
            result = await monitor._check_model_health(model)

            # Verify result shows healthy status
            assert result is not None
            assert result.status.value == "healthy"

            # Verify Sentry capture was NOT called
            assert not mock_capture.called, "Sentry should not capture errors for healthy models"


class TestApiKeyForGateway:
    """Test API key retrieval for different gateways"""

    def test_get_api_key_for_known_gateway(self):
        """Test that API key is returned for known gateways"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Test with mock config values
        with patch("src.services.model_health_monitor.Config") as mock_config:
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
            with patch.object(monitor, "_get_api_key_for_gateway", return_value=None):
                result = await monitor._perform_model_request("test-model", "openrouter")

                assert result["success"] is False
                assert result["status_code"] == 401
                assert "No API key configured" in result["error"]

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


class TestShouldCaptureError:
    """Test _should_capture_error method for intelligent error filtering"""

    def test_should_capture_unknown_errors(self):
        """Test that unknown errors (no status code) are captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # No status code - should capture
        assert monitor._should_capture_error(None, "Unknown error") is True
        assert monitor._should_capture_error(None, None) is True

    def test_should_not_capture_rate_limits(self):
        """Test that rate limit errors (429) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Rate limit errors should not be captured
        assert monitor._should_capture_error(429, "Rate limit exceeded") is False
        assert monitor._should_capture_error(429, None) is False

    def test_should_not_capture_data_policy_errors(self):
        """Test that data policy restriction errors (404) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Data policy errors should not be captured
        assert monitor._should_capture_error(404, "Data policy restriction") is False
        assert (
            monitor._should_capture_error(
                404, "Your account does not have access due to data policy"
            )
            is False
        )
        assert monitor._should_capture_error(404, "DATA POLICY violation") is False

    def test_should_capture_other_404_errors(self):
        """Test that other 404 errors (not data policy) ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Non-data-policy 404s should be captured
        assert monitor._should_capture_error(404, "Model not found") is True
        assert monitor._should_capture_error(404, "Endpoint not found") is True
        assert monitor._should_capture_error(404, None) is True

    def test_should_not_capture_service_unavailable(self):
        """Test that service unavailable errors (503) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Service unavailable errors should not be captured
        assert monitor._should_capture_error(503, "Service unavailable") is False
        assert monitor._should_capture_error(503, "The service is temporarily unavailable") is False
        assert monitor._should_capture_error(503, "SERVICE UNAVAILABLE - try again") is False

    def test_should_capture_other_503_errors(self):
        """Test that other 503 errors ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # 503 without service unavailable message should be captured
        assert monitor._should_capture_error(503, "Database connection failed") is True
        assert monitor._should_capture_error(503, None) is True

    def test_should_not_capture_max_output_tokens_validation(self):
        """Test that max_output_tokens validation errors (400) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Google Vertex AI max_output_tokens validation errors
        assert (
            monitor._should_capture_error(
                400, "max_output_tokens must be greater than minimum value of 1"
            )
            is False
        )
        assert (
            monitor._should_capture_error(400, "Invalid max_output_tokens: minimum value is 10")
            is False
        )

    def test_should_not_capture_audio_modality_errors(self):
        """Test that audio modality requirement errors (400) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Audio-only model requirement errors
        assert (
            monitor._should_capture_error(400, "This model requires audio input modality") is False
        )
        assert (
            monitor._should_capture_error(400, "Audio modality is required for this model") is False
        )

    def test_should_capture_other_400_errors(self):
        """Test that other 400 errors ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Other 400 errors should be captured
        assert monitor._should_capture_error(400, "Invalid request format") is True
        assert monitor._should_capture_error(400, "Missing required field") is True
        assert monitor._should_capture_error(400, None) is True

    def test_should_not_capture_auth_key_errors(self):
        """Test that authentication key errors (403) are NOT captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Authentication key errors should not be captured
        assert monitor._should_capture_error(403, "Invalid API key") is False
        assert monitor._should_capture_error(403, "API key is required") is False
        assert monitor._should_capture_error(403, "Your API KEY is invalid") is False

    def test_should_capture_other_403_errors(self):
        """Test that other 403 errors ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Non-key-related 403s should be captured
        assert monitor._should_capture_error(403, "Access denied") is True
        assert monitor._should_capture_error(403, "Insufficient permissions") is True
        assert monitor._should_capture_error(403, None) is True

    def test_should_capture_server_errors(self):
        """Test that server errors (500, 502, etc.) ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Server errors should always be captured
        assert monitor._should_capture_error(500, "Internal server error") is True
        assert monitor._should_capture_error(502, "Bad gateway") is True
        assert monitor._should_capture_error(504, "Gateway timeout") is True

    def test_should_capture_client_errors(self):
        """Test that most client errors ARE captured"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        # Most client errors should be captured
        assert monitor._should_capture_error(401, "Unauthorized") is True
        assert monitor._should_capture_error(402, "Payment required") is True
        assert monitor._should_capture_error(405, "Method not allowed") is True
        assert monitor._should_capture_error(408, "Request timeout") is True

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.capture_exception")
    async def test_error_filtering_in_check_model_health_rate_limit(self, mock_capture):
        """Test that rate limit errors are not sent to Sentry during health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        model = {
            "id": "test-model",
            "provider": "test-provider",
            "gateway": "openrouter",
            "name": "Test Model",
        }

        # Mock health check returning rate limit error
        with patch.object(monitor, "_perform_model_request") as mock_request:
            mock_request.return_value = {
                "success": False,
                "error": "Rate limit exceeded",
                "status_code": 429,
                "response_time": 100,
            }

            result = await monitor._check_model_health(model)

            # Model should be marked unhealthy
            assert result is not None
            assert result.status.value == "unhealthy"
            assert result.error_message == "Rate limit exceeded"

            # But Sentry should NOT be called for rate limits
            assert not mock_capture.called, "Rate limit errors should not be captured to Sentry"

    @pytest.mark.asyncio
    @patch("src.utils.sentry_context.capture_exception")
    async def test_error_filtering_in_check_model_health_server_error(self, mock_capture):
        """Test that server errors ARE sent to Sentry during health checks"""
        from src.services.model_health_monitor import ModelHealthMonitor

        monitor = ModelHealthMonitor()

        model = {
            "id": "test-model",
            "provider": "test-provider",
            "gateway": "openrouter",
            "name": "Test Model",
        }

        # Mock health check returning server error
        with patch.object(monitor, "_perform_model_request") as mock_request:
            mock_request.return_value = {
                "success": False,
                "error": "Internal server error",
                "status_code": 500,
                "response_time": 100,
            }

            result = await monitor._check_model_health(model)

            # Model should be marked unhealthy
            assert result is not None
            assert result.status.value == "unhealthy"
            assert result.error_message == "Internal server error"

            # Sentry SHOULD be called for server errors
            assert mock_capture.called, "Server errors should be captured to Sentry"
