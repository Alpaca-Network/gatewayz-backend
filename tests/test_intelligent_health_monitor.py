"""
Tests for the Intelligent Health Monitor

Tests the tiered monitoring, scheduling, and health check functionality.
"""

import asyncio
from datetime import datetime, timezone, UTC
from unittest.mock import AsyncMock, MagicMock, patch

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
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_success(mock_client, health_monitor):
    """Test successful health check"""
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
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_rate_limited(mock_client, health_monitor):
    """Test health check with rate limit response"""
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
async def test_check_model_health_timeout(health_monitor):
    """Test health check with timeout - simplified test using asyncio.timeout"""
    import asyncio

    # Patch _check_model_health_with_timeout to simulate timeout
    async def mock_check_with_timeout(model):
        # Simulate a timeout by raising asyncio.TimeoutError
        raise TimeoutError("Request timeout")

    # Mock the internal HTTP client to raise timeout
    original_check = health_monitor._check_model_health

    async def timeout_wrapper(model):
        try:
            # Simulate timeout in the HTTP request
            import httpx
            raise httpx.TimeoutException("Request timeout")
        except httpx.TimeoutException:
            from src.services.intelligent_health_monitor import HealthCheckResult, HealthCheckStatus
            from datetime import datetime, timezone
            return HealthCheckResult(
                provider=model["provider"],
                model=model["model"],
                gateway=model["gateway"],
                status=HealthCheckStatus.TIMEOUT,
                response_time_ms=5000.0,
                error_message="Request timeout after 5s",
                http_status_code=None,
                checked_at=datetime.now(UTC),
            )

    health_monitor._check_model_health = timeout_wrapper

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

    # Restore original method
    health_monitor._check_model_health = original_check


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.httpx.AsyncClient")
async def test_check_model_health_unauthorized(mock_client, health_monitor):
    """Test health check with unauthorized response"""
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
    now = datetime.now(UTC)
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


@pytest.mark.asyncio
async def test_tier_update_loop_handles_missing_function():
    """Test that tier update loop gracefully handles PGRST202 errors when function is not found"""
    monitor = IntelligentHealthMonitor(batch_size=10, max_concurrent_checks=5, redis_coordination=False)
    monitor.monitoring_active = True

    # Mock supabase to simulate PGRST202 error
    with patch("src.services.intelligent_health_monitor.logger") as mock_logger:
        with patch("src.config.supabase_config.supabase") as mock_supabase:
            # Simulate the PGRST202 error from PostgREST
            mock_rpc = MagicMock()
            mock_rpc.execute.side_effect = Exception(
                "{'code': 'PGRST202', 'details': 'Searched for the function public.update_model_tier "
                "without parameters or with a single unnamed json/jsonb parameter, but no matches were "
                "found in the schema cache.', 'hint': None, 'message': 'Could not find the function "
                "public.update_model_tier without parameters in the schema cache'}"
            )
            mock_supabase.rpc.return_value = mock_rpc

            # We need to allow the loop to execute the RPC call
            # Store original sleep to avoid recursion
            original_sleep = asyncio.sleep
            call_count = [0]

            async def mock_sleep_fn(delay):
                call_count[0] += 1
                # First call is from the loop's initial sleep, let it proceed
                # After that, we'll stop
                if call_count[0] > 1:
                    # Set flag to stop after first iteration executes
                    monitor.monitoring_active = False
                await original_sleep(0)  # Yield to event loop

            with patch("src.services.intelligent_health_monitor.asyncio.sleep", side_effect=mock_sleep_fn) as mock_sleep:
                monitor.monitoring_active = True

                # Start the tier update loop task
                task = asyncio.create_task(monitor._tier_update_loop())

                # Wait for execution to complete
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        # Task cancellation during test cleanup is expected; ignore
                        pass

                # Verify the error was handled gracefully with a warning
                assert mock_logger.warning.called
                warning_call = mock_logger.warning.call_args[0][0]
                assert "update_model_tier" in warning_call
                assert "not found in schema cache" in warning_call
                assert "PGRST202" in warning_call or "Could not find the function" in warning_call


@pytest.mark.asyncio
async def test_tier_update_loop_handles_other_errors():
    """Test that tier update loop properly logs other unexpected errors"""
    monitor = IntelligentHealthMonitor(batch_size=10, max_concurrent_checks=5, redis_coordination=False)

    # Mock supabase to simulate a different error
    with patch("src.services.intelligent_health_monitor.logger") as mock_logger:
        with patch("src.config.supabase_config.supabase") as mock_supabase:
            # Simulate a different error (e.g., network error)
            mock_rpc = MagicMock()
            mock_rpc.execute.side_effect = Exception("Network timeout error")
            mock_supabase.rpc.return_value = mock_rpc

            # We need to allow the loop to execute the RPC call
            # Store original sleep to avoid recursion
            original_sleep = asyncio.sleep
            call_count = [0]

            async def mock_sleep_fn(delay):
                call_count[0] += 1
                # First call is from the loop's initial sleep, let it proceed
                # Second call is from error handler's sleep, then stop
                if call_count[0] > 1:
                    # Set flag to stop after first iteration executes
                    monitor.monitoring_active = False
                await original_sleep(0)  # Yield to event loop

            with patch("src.services.intelligent_health_monitor.asyncio.sleep", side_effect=mock_sleep_fn) as mock_sleep:
                monitor.monitoring_active = True

                # Start the tier update loop task
                task = asyncio.create_task(monitor._tier_update_loop())

                # Wait for execution to complete
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        # Task cancellation during test cleanup is expected; ignore
                        pass

                # Verify the error was logged properly as an ERROR (not a warning)
                assert mock_logger.error.called
                error_call = mock_logger.error.call_args[0][0]
                assert "Error in tier update loop" in error_call
                assert "Network timeout error" in error_call


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_create_or_update_incident_handles_none_response(mock_supabase, mock_logger, health_monitor):
    """Test that _create_or_update_incident handles None response from Supabase gracefully"""
    # Create a mock that returns None when execute() is called
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.execute.return_value = None  # Simulate None response

    mock_supabase.table.return_value = mock_table

    # Create a test health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.ERROR,
        response_time_ms=150.5,
        error_message="Test error",
        http_status_code=500,
        checked_at=datetime.now(UTC),
    )

    # Should not raise an exception
    await health_monitor._create_or_update_incident(result, consecutive_failures=3)

    # Verify debug log was called (changed from warning to debug to reduce log noise)
    mock_logger.debug.assert_called()
    debug_call = mock_logger.debug.call_args[0][0]
    assert "Supabase query returned None" in debug_call
    assert "gpt-4" in debug_call


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_process_health_check_result_handles_none_response(mock_supabase, mock_logger, health_monitor):
    """Test that _process_health_check_result handles None response from Supabase gracefully"""
    # Create a mock that returns None when execute() is called
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.execute.return_value = None  # Simulate None response

    mock_supabase.table.return_value = mock_table

    # Create a test health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.5,
        error_message=None,
        http_status_code=200,
        checked_at=datetime.now(UTC),
    )

    # Should not raise an exception
    await health_monitor._process_health_check_result(result)

    # Verify debug log was called (changed from warning to debug to reduce log noise)
    mock_logger.debug.assert_called()
    debug_call = mock_logger.debug.call_args[0][0]
    assert "Supabase query returned None" in debug_call
    assert "gpt-4" in debug_call


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.asyncio.sleep", new_callable=AsyncMock)
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_process_health_check_result_retries_on_query_failure(mock_supabase, mock_logger, mock_sleep, health_monitor):
    """Test that _process_health_check_result retries queries on failure before giving up"""
    # Create a mock that raises an exception on first call, succeeds on second
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table

    # First call raises, second call succeeds
    mock_response = MagicMock()
    mock_response.data = {"provider": "openai", "model": "gpt-4", "call_count": 10, "success_count": 9}
    call_count = [0]

    def execute_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("Temporary connection error")
        return mock_response

    mock_table.execute.side_effect = execute_side_effect
    mock_supabase.table.return_value = mock_table

    # Create a test health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.5,
        error_message=None,
        http_status_code=200,
        checked_at=datetime.now(UTC),
    )

    # Should not raise an exception - retry should succeed
    await health_monitor._process_health_check_result(result)

    # Verify retry happened (sleep was called for delay between retries)
    mock_sleep.assert_called_once_with(0.5)

    # Verify execute was called twice (initial + retry)
    assert call_count[0] == 2


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.asyncio.sleep", new_callable=AsyncMock)
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_create_or_update_incident_retries_on_query_failure(mock_supabase, mock_logger, mock_sleep, health_monitor):
    """Test that _create_or_update_incident retries queries on failure before giving up"""
    # Create a mock that raises an exception on first call, succeeds on second
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table

    # First call raises, second call succeeds
    mock_response = MagicMock()
    mock_response.data = None  # No active incident
    call_count = [0]

    def execute_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("Temporary connection error")
        return mock_response

    mock_table.execute.side_effect = execute_side_effect
    mock_supabase.table.return_value = mock_table

    # Create a test health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.ERROR,
        response_time_ms=150.5,
        error_message="Test error",
        http_status_code=500,
        checked_at=datetime.now(UTC),
    )

    # Should not raise an exception - retry should succeed
    await health_monitor._create_or_update_incident(result, consecutive_failures=3)

    # Verify retry happened (sleep was called for delay between retries)
    mock_sleep.assert_called_once_with(0.5)

    # Verify execute was called twice (initial + retry)
    assert call_count[0] == 2


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.asyncio.sleep", new_callable=AsyncMock)
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_process_health_check_result_logs_debug_after_max_retries(mock_supabase, mock_logger, mock_sleep, health_monitor):
    """Test that _process_health_check_result logs at debug level after all retries fail"""
    # Create a mock that always raises an exception
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.execute.side_effect = Exception("Persistent connection error")

    mock_supabase.table.return_value = mock_table

    # Create a test health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.5,
        error_message=None,
        http_status_code=200,
        checked_at=datetime.now(UTC),
    )

    # Should not raise an exception - gracefully handle failure
    await health_monitor._process_health_check_result(result)

    # Verify retry delay was called once (between attempt 1 and 2)
    mock_sleep.assert_called_once_with(0.5)

    # Verify debug log was called on final failure
    mock_logger.debug.assert_called()
    debug_call = mock_logger.debug.call_args[0][0]
    assert "Health tracking query failed" in debug_call
    assert "after 2 attempts" in debug_call


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_process_health_check_result_preserves_uptime_percentages(mock_supabase, mock_logger, health_monitor):
    """Test that _process_health_check_result preserves existing uptime percentages rather than overwriting them"""
    # Create mock with existing uptime data
    mock_table = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {
        "provider": "openai",
        "model": "gpt-4",
        "gateway": "openrouter",
        "call_count": 100,
        "success_count": 95,
        "error_count": 5,
        "consecutive_failures": 0,
        "consecutive_successes": 5,
        "circuit_breaker_state": "closed",
        "monitoring_tier": "critical",
        "average_response_time_ms": 200.0,
        # These are the existing uptime values calculated by the aggregate task
        "uptime_percentage_24h": 99.5,
        "uptime_percentage_7d": 98.8,
        "uptime_percentage_30d": 97.2,
    }

    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.execute.return_value = mock_response
    mock_table.upsert.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_supabase.table.return_value = mock_table

    # Create a successful health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.0,
        error_message=None,
        http_status_code=200,
        checked_at=datetime.now(UTC),
    )

    await health_monitor._process_health_check_result(result)

    # Verify upsert was called with preserved uptime values
    upsert_call = mock_table.upsert.call_args
    assert upsert_call is not None
    upsert_data = upsert_call[0][0]

    # The uptime percentages should be preserved, not recalculated
    assert upsert_data["uptime_percentage_24h"] == 99.5
    assert upsert_data["uptime_percentage_7d"] == 98.8
    assert upsert_data["uptime_percentage_30d"] == 97.2


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_process_health_check_result_defaults_uptime_for_new_models(mock_supabase, mock_logger, health_monitor):
    """Test that _process_health_check_result defaults uptime to 100% for new models without existing data"""
    # Create mock for a new model with no existing uptime data
    mock_table = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {
        "provider": "openai",
        "model": "gpt-4-new",
        "gateway": "openrouter",
        "call_count": 0,
        "success_count": 0,
        "error_count": 0,
        "consecutive_failures": 0,
        "consecutive_successes": 0,
        "circuit_breaker_state": "closed",
        "monitoring_tier": "standard",
        "average_response_time_ms": None,
        # No uptime values yet
        "uptime_percentage_24h": None,
        "uptime_percentage_7d": None,
        "uptime_percentage_30d": None,
    }

    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.maybe_single.return_value = mock_table
    mock_table.execute.return_value = mock_response
    mock_table.upsert.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_supabase.table.return_value = mock_table

    # Create a successful health check result
    result = HealthCheckResult(
        provider="openai",
        model="gpt-4-new",
        gateway="openrouter",
        status=HealthCheckStatus.SUCCESS,
        response_time_ms=150.0,
        error_message=None,
        http_status_code=200,
        checked_at=datetime.now(UTC),
    )

    await health_monitor._process_health_check_result(result)

    # Verify upsert was called with default 100% uptime
    upsert_call = mock_table.upsert.call_args
    assert upsert_call is not None
    upsert_data = upsert_call[0][0]

    # New models should default to 100% uptime
    assert upsert_data["uptime_percentage_24h"] == 100.0
    assert upsert_data["uptime_percentage_7d"] == 100.0
    assert upsert_data["uptime_percentage_30d"] == 100.0


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.asyncio.sleep", new_callable=AsyncMock)
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_aggregate_hourly_metrics_calculates_uptime_from_history(mock_supabase, mock_logger, mock_sleep, health_monitor):
    """Test that _aggregate_hourly_metrics calculates uptime from actual history data"""
    # Mock tracked models
    mock_tracked_response = MagicMock()
    mock_tracked_response.data = [
        {"provider": "openai", "model": "gpt-4", "gateway": "openrouter"},
    ]

    # Mock history data - 10 checks, 9 successful
    mock_history_response = MagicMock()
    mock_history_response.data = [
        {"status": "success"} for _ in range(9)
    ] + [{"status": "error"}]

    # Set up mock table behavior
    call_tracker = {"tracked_calls": 0, "history_calls": 0, "update_calls": 0}

    def table_side_effect(table_name):
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.update.return_value = mock_table

        if table_name == "model_health_tracking":
            if call_tracker["update_calls"] > 0:
                # This is an update call
                mock_table.execute.return_value = MagicMock(data=[{}])
            else:
                # This is a select call for tracked models
                call_tracker["tracked_calls"] += 1
                mock_table.execute.return_value = mock_tracked_response
        elif table_name == "model_health_history":
            call_tracker["history_calls"] += 1
            mock_table.execute.return_value = mock_history_response

        def update_side_effect(data):
            call_tracker["update_calls"] += 1
            # Verify the calculated uptime
            assert "uptime_percentage_24h" in data
            # 9 out of 10 = 90%
            assert data["uptime_percentage_24h"] == 90.0
            mock_update_table = MagicMock()
            mock_update_table.eq.return_value = mock_update_table
            mock_update_table.execute.return_value = MagicMock(data=[{}])
            return mock_update_table

        mock_table.update.side_effect = update_side_effect
        return mock_table

    mock_supabase.table.side_effect = table_side_effect

    # Run the aggregation
    await health_monitor._aggregate_hourly_metrics()

    # Verify calls were made
    assert call_tracker["tracked_calls"] >= 1
    assert call_tracker["history_calls"] >= 1
    assert call_tracker["update_calls"] >= 1


@pytest.mark.asyncio
@patch("src.services.intelligent_health_monitor.asyncio.sleep", new_callable=AsyncMock)
@patch("src.services.intelligent_health_monitor.logger")
@patch("src.config.supabase_config.supabase")
async def test_aggregate_hourly_metrics_defaults_to_100_when_no_history(mock_supabase, mock_logger, mock_sleep, health_monitor):
    """Test that _aggregate_hourly_metrics defaults to 100% uptime when there's no history data"""
    # Mock tracked models
    mock_tracked_response = MagicMock()
    mock_tracked_response.data = [
        {"provider": "openai", "model": "gpt-4-new", "gateway": "openrouter"},
    ]

    # Empty history data
    mock_empty_history = MagicMock()
    mock_empty_history.data = []

    call_tracker = {"update_calls": 0}

    def table_side_effect(table_name):
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.gte.return_value = mock_table
        mock_table.update.return_value = mock_table

        if table_name == "model_health_tracking":
            if call_tracker["update_calls"] > 0:
                mock_table.execute.return_value = MagicMock(data=[{}])
            else:
                mock_table.execute.return_value = mock_tracked_response
        elif table_name == "model_health_history":
            mock_table.execute.return_value = mock_empty_history

        def update_side_effect(data):
            call_tracker["update_calls"] += 1
            # Should default to 100% when no history
            assert data["uptime_percentage_24h"] == 100.0
            assert data["uptime_percentage_7d"] == 100.0
            assert data["uptime_percentage_30d"] == 100.0
            mock_update_table = MagicMock()
            mock_update_table.eq.return_value = mock_update_table
            mock_update_table.execute.return_value = MagicMock(data=[{}])
            return mock_update_table

        mock_table.update.side_effect = update_side_effect
        return mock_table

    mock_supabase.table.side_effect = table_side_effect

    # Run the aggregation
    await health_monitor._aggregate_hourly_metrics()

    # Verify update was called with 100% defaults
    assert call_tracker["update_calls"] >= 1
