"""
Tests for the Intelligent Health Monitor

Tests the tiered monitoring, scheduling, and health check functionality.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.services.intelligent_health_monitor import (
    CircuitBreakerState,
    HealthCheckResult,
    HealthCheckStatus,
    IntelligentHealthMonitor,
    MonitoringTier,
)


@pytest.fixture
def health_monitor():
    """Create a health monitor instance for testing"""
    return IntelligentHealthMonitor(batch_size=10, max_concurrent_checks=5, redis_coordination=False)


@pytest.mark.asyncio
async def test_health_monitor_initialization(health_monitor):
    """Test health monitor initializes correctly"""
    assert health_monitor.batch_size == 10
    assert health_monitor.max_concurrent_checks == 5
    assert health_monitor.redis_coordination is False
    assert health_monitor.monitoring_active is False


@pytest.mark.asyncio
async def test_tier_configuration(health_monitor):
    """Test tier configuration is set correctly"""
    assert MonitoringTier.CRITICAL in health_monitor.tier_config
    assert MonitoringTier.POPULAR in health_monitor.tier_config
    assert MonitoringTier.STANDARD in health_monitor.tier_config
    assert MonitoringTier.ON_DEMAND in health_monitor.tier_config

    # Check intervals are correct
    assert health_monitor.tier_config[MonitoringTier.CRITICAL]["interval_seconds"] == 300
    assert health_monitor.tier_config[MonitoringTier.POPULAR]["interval_seconds"] == 1800
    assert health_monitor.tier_config[MonitoringTier.STANDARD]["interval_seconds"] == 7200
    assert health_monitor.tier_config[MonitoringTier.ON_DEMAND]["interval_seconds"] == 14400


@pytest.mark.asyncio
async def test_get_gateway_endpoint(health_monitor):
    """Test gateway endpoint URL mapping"""
    assert health_monitor._get_gateway_endpoint("openrouter") == "https://openrouter.ai/api/v1/chat/completions"
    assert health_monitor._get_gateway_endpoint("featherless") == "https://api.featherless.ai/v1/chat/completions"
    assert health_monitor._get_gateway_endpoint("groq") == "https://api.groq.com/openai/v1/chat/completions"
    assert health_monitor._get_gateway_endpoint("unknown") is None


@pytest.mark.asyncio
async def test_calculate_circuit_breaker_state_closed_to_open(health_monitor):
    """Test circuit breaker transitions from closed to open"""
    # Should stay closed with few failures
    state = health_monitor._calculate_circuit_breaker_state("closed", consecutive_failures=3, consecutive_successes=0)
    assert state == CircuitBreakerState.CLOSED

    # Should open with 5+ failures
    state = health_monitor._calculate_circuit_breaker_state("closed", consecutive_failures=5, consecutive_successes=0)
    assert state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
async def test_calculate_circuit_breaker_state_half_open_to_closed(health_monitor):
    """Test circuit breaker transitions from half_open to closed"""
    # Should close after 3 successes
    state = health_monitor._calculate_circuit_breaker_state("half_open", consecutive_failures=0, consecutive_successes=3)
    assert state == CircuitBreakerState.CLOSED

    # Should stay half_open with fewer successes
    state = health_monitor._calculate_circuit_breaker_state("half_open", consecutive_failures=0, consecutive_successes=2)
    assert state == CircuitBreakerState.HALF_OPEN


@pytest.mark.asyncio
async def test_calculate_circuit_breaker_state_half_open_to_open(health_monitor):
    """Test circuit breaker transitions from half_open back to open on failure"""
    state = health_monitor._calculate_circuit_breaker_state("half_open", consecutive_failures=1, consecutive_successes=0)
    assert state == CircuitBreakerState.OPEN


@pytest.mark.asyncio
async def test_determine_incident_severity(health_monitor):
    """Test incident severity determination based on consecutive failures"""
    from src.services.intelligent_health_monitor import IncidentSeverity

    assert health_monitor._determine_incident_severity(1) == IncidentSeverity.LOW
    assert health_monitor._determine_incident_severity(3) == IncidentSeverity.MEDIUM
    assert health_monitor._determine_incident_severity(5) == IncidentSeverity.HIGH
    assert health_monitor._determine_incident_severity(10) == IncidentSeverity.CRITICAL


@pytest.mark.asyncio
async def test_map_status_to_incident_type(health_monitor):
    """Test mapping health check status to incident type"""
    assert health_monitor._map_status_to_incident_type(HealthCheckStatus.ERROR) == "outage"
    assert health_monitor._map_status_to_incident_type(HealthCheckStatus.TIMEOUT) == "timeout"
    assert health_monitor._map_status_to_incident_type(HealthCheckStatus.RATE_LIMITED) == "rate_limit"
    assert health_monitor._map_status_to_incident_type(HealthCheckStatus.UNAUTHORIZED) == "authentication"
    assert health_monitor._map_status_to_incident_type(HealthCheckStatus.NOT_FOUND) == "unavailable"


@pytest.mark.asyncio
@patch("src.config.config.Config")
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_success(mock_config, mock_client, health_monitor):
    """Test successful health check"""
    # Mock Config attributes
    type(mock_config).OPENROUTER_API_KEY = PropertyMock(return_value="test-key")

    # Mock successful HTTP response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"choices": [{"message": {"content": "test"}}]}

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock()
    mock_client.return_value = mock_client_instance

    model = {
        "provider": "openai",
        "model": "gpt-4",
        "gateway": "openrouter",
        "monitoring_tier": "critical",
    }

    result = await health_monitor._check_model_health(model)

    assert result is not None
    assert result.provider == "openai"
    assert result.model == "gpt-4"
    assert result.gateway == "openrouter"
    assert result.status == HealthCheckStatus.SUCCESS
    assert result.response_time_ms is not None
    assert result.response_time_ms > 0


@pytest.mark.asyncio
@patch("src.config.config.Config")
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_rate_limited(mock_config, mock_client, health_monitor):
    """Test health check with rate limit response"""
    # Mock Config attributes
    type(mock_config).OPENROUTER_API_KEY = PropertyMock(return_value="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limit exceeded"

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock()
    mock_client.return_value = mock_client_instance

    model = {
        "provider": "openai",
        "model": "gpt-4",
        "gateway": "openrouter",
        "monitoring_tier": "critical",
    }

    result = await health_monitor._check_model_health(model)

    assert result is not None
    assert result.status == HealthCheckStatus.RATE_LIMITED
    assert result.error_message == "Rate limit exceeded"


@pytest.mark.asyncio
@patch("src.config.config.Config")
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_timeout(mock_config, mock_client, health_monitor):
    """Test health check with timeout"""
    import httpx

    # Mock Config attributes - use getattr to match actual implementation
    type(mock_config).OPENROUTER_API_KEY = PropertyMock(return_value="test-key")

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(side_effect=httpx.TimeoutException("Request timeout"))
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock()
    mock_client.return_value = mock_client_instance

    model = {
        "provider": "openai",
        "model": "gpt-4",
        "gateway": "openrouter",
        "monitoring_tier": "critical",
    }

    result = await health_monitor._check_model_health(model)

    assert result is not None
    assert result.status == HealthCheckStatus.TIMEOUT
    assert "timeout" in result.error_message.lower()


@pytest.mark.asyncio
@patch("src.config.config.Config")
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_unauthorized(mock_config, mock_client, health_monitor):
    """Test health check with unauthorized response"""
    # Mock Config attributes
    type(mock_config).OPENROUTER_API_KEY = PropertyMock(return_value="test-key")

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    mock_client_instance = MagicMock()
    mock_client_instance.post = AsyncMock(return_value=mock_response)
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock()
    mock_client.return_value = mock_client_instance

    model = {
        "provider": "openai",
        "model": "gpt-4",
        "gateway": "openrouter",
        "monitoring_tier": "critical",
    }

    result = await health_monitor._check_model_health(model)

    assert result is not None
    assert result.status == HealthCheckStatus.UNAUTHORIZED
    assert result.error_message == "Authentication failed"


@pytest.mark.asyncio
async def test_health_check_result_creation():
    """Test HealthCheckResult dataclass creation"""
    now = datetime.now(timezone.utc)
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.5,
        error_message=None,
        http_status_code=200,
        checked_at=now,
    )

    assert result.provider == "openai"
    assert result.model == "gpt-4"
    assert result.gateway == "openrouter"
    assert result.status == HealthCheckStatus.SUCCESS
    assert result.response_time_ms == 150.5
    assert result.error_message is None
    assert result.http_status_code == 200
    assert result.checked_at == now


@pytest.mark.asyncio
async def test_monitoring_tier_enum():
    """Test MonitoringTier enum values"""
    assert MonitoringTier.CRITICAL.value == "critical"
    assert MonitoringTier.POPULAR.value == "popular"
    assert MonitoringTier.STANDARD.value == "standard"
    assert MonitoringTier.ON_DEMAND.value == "on_demand"


@pytest.mark.asyncio
async def test_health_check_status_enum():
    """Test HealthCheckStatus enum values"""
    assert HealthCheckStatus.SUCCESS.value == "success"
    assert HealthCheckStatus.ERROR.value == "error"
    assert HealthCheckStatus.TIMEOUT.value == "timeout"
    assert HealthCheckStatus.RATE_LIMITED.value == "rate_limited"
    assert HealthCheckStatus.UNAUTHORIZED.value == "unauthorized"
    assert HealthCheckStatus.NOT_FOUND.value == "not_found"


@pytest.mark.asyncio
async def test_circuit_breaker_state_enum():
    """Test CircuitBreakerState enum values"""
    assert CircuitBreakerState.CLOSED.value == "closed"
    assert CircuitBreakerState.OPEN.value == "open"
    assert CircuitBreakerState.HALF_OPEN.value == "half_open"


@pytest.mark.asyncio
@patch("src.config.supabase_config.supabase")
async def test_get_models_for_checking_empty(mock_supabase, health_monitor):
    """Test getting models for checking when none are due"""
    mock_response = MagicMock()
    mock_response.data = []

    mock_supabase.table.return_value.select.return_value.eq.return_value.lte.return_value.order.return_value.order.return_value.limit.return_value.execute.return_value = (
        mock_response
    )

    models = await health_monitor._get_models_for_checking()
    assert models == []


@pytest.mark.asyncio
async def test_start_monitoring(health_monitor):
    """Test starting the monitoring service"""
    # Start monitoring
    await health_monitor.start_monitoring()

    assert health_monitor.monitoring_active is True
    assert health_monitor._worker_id is not None

    # Stop it to clean up
    await health_monitor.stop_monitoring()


@pytest.mark.asyncio
async def test_stop_monitoring(health_monitor):
    """Test stopping the monitoring service"""
    await health_monitor.start_monitoring()
    assert health_monitor.monitoring_active is True

    await health_monitor.stop_monitoring()
    assert health_monitor.monitoring_active is False


@pytest.mark.asyncio
async def test_concurrent_check_limiting(health_monitor):
    """Test that concurrent checks are limited by semaphore"""
    # The semaphore should limit concurrent execution
    assert health_monitor._semaphore._value == health_monitor.max_concurrent_checks
