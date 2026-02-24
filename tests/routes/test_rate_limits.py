"""
Comprehensive tests for Rate Limits routes

Covers:
- User rate limit configuration management
- Admin rate limit management endpoints
- Rate limit config, reset, delete, update
- User rate limits listing

Uses FastAPI dependency override mechanism for testing.
"""

import os
import sys
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# Set test environment
os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ["ADMIN_API_KEY"] = "test-admin-key-12345"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-bytes-long!"
os.environ["API_GATEWAY_SALT"] = "test-salt-for-hashing-keys-minimum-16-chars"

from src.main import app
from src.routes.rate_limits import DEFAULT_RATE_LIMIT_CONFIG, router
from src.security.deps import require_admin

# Skip tests on Python 3.10 due to compatibility issues
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="Rate limits route tests have Python 3.10 compatibility issues",
)


@pytest.fixture
def client():
    """FastAPI test client"""
    app.dependency_overrides = {}
    yield TestClient(app)
    app.dependency_overrides = {}


@pytest.fixture
def auth_headers():
    """Authentication headers for admin"""
    return {"Authorization": "Bearer gw_admin_key_123", "Content-Type": "application/json"}


def mock_require_admin():
    """Mock admin authentication dependency"""
    return {
        "id": 1,
        "username": "admin",
        "is_admin": True,
        "role": "admin",
    }


def mock_get_api_key():
    """Mock API key dependency"""
    return "gw_test_key_123"


class TestRateLimitsRoutes:
    """Test Rate Limits route handlers"""

    def test_router_exists(self):
        """Test that router is defined"""
        assert router is not None
        assert hasattr(router, "routes")

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.routes.rate_limits

        assert src.routes.rate_limits is not None

    def test_default_config_defined(self):
        """Test that default rate limit config is defined"""
        assert DEFAULT_RATE_LIMIT_CONFIG is not None
        assert "requests_per_minute" in DEFAULT_RATE_LIMIT_CONFIG
        assert "requests_per_hour" in DEFAULT_RATE_LIMIT_CONFIG
        assert "requests_per_day" in DEFAULT_RATE_LIMIT_CONFIG
        assert "tokens_per_minute" in DEFAULT_RATE_LIMIT_CONFIG


class TestAdminRateLimitsConfig:
    """Test admin rate limit config endpoints"""

    @patch("src.routes.rate_limits.get_rate_limit_config")
    def test_get_config_default(self, mock_get_config, client, auth_headers):
        """Get default rate limit configuration"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.get("/admin/rate-limits/config", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "config" in data
        assert data["config"]["requests_per_minute"] == 60

    @patch("src.routes.rate_limits.get_rate_limit_config")
    def test_get_config_for_specific_key(self, mock_get_config, client, auth_headers):
        """Get rate limit config for specific API key"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_get_config.return_value = {
            "requests_per_minute": 100,
            "requests_per_hour": 2000,
        }

        response = client.get("/admin/rate-limits/config?api_key=gw_test_key", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "gw_test" in data["api_key"]

    @patch("src.routes.rate_limits.update_rate_limit_config")
    def test_set_config_success(self, mock_update_config, client, auth_headers):
        """Successfully set rate limit configuration"""
        app.dependency_overrides[require_admin] = mock_require_admin
        mock_update_config.return_value = True

        response = client.post(
            "/admin/rate-limits/config",
            json={
                "api_key": "gw_test_key_123",
                "config": {
                    "requests_per_minute": 100,
                    "requests_per_hour": 2000,
                    "requests_per_day": 20000,
                    "tokens_per_minute": 20000,
                    "tokens_per_hour": 200000,
                    "tokens_per_day": 2000000,
                    "burst_limit": 200,
                    "concurrency_limit": 100,
                },
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["config"]["requests_per_minute"] == 100


class TestAdminRateLimitsReset:
    """Test admin rate limit reset endpoint"""

    @patch("src.routes.rate_limits.update_rate_limit_config")
    def test_reset_config_success(self, mock_update_config, client, auth_headers):
        """Successfully reset rate limit to defaults"""
        app.dependency_overrides[require_admin] = mock_require_admin
        mock_update_config.return_value = True

        response = client.post(
            "/admin/rate-limits/config/reset?api_key=gw_test_key_123", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["config"] == DEFAULT_RATE_LIMIT_CONFIG

    def test_reset_config_missing_key(self, client, auth_headers):
        """Reset config requires api_key parameter"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.post("/admin/rate-limits/config/reset", headers=auth_headers)

        assert response.status_code == 422  # Missing required parameter


class TestAdminRateLimitsUpdate:
    """Test admin rate limit update endpoint"""

    @patch("src.routes.rate_limits.set_user_rate_limits")
    def test_update_rate_limits_success(self, mock_set_limits, client, auth_headers):
        """Successfully update rate limits"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.put(
            "/admin/rate-limits/update",
            json={
                "api_key": "gw_test_key_123",
                "config": {
                    "requests_per_minute": 120,
                    "requests_per_hour": 3000,
                    "requests_per_day": 30000,
                    "tokens_per_minute": 30000,
                    "tokens_per_hour": 300000,
                    "tokens_per_day": 3000000,
                    "burst_limit": 150,
                    "concurrency_limit": 75,
                },
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_set_limits.assert_called_once()


class TestAdminRateLimitsDelete:
    """Test admin rate limit delete endpoint"""

    @patch("src.routes.rate_limits.get_supabase_client")
    def test_delete_rate_limits_success(self, mock_supabase, client, auth_headers):
        """Successfully delete rate limits"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock delete operation
        mock_client.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = [
            {"id": 1}
        ]
        # Mock update operation
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 1}
        ]

        response = client.delete(
            "/admin/rate-limits/delete?api_key=gw_test_key_123", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "default_config" in data


class TestAdminRateLimitsUsers:
    """Test admin rate limits users listing endpoint"""

    @patch("src.routes.rate_limits.get_supabase_client")
    def test_get_users_rate_limits(self, mock_supabase, client, auth_headers):
        """Get rate limits for all users"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.range.return_value.execute.return_value.data = [
            {
                "id": 1,
                "api_key": "gw_key_123456789",
                "key_name": "Test Key",
                "user_id": 2,
                "rate_limit_config": {"requests_per_minute": 100},
                "environment_tag": "production",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]

        response = client.get("/admin/rate-limits/users", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "users" in data
        assert len(data["users"]) == 1
        assert data["users"][0]["has_custom_config"] is True

    @patch("src.routes.rate_limits.get_supabase_client")
    def test_get_users_rate_limits_with_filters(self, mock_supabase, client, auth_headers):
        """Get rate limits with filters"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.eq.return_value.range.return_value.execute.return_value.data = (
            []
        )

        response = client.get(
            "/admin/rate-limits/users?user_id=2&has_custom_config=true", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["user_id"] == 2
        assert data["filters"]["has_custom_config"] is True


class TestAdminRateLimitsAlerts:
    """Test admin rate limit alerts endpoint"""

    @patch("src.routes.rate_limits.get_rate_limit_alerts")
    def test_get_alerts_success(self, mock_get_alerts, client, auth_headers):
        """Get rate limit alerts"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_get_alerts.return_value = [
            {
                "id": 1,
                "api_key": "gw_test_key",
                "alert_type": "rate_exceeded",
                "details": {"requests": 1000},
                "created_at": datetime.now(UTC).isoformat(),
                "resolved": False,
            }
        ]

        response = client.get("/admin/rate-limits/alerts", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "alerts" in data
        assert data["total_alerts"] == 1


class TestAdminRateLimitsSystem:
    """Test admin system rate limits endpoint"""

    @patch("src.routes.rate_limits.get_system_rate_limit_stats")
    def test_get_system_stats(self, mock_get_stats, client, auth_headers):
        """Get system-wide rate limit stats"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_get_stats.return_value = {
            "timestamp": datetime.now(UTC).isoformat(),
            "minute": {"requests": 100, "tokens": 10000},
            "hour": {"requests": 1000, "tokens": 100000},
            "day": {"requests": 10000, "tokens": 1000000},
        }

        response = client.get("/admin/rate-limits/system", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "system_stats" in data


class TestRateLimitsAuthorization:
    """Test authorization for rate limits endpoints"""

    def test_admin_endpoints_require_admin(self, client):
        """Admin rate limit endpoints require admin authentication"""
        app.dependency_overrides = {}

        endpoints = [
            ("/admin/rate-limits/config", "GET"),
            ("/admin/rate-limits/system", "GET"),
            ("/admin/rate-limits/alerts", "GET"),
            ("/admin/rate-limits/users", "GET"),
        ]

        for endpoint, method in endpoints:
            if method == "GET":
                response = client.get(
                    endpoint, headers={"Authorization": "Bearer regular_user_key"}
                )
            else:
                response = client.post(
                    endpoint, json={}, headers={"Authorization": "Bearer regular_user_key"}
                )

            # Should fail with authentication error
            assert response.status_code in [
                401,
                403,
                422,
            ], f"Endpoint {endpoint} should require admin auth"
