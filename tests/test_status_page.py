"""
Tests for the Public Status Page API

Tests the status page endpoints that provide public health information.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


@pytest.fixture
def client():
    """Create test client"""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_supabase_data():
    """Mock Supabase data for status page"""
    return {
        "providers": [
            {
                "provider": "openai",
                "gateway": "openrouter",
                "status_indicator": "operational",
                "total_models": 8,
                "healthy_models": 8,
                "offline_models": 0,
                "avg_uptime_24h": 99.95,
                "avg_uptime_7d": 99.90,
                "avg_response_time_ms": 450.0,
                "last_checked_at": datetime.now(timezone.utc).isoformat(),
                "total_usage_24h": 1000,
            }
        ],
        "models": [
            {
                "model": "gpt-4",
                "provider": "openai",
                "gateway": "openrouter",
                "monitoring_tier": "critical",
                "last_status": "success",
                "uptime_percentage_24h": 99.95,
                "uptime_percentage_7d": 99.90,
                "uptime_percentage_30d": 99.85,
                "average_response_time_ms": 450.0,
                "last_called_at": datetime.now(timezone.utc).isoformat(),
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "last_failure_at": None,
                "circuit_breaker_state": "closed",
                "consecutive_failures": 0,
                "usage_count_24h": 500,
                "is_enabled": True,
                "active_incidents_count": 0,
                "status_indicator": "operational",
            }
        ],
        "incidents": [
            {
                "id": 1,
                "provider": "openai",
                "model": "gpt-4",
                "gateway": "openrouter",
                "incident_type": "timeout",
                "severity": "medium",
                "status": "resolved",
                "started_at": "2025-11-27T10:00:00Z",
                "resolved_at": "2025-11-27T10:30:00Z",
                "duration_seconds": 1800,
                "error_message": "Request timeout",
                "error_count": 5,
                "resolution_notes": "Resolved automatically",
            }
        ],
    }


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_overall_status(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/ endpoint"""
    # Mock provider health data
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["providers"]
    mock_supabase.table.return_value.select.return_value.execute.return_value = mock_response

    # Mock incidents count
    mock_incidents = MagicMock()
    mock_incidents.count = 0
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_incidents

    response = client.get("/v1/status/")

    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert "uptime_percentage" in data
    assert "total_models" in data
    assert "healthy_models" in data
    assert "total_providers" in data


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_providers_status(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/providers endpoint"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["providers"]
    mock_supabase.table.return_value.select.return_value.order.return_value.execute.return_value = mock_response

    response = client.get("/v1/status/providers")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    if len(data) > 0:
        provider = data[0]
        assert "name" in provider
        assert "gateway" in provider
        assert "status" in provider
        assert "uptime_24h" in provider
        assert "total_models" in provider


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_models_status(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/models endpoint"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["models"]

    # Chain mock for query builder
    mock_query = MagicMock()
    mock_query.range.return_value.order.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/models?limit=10")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_models_status_with_filters(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/models with filters"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["models"]

    # Chain mock for query builder with filters
    mock_query = MagicMock()
    mock_query.eq.return_value = mock_query
    mock_query.range.return_value.order.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/models?provider=openai&gateway=openrouter&tier=critical")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_model_status(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/models/{provider}/{model_id} endpoint"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["models"][0]

    # Chain mock for query builder
    mock_query = MagicMock()
    mock_query.eq.return_value = mock_query
    mock_query.maybe_single.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/models/openai/gpt-4")

    assert response.status_code == 200
    data = response.json()

    assert data["model_id"] == "gpt-4"
    assert data["provider"] == "openai"
    assert "status" in data
    assert "uptime_24h" in data


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_model_status_not_found(mock_supabase, client):
    """Test GET /v1/status/models/{provider}/{model_id} when model doesn't exist"""
    mock_response = MagicMock()
    mock_response.data = None

    mock_query = MagicMock()
    mock_query.eq.return_value = mock_query
    mock_query.maybe_single.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/models/unknown/unknown-model")

    assert response.status_code == 404


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_incidents(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/incidents endpoint"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["incidents"]

    # Chain mock for query builder
    mock_query = MagicMock()
    mock_query.range.return_value.order.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/incidents")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    if len(data) > 0:
        incident = data[0]
        assert "id" in incident
        assert "provider" in incident
        assert "model" in incident
        assert "severity" in incident
        assert "status" in incident


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_incidents_with_filters(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/incidents with filters"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["incidents"]

    # Chain mock for query builder with filters
    mock_query = MagicMock()
    mock_query.eq.return_value = mock_query
    mock_query.range.return_value.order.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/incidents?status=active&severity=critical")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_search_models(mock_supabase, client, mock_supabase_data):
    """Test GET /v1/status/search endpoint"""
    mock_response = MagicMock()
    mock_response.data = mock_supabase_data["models"]

    # Chain mock for query builder
    mock_query = MagicMock()
    mock_query.or_.return_value.limit.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/search?q=gpt")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_models_requires_query(client):
    """Test GET /v1/status/search requires query parameter"""
    response = client.get("/v1/status/search")

    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_stats(mock_supabase, client):
    """Test GET /v1/status/stats endpoint"""
    # Mock tier counts
    mock_tier_response = MagicMock()
    mock_tier_response.data = [
        {"monitoring_tier": "critical"},
        {"monitoring_tier": "popular"},
        {"monitoring_tier": "standard"},
    ]
    mock_tier_response.count = 3

    # Mock incidents
    mock_incidents_response = MagicMock()
    mock_incidents_response.data = [{"status": "active"}, {"status": "resolved"}]
    mock_incidents_response.count = 2

    # Mock checks
    mock_checks_response = MagicMock()
    mock_checks_response.data = [
        {"status": "success"},
        {"status": "success"},
        {"status": "error"},
    ]
    mock_checks_response.count = 3

    # Set up mock to return different responses based on table name
    def table_side_effect(table_name):
        mock_table = MagicMock()
        if table_name == "model_health_tracking":
            mock_table.select.return_value.eq.return_value.execute.return_value = mock_tier_response
        elif table_name == "model_health_incidents":
            mock_table.select.return_value.execute.return_value = mock_incidents_response
        elif table_name == "model_health_history":
            mock_table.select.return_value.gte.return_value.execute.return_value = mock_checks_response
        return mock_table

    mock_supabase.table.side_effect = table_side_effect

    response = client.get("/v1/status/stats")

    assert response.status_code == 200
    data = response.json()

    assert "monitoring" in data
    assert "incidents" in data
    assert "checks_24h" in data


def test_format_duration_seconds():
    """Test duration formatting helper"""
    from src.routes.status_page import _format_duration

    assert _format_duration(30) == "30s"
    assert _format_duration(90) == "1m"
    assert _format_duration(3600) == "1h 0m"
    assert _format_duration(3660) == "1h 1m"
    assert _format_duration(86400) == "1d 0h"
    assert _format_duration(90000) == "1d 1h"


@pytest.mark.asyncio
@patch("src.routes.status_page.supabase")
async def test_get_uptime_history(mock_supabase, client):
    """Test GET /v1/status/uptime/{provider}/{model_id} endpoint"""
    mock_response = MagicMock()
    mock_response.data = [
        {
            "period_start": "2025-11-27T10:00:00Z",
            "uptime_percentage": 99.5,
            "avg_response_time_ms": 450.0,
            "total_checks": 100,
            "successful_checks": 99,
            "failed_checks": 1,
        }
    ]

    # Chain mock for query builder
    mock_query = MagicMock()
    mock_query.eq.return_value = mock_query
    mock_query.gte.return_value.order.return_value.execute.return_value = mock_response

    mock_supabase.table.return_value.select.return_value = mock_query

    response = client.get("/v1/status/uptime/openai/gpt-4?period=24h")

    assert response.status_code == 200
    data = response.json()

    assert "provider" in data
    assert "model" in data
    assert "period" in data
    assert "data_points" in data
    assert isinstance(data["data_points"], list)


@pytest.mark.asyncio
async def test_get_uptime_history_invalid_period(client):
    """Test GET /v1/status/uptime with invalid period"""
    with patch("src.routes.status_page.supabase"):
        response = client.get("/v1/status/uptime/openai/gpt-4?period=invalid")

        assert response.status_code == 400
