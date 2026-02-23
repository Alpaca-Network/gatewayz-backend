"""
Tests for provider credit balance API endpoints.
"""

import pytest
from datetime import datetime, timezone, UTC
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_admin_user():
    """Mock admin user for testing"""
    return {
        "id": 1,
        "email": "admin@test.com",
        "role": "admin",
        "tier": "max"
    }


@pytest.fixture
def mock_super_admin_user():
    """Mock super admin user for testing"""
    return {
        "id": 1,
        "email": "superadmin@test.com",
        "role": "super_admin",
        "tier": "max"
    }


@pytest.fixture
def mock_regular_user():
    """Mock regular user for testing"""
    return {
        "id": 2,
        "email": "user@test.com",
        "role": "user",
        "tier": "pro"
    }


class TestGetProviderCreditBalances:
    """Test GET /api/provider-credits/balance endpoint"""

    @pytest.mark.asyncio
    async def test_get_balances_as_admin(self, client, mock_admin_user):
        """Test retrieving all provider credit balances as admin"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.check_all_provider_credits") as mock_check:

            # Mock credit check response
            mock_check.return_value = {
                "openrouter": {
                    "provider": "openrouter",
                    "balance": 123.45,
                    "status": "healthy",
                    "checked_at": datetime.now(UTC),
                    "cached": False
                }
            }

            response = client.get(
                "/api/provider-credits/balance",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert "providers" in data
            assert "openrouter" in data["providers"]
            assert data["providers"]["openrouter"]["balance"] == 123.45
            assert data["providers"]["openrouter"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_get_balances_with_warning_status(self, client, mock_admin_user):
        """Test retrieving balance with warning status"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.check_all_provider_credits") as mock_check:

            # Mock low balance
            mock_check.return_value = {
                "openrouter": {
                    "provider": "openrouter",
                    "balance": 15.0,
                    "status": "warning",
                    "checked_at": datetime.now(UTC),
                    "cached": False
                }
            }

            response = client.get(
                "/api/provider-credits/balance",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["providers"]["openrouter"]["status"] == "warning"
            assert data["providers"]["openrouter"]["balance"] == 15.0

    @pytest.mark.asyncio
    async def test_get_balances_unauthorized(self, client, mock_regular_user):
        """Test that non-admin users cannot access credit balances"""
        from fastapi import HTTPException

        async def mock_require_admin_raises():
            raise HTTPException(status_code=403, detail="Administrator privileges required")

        with patch("src.routes.provider_credits.require_admin", side_effect=mock_require_admin_raises):
            response = client.get(
                "/api/provider-credits/balance",
                headers={"Authorization": "Bearer user-key"}
            )

            # Should fail authentication
            assert response.status_code in (401, 403, 500)


class TestGetSpecificProviderBalance:
    """Test GET /api/provider-credits/balance/{provider} endpoint"""

    @pytest.mark.asyncio
    async def test_get_openrouter_balance(self, client, mock_admin_user):
        """Test retrieving OpenRouter specific balance"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.check_openrouter_credits") as mock_check:

            mock_check.return_value = {
                "provider": "openrouter",
                "balance": 50.0,
                "status": "info",
                "checked_at": datetime.now(UTC),
                "cached": True
            }

            response = client.get(
                "/api/provider-credits/balance/openrouter",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["provider"] == "openrouter"
            assert data["balance"] == 50.0
            assert data["status"] == "info"
            assert data["cached"] is True

    @pytest.mark.asyncio
    async def test_get_unsupported_provider(self, client, mock_admin_user):
        """Test retrieving balance for unsupported provider"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user):

            response = client.get(
                "/api/provider-credits/balance/unsupported-provider",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 400
            assert "not supported" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_balance_with_error(self, client, mock_admin_user):
        """Test handling errors during balance retrieval"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.check_openrouter_credits") as mock_check:

            mock_check.return_value = {
                "provider": "openrouter",
                "balance": None,
                "status": "unknown",
                "checked_at": datetime.now(UTC),
                "cached": False,
                "error": "API key not configured"
            }

            response = client.get(
                "/api/provider-credits/balance/openrouter",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "unknown"
            assert data["error"] == "API key not configured"


class TestClearProviderCreditCache:
    """Test POST /api/provider-credits/balance/clear-cache endpoint"""

    @pytest.mark.asyncio
    async def test_clear_all_cache(self, client, mock_admin_user):
        """Test clearing all provider credit caches"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.clear_credit_cache") as mock_clear:

            response = client.post(
                "/api/provider-credits/balance/clear-cache",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "all providers" in data["message"]

            # Should have called clear_credit_cache with None
            mock_clear.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_clear_specific_provider_cache(self, client, mock_admin_user):
        """Test clearing cache for specific provider"""
        with patch("src.routes.provider_credits.require_admin", return_value=mock_admin_user), \
             patch("src.routes.provider_credits.clear_credit_cache") as mock_clear:

            response = client.post(
                "/api/provider-credits/balance/clear-cache?provider=openrouter",
                headers={"Authorization": "Bearer admin-key"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "openrouter" in data["message"]

            # Should have called clear_credit_cache with provider name
            mock_clear.assert_called_once_with("openrouter")

    @pytest.mark.asyncio
    async def test_clear_cache_unauthorized(self, client, mock_regular_user):
        """Test that non-admin users cannot clear cache"""
        from fastapi import HTTPException

        async def mock_require_admin_raises():
            raise HTTPException(status_code=403, detail="Administrator privileges required")

        with patch("src.routes.provider_credits.require_admin", side_effect=mock_require_admin_raises):
            response = client.post(
                "/api/provider-credits/balance/clear-cache",
                headers={"Authorization": "Bearer user-key"}
            )

            # Should fail authentication
            assert response.status_code in (401, 403, 500)
