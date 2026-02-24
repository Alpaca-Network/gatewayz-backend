"""
Tests for Partner Trials API Routes

Tests the partner trial endpoints used for Redbeard and other partner integrations.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    """Create test client"""
    return TestClient(app)


@pytest.fixture
def mock_api_key():
    """Mock API key for authenticated endpoints"""
    return "gw_live_test_key_12345"


class TestPartnerTrialsPublicEndpoints:
    """Tests for public (unauthenticated) endpoints"""

    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_config")
    def test_get_partner_config_success(self, mock_get_config, client):
        """Test getting partner config for valid partner"""
        mock_get_config.return_value = {
            "partner_code": "REDBEARD",
            "partner_name": "Red Beard Ventures",
            "trial_duration_days": 14,
            "trial_tier": "pro",
            "trial_credits_usd": 20.00,
            "daily_usage_limit_usd": 5.00,
        }

        response = client.get("/partner-trials/config/REDBEARD")

        assert response.status_code == 200
        data = response.json()
        assert data["partner_code"] == "REDBEARD"
        assert data["partner_name"] == "Red Beard Ventures"
        assert data["trial_duration_days"] == 14
        assert data["trial_tier"] == "pro"
        assert data["trial_credits_usd"] == 20.00

    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_config")
    def test_get_partner_config_not_found(self, mock_get_config, client):
        """Test getting config for non-existent partner"""
        mock_get_config.return_value = None

        response = client.get("/partner-trials/config/NONEXISTENT")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("src.routes.partner_trials.PartnerTrialService.is_partner_code")
    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_config")
    def test_check_partner_code_valid(self, mock_get_config, mock_is_partner, client):
        """Test checking a valid partner code"""
        mock_is_partner.return_value = True
        mock_get_config.return_value = {
            "partner_name": "Red Beard Ventures",
            "trial_duration_days": 14,
            "trial_tier": "pro",
        }

        response = client.get("/partner-trials/check/REDBEARD")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "REDBEARD"
        assert data["is_partner_code"] is True
        assert data["partner_name"] == "Red Beard Ventures"
        assert data["trial_duration_days"] == 14
        assert data["trial_tier"] == "pro"

    @patch("src.routes.partner_trials.PartnerTrialService.is_partner_code")
    def test_check_partner_code_invalid(self, mock_is_partner, client):
        """Test checking a user referral code (not a partner code)"""
        mock_is_partner.return_value = False

        response = client.get("/partner-trials/check/ABC12345")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "ABC12345"
        assert data["is_partner_code"] is False
        assert data["partner_name"] is None


class TestPartnerTrialsAuthenticatedEndpoints:
    """Tests for authenticated endpoints"""

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.get_api_key")
    @patch("src.routes.partner_trials.PartnerTrialService.start_partner_trial")
    def test_start_partner_trial_success(
        self, mock_start_trial, mock_get_api_key, mock_get_user_id, client, mock_api_key
    ):
        """Test starting a partner trial"""
        mock_get_api_key.return_value = mock_api_key
        mock_get_user_id.return_value = 123
        mock_start_trial.return_value = {
            "success": True,
            "partner_code": "REDBEARD",
            "partner_name": "Red Beard Ventures",
            "trial_tier": "pro",
            "trial_credits_usd": 20.00,
            "trial_duration_days": 14,
            "trial_expires_at": "2024-01-15T00:00:00+00:00",
            "daily_usage_limit_usd": 5.00,
        }

        response = client.post(
            "/partner-trials/start",
            json={"partner_code": "REDBEARD", "signup_source": "landing_page"},
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["partner_code"] == "REDBEARD"
        assert data["trial_tier"] == "pro"
        assert data["trial_credits_usd"] == 20.00

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.get_api_key")
    @patch("src.routes.partner_trials.PartnerTrialService.start_partner_trial")
    def test_start_partner_trial_invalid_partner(
        self, mock_start_trial, mock_get_api_key, mock_get_user_id, client, mock_api_key
    ):
        """Test starting trial with invalid partner code"""
        mock_get_api_key.return_value = mock_api_key
        mock_get_user_id.return_value = 123
        mock_start_trial.side_effect = ValueError("Partner 'INVALID' not found or inactive")

        response = client.post(
            "/partner-trials/start",
            json={"partner_code": "INVALID"},
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_trial_status")
    def test_get_partner_trial_status_active(
        self, mock_get_status, mock_get_user_id, client, mock_api_key
    ):
        """Test getting active partner trial status"""
        mock_get_user_id.return_value = 123
        mock_get_status.return_value = {
            "has_partner_trial": True,
            "partner_code": "REDBEARD",
            "trial_status": "active",
            "is_expired": False,
            "days_remaining": 10,
            "trial_started_at": "2024-01-01T00:00:00+00:00",
            "trial_expires_at": "2024-01-15T00:00:00+00:00",
            "credits_used": 5.50,
            "tokens_used": 100000,
            "requests_made": 50,
            "converted": False,
        }

        response = client.get(
            "/partner-trials/status",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_partner_trial"] is True
        assert data["partner_code"] == "REDBEARD"
        assert data["trial_status"] == "active"
        assert data["days_remaining"] == 10

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_trial_status")
    def test_get_partner_trial_status_no_trial(
        self, mock_get_status, mock_get_user_id, client, mock_api_key
    ):
        """Test getting status when no partner trial exists"""
        mock_get_user_id.return_value = 123
        mock_get_status.return_value = {"has_partner_trial": False}

        response = client.get(
            "/partner-trials/status",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_partner_trial"] is False

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.PartnerTrialService.get_user_daily_limit")
    def test_get_daily_limit_partner_user(
        self, mock_get_limit, mock_get_user_id, client, mock_api_key
    ):
        """Test getting daily limit for partner trial user"""
        mock_get_user_id.return_value = 123
        mock_get_limit.return_value = 5.00

        response = client.get(
            "/partner-trials/daily-limit",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == 123
        assert data["daily_limit_usd"] == 5.00
        assert data["unlimited"] is False

    @patch("src.routes.partner_trials.get_current_user_id")
    @patch("src.routes.partner_trials.PartnerTrialService.get_user_daily_limit")
    def test_get_daily_limit_unlimited(
        self, mock_get_limit, mock_get_user_id, client, mock_api_key
    ):
        """Test getting daily limit for paid subscriber (unlimited)"""
        mock_get_user_id.return_value = 123
        mock_get_limit.return_value = float("inf")

        response = client.get(
            "/partner-trials/daily-limit",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["daily_limit_usd"] is None
        assert data["unlimited"] is True


class TestPartnerTrialsAdminEndpoints:
    """Tests for admin endpoints"""

    @patch("src.routes.partner_trials.get_api_key")
    @patch("src.routes.partner_trials.PartnerTrialService.get_partner_analytics")
    def test_get_partner_analytics(
        self, mock_get_analytics, mock_get_api_key, client, mock_api_key
    ):
        """Test getting partner analytics"""
        mock_get_api_key.return_value = mock_api_key
        mock_get_analytics.return_value = {
            "partner_code": "REDBEARD",
            "total_trials": 100,
            "active_trials": 25,
            "converted_trials": 50,
            "expired_trials": 25,
            "conversion_rate_percent": 50.0,
            "total_revenue_usd": 999.50,
            "avg_revenue_per_conversion": 19.99,
        }

        response = client.get(
            "/partner-trials/analytics/REDBEARD",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["partner_code"] == "REDBEARD"
        assert data["total_trials"] == 100
        assert data["conversion_rate_percent"] == 50.0
        assert data["total_revenue_usd"] == 999.50

    @patch("src.routes.partner_trials.get_api_key")
    @patch("src.routes.partner_trials.PartnerTrialService.expire_partner_trial")
    def test_force_expire_trial(self, mock_expire, mock_get_api_key, client, mock_api_key):
        """Test force expiring a trial"""
        mock_get_api_key.return_value = mock_api_key
        mock_expire.return_value = {
            "success": True,
            "expired_at": "2024-01-10T00:00:00+00:00",
        }

        response = client.post(
            "/partner-trials/expire/123",
            headers={"Authorization": f"Bearer {mock_api_key}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "expired_at" in data
