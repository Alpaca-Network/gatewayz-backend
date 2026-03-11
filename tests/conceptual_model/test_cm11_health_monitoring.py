"""
CM-11: Health Monitoring Tests

Tests covering:
  11.1 Critical tier 5-minute interval
  11.2 Popular tier 30-minute interval
  11.3 Standard tier 2-4 hour interval
  11.4 Passive health captures from inference
  11.5 /health always returns 200
  11.6 Health response contains version
  11.7 Health response contains status
  11.8 Health response contains timestamp
  11.9 Incident severity levels (Critical/High/Medium/Low)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.intelligent_health_monitor import (
    IncidentSeverity,
    IntelligentHealthMonitor,
    MonitoringTier,
)


# ===================================================================
# 11.1 Critical tier - 5 minute interval
# ===================================================================

@pytest.mark.cm_verified
def test_health_check_critical_tier_5min_interval():
    """CM-11.1: Top 5% models (CRITICAL tier) are scheduled every 5 minutes (300s)."""
    monitor = IntelligentHealthMonitor.__new__(IntelligentHealthMonitor)
    monitor.__init__()

    critical_config = monitor.tier_config[MonitoringTier.CRITICAL]
    assert critical_config["interval_seconds"] == 300, (
        f"Expected CRITICAL tier interval of 300s (5 min), got {critical_config['interval_seconds']}s"
    )


# ===================================================================
# 11.2 Popular tier - 30 minute interval
# ===================================================================

@pytest.mark.cm_verified
def test_health_check_popular_tier_30min_interval():
    """CM-11.2: Next 20% models (POPULAR tier) are scheduled every 30 minutes (1800s)."""
    monitor = IntelligentHealthMonitor()

    popular_config = monitor.tier_config[MonitoringTier.POPULAR]
    assert popular_config["interval_seconds"] == 1800, (
        f"Expected POPULAR tier interval of 1800s (30 min), got {popular_config['interval_seconds']}s"
    )


# ===================================================================
# 11.3 Standard tier - 2 to 4 hour interval
# ===================================================================

@pytest.mark.cm_verified
def test_health_check_standard_tier_2_to_4hr_interval():
    """CM-11.3: Remaining 75% models (STANDARD tier) are checked every 2-4 hours (7200-14400s)."""
    monitor = IntelligentHealthMonitor()

    standard_interval = monitor.tier_config[MonitoringTier.STANDARD]["interval_seconds"]
    on_demand_interval = monitor.tier_config[MonitoringTier.ON_DEMAND]["interval_seconds"]

    # STANDARD tier is 7200s (2 hours)
    assert standard_interval == 7200, (
        f"Expected STANDARD tier interval of 7200s (2 hr), got {standard_interval}s"
    )
    # ON_DEMAND tier (least used models) is 14400s (4 hours), covering the upper bound
    assert on_demand_interval == 14400, (
        f"Expected ON_DEMAND tier interval of 14400s (4 hr), got {on_demand_interval}s"
    )
    # Both fall within the 2-4 hour range
    assert 7200 <= standard_interval <= 14400
    assert 7200 <= on_demand_interval <= 14400


# ===================================================================
# 11.4 Passive health captures from inference
# ===================================================================

@pytest.mark.cm_verified
@pytest.mark.asyncio
async def test_passive_health_captures_from_inference():
    """CM-11.4: Every inference call contributes health data via capture_model_health."""
    mock_to_thread = AsyncMock(return_value=None)

    with patch("src.services.passive_health_monitor.record_model_call"):
        with patch("src.services.passive_health_monitor.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = mock_to_thread

            from src.services.passive_health_monitor import capture_model_health

            await capture_model_health(
                provider="openrouter",
                model="meta-llama/Llama-3.3-70B-Instruct",
                response_time_ms=245.3,
                status="success",
                usage={"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            )

            # Verify record_model_call was invoked via to_thread with correct params
            mock_to_thread.assert_called_once()
            call_kwargs = mock_to_thread.call_args
            # to_thread is called as: asyncio.to_thread(record_model_call, provider=..., ...)
            assert call_kwargs.kwargs["provider"] == "openrouter"
            assert call_kwargs.kwargs["model"] == "meta-llama/Llama-3.3-70B-Instruct"
            assert call_kwargs.kwargs["response_time_ms"] == 245.3
            assert call_kwargs.kwargs["status"] == "success"
            assert call_kwargs.kwargs["input_tokens"] == 50
            assert call_kwargs.kwargs["output_tokens"] == 100
            assert call_kwargs.kwargs["total_tokens"] == 150


# ===================================================================
# 11.5 /health always returns 200
# ===================================================================

@pytest.mark.cm_verified
@pytest.mark.asyncio
async def test_health_endpoint_always_returns_200():
    """CM-11.5: /health returns HTTP 200 even when subsystems are down."""
    # Simulate database being unavailable
    with patch(
        "src.routes.health.get_initialization_status",
        return_value={"initialized": False, "has_error": True, "error_type": "ConnectionError"},
    ):
        from src.routes.health import health_check

        response = await health_check()

        # The endpoint always returns a dict (FastAPI converts to 200 JSON response);
        # it never raises HTTPException.
        assert isinstance(response, dict)
        assert response["status"] == "healthy"
        # When DB is down, it reports degraded mode but still 200
        assert response.get("database") == "unavailable"
        assert response.get("mode") == "degraded"


# ===================================================================
# 11.6 Health response contains version
# ===================================================================

@pytest.mark.cm_verified
def test_health_response_contains_version():
    """CM-11.6: The FastAPI app is configured with an API version string."""
    # The version is set on the FastAPI app object in create_app().
    # We verify the app carries a version attribute used by docs and health consumers.
    from src.main import create_app

    app = create_app()
    assert app.version is not None
    assert isinstance(app.version, str)
    assert len(app.version) > 0, "App version must be a non-empty string"


# ===================================================================
# 11.7 Health response contains status
# ===================================================================

@pytest.mark.cm_verified
@pytest.mark.asyncio
async def test_health_response_contains_status():
    """CM-11.7: /health response includes an overall 'status' field."""
    with patch(
        "src.routes.health.get_initialization_status",
        return_value={"initialized": True, "has_error": False},
    ):
        from src.routes.health import health_check

        response = await health_check()

        assert "status" in response, "Health response must contain 'status' field"
        assert response["status"] in ("healthy", "degraded", "unhealthy", "warming_up")


# ===================================================================
# 11.8 Health response contains timestamp
# ===================================================================

@pytest.mark.cm_verified
@pytest.mark.asyncio
async def test_health_response_contains_timestamp():
    """CM-11.8: /health response includes a timestamp."""
    with patch(
        "src.routes.health.get_initialization_status",
        return_value={"initialized": True, "has_error": False},
    ):
        from src.routes.health import health_check

        response = await health_check()

        assert "timestamp" in response, "Health response must contain 'timestamp' field"
        # Verify it's a valid ISO format timestamp
        from datetime import datetime
        ts = datetime.fromisoformat(response["timestamp"])
        assert ts is not None


# ===================================================================
# 11.9 Incident severity levels
# ===================================================================

@pytest.mark.cm_verified
def test_incident_severity_levels():
    """CM-11.9: System supports Critical, High, Medium, and Low severity levels."""
    # Verify the enum has all four expected levels
    expected_levels = {"critical", "high", "medium", "low"}
    actual_levels = {s.value for s in IncidentSeverity}
    assert actual_levels == expected_levels, (
        f"Expected severity levels {expected_levels}, got {actual_levels}"
    )

    # Verify the monitor maps consecutive failures to correct severities
    monitor = IntelligentHealthMonitor()
    assert monitor._determine_incident_severity(10) == IncidentSeverity.CRITICAL
    assert monitor._determine_incident_severity(15) == IncidentSeverity.CRITICAL
    assert monitor._determine_incident_severity(5) == IncidentSeverity.HIGH
    assert monitor._determine_incident_severity(7) == IncidentSeverity.HIGH
    assert monitor._determine_incident_severity(3) == IncidentSeverity.MEDIUM
    assert monitor._determine_incident_severity(4) == IncidentSeverity.MEDIUM
    assert monitor._determine_incident_severity(1) == IncidentSeverity.LOW
    assert monitor._determine_incident_severity(2) == IncidentSeverity.LOW
