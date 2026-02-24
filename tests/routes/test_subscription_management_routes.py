#!/usr/bin/env python3
"""
Tests for Subscription Management Routes
Tests upgrade, downgrade, cancel, and get subscription endpoints
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_current_user():
    """Mock authenticated user"""
    return {
        "id": 123,
        "email": "test@example.com",
        "tier": "pro",
        "subscription_status": "active",
    }


@pytest.fixture
def mock_subscription_response():
    """Mock subscription management response"""
    return MagicMock(
        success=True,
        subscription_id="sub_test123",
        status="active",
        current_tier="max",
        message="Successfully upgraded",
        effective_date=None,
        proration_amount=None,
    )


@pytest.fixture
def mock_current_subscription_response():
    """Mock current subscription response"""
    return MagicMock(
        has_subscription=True,
        subscription_id="sub_test123",
        status="active",
        tier="pro",
        current_period_start=datetime(2024, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2024, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        canceled_at=None,
        product_id="prod_TKOqQPhVRxNp4Q",
        price_id="price_pro_8",
    )


@pytest.fixture
def client():
    """Create test client with mocked dependencies"""
    # Import here to avoid circular imports
    from src.main import create_app

    app = create_app()
    return TestClient(app)


class TestGetCurrentSubscriptionEndpoint:
    """Tests for GET /api/stripe/subscription endpoint"""

    def test_get_subscription_success(
        self, client, mock_current_user, mock_current_subscription_response
    ):
        """Test successful subscription retrieval"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.get_current_subscription",
                return_value=mock_current_subscription_response,
            ):
                response = client.get(
                    "/api/stripe/subscription",
                    headers={"Authorization": "Bearer test_token"},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["has_subscription"] is True
                assert data["subscription_id"] == "sub_test123"
                assert data["tier"] == "pro"
                assert data["status"] == "active"

    def test_get_subscription_no_subscription(self, client, mock_current_user):
        """Test getting subscription when user has none"""
        no_sub_response = MagicMock(
            has_subscription=False,
            subscription_id=None,
            status=None,
            tier="basic",
            current_period_start=None,
            current_period_end=None,
            cancel_at_period_end=False,
            canceled_at=None,
            product_id=None,
            price_id=None,
        )

        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.get_current_subscription",
                return_value=no_sub_response,
            ):
                response = client.get(
                    "/api/stripe/subscription",
                    headers={"Authorization": "Bearer test_token"},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["has_subscription"] is False
                assert data["tier"] == "basic"


class TestUpgradeSubscriptionEndpoint:
    """Tests for POST /api/stripe/subscription/upgrade endpoint"""

    def test_upgrade_success(self, client, mock_current_user, mock_subscription_response):
        """Test successful subscription upgrade"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.upgrade_subscription",
                return_value=mock_subscription_response,
            ):
                response = client.post(
                    "/api/stripe/subscription/upgrade",
                    headers={"Authorization": "Bearer test_token"},
                    json={
                        "new_price_id": "price_max_75",
                        "new_product_id": "prod_TKOraBpWMxMAIu",
                        "proration_behavior": "create_prorations",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["subscription_id"] == "sub_test123"
                assert data["current_tier"] == "max"

    def test_upgrade_no_subscription(self, client, mock_current_user):
        """Test upgrading when user has no subscription"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.upgrade_subscription",
                side_effect=ValueError("User does not have an active subscription"),
            ):
                response = client.post(
                    "/api/stripe/subscription/upgrade",
                    headers={"Authorization": "Bearer test_token"},
                    json={
                        "new_price_id": "price_max_75",
                        "new_product_id": "prod_TKOraBpWMxMAIu",
                    },
                )

                assert response.status_code == 400
                assert "does not have an active subscription" in response.json()["detail"]

    def test_upgrade_invalid_proration_behavior(self, client, mock_current_user):
        """Test upgrade with invalid proration behavior"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            response = client.post(
                "/api/stripe/subscription/upgrade",
                headers={"Authorization": "Bearer test_token"},
                json={
                    "new_price_id": "price_max_75",
                    "new_product_id": "prod_TKOraBpWMxMAIu",
                    "proration_behavior": "invalid_behavior",
                },
            )

            assert response.status_code == 422  # Validation error


class TestDowngradeSubscriptionEndpoint:
    """Tests for POST /api/stripe/subscription/downgrade endpoint"""

    def test_downgrade_success(self, client, mock_current_user):
        """Test successful subscription downgrade"""
        downgrade_response = MagicMock(
            success=True,
            subscription_id="sub_test123",
            status="active",
            current_tier="pro",
            message="Successfully downgraded to pro tier. Credit applied for unused time.",
            effective_date=None,
            proration_amount=None,
        )

        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.downgrade_subscription",
                return_value=downgrade_response,
            ):
                response = client.post(
                    "/api/stripe/subscription/downgrade",
                    headers={"Authorization": "Bearer test_token"},
                    json={
                        "new_price_id": "price_pro_8",
                        "new_product_id": "prod_TKOqQPhVRxNp4Q",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["current_tier"] == "pro"
                assert "downgraded" in data["message"].lower()

    def test_downgrade_no_subscription(self, client, mock_current_user):
        """Test downgrading when user has no subscription"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.downgrade_subscription",
                side_effect=ValueError("User does not have an active subscription"),
            ):
                response = client.post(
                    "/api/stripe/subscription/downgrade",
                    headers={"Authorization": "Bearer test_token"},
                    json={
                        "new_price_id": "price_pro_8",
                        "new_product_id": "prod_TKOqQPhVRxNp4Q",
                    },
                )

                assert response.status_code == 400


class TestCancelSubscriptionEndpoint:
    """Tests for POST /api/stripe/subscription/cancel endpoint"""

    def test_cancel_at_period_end_success(self, client, mock_current_user):
        """Test canceling subscription at end of billing period"""
        cancel_response = MagicMock(
            success=True,
            subscription_id="sub_test123",
            status="cancel_scheduled",
            current_tier="pro",
            message="Subscription will be canceled at the end of the billing period.",
            effective_date=datetime(2024, 2, 1, tzinfo=UTC),
        )

        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.cancel_subscription",
                return_value=cancel_response,
            ):
                response = client.post(
                    "/api/stripe/subscription/cancel",
                    headers={"Authorization": "Bearer test_token"},
                    json={
                        "cancel_at_period_end": True,
                        "reason": "Switching to another service",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["status"] == "cancel_scheduled"
                assert data["current_tier"] == "pro"  # Still pro until period ends
                assert data["effective_date"] is not None

    def test_cancel_immediately_success(self, client, mock_current_user):
        """Test canceling subscription immediately"""
        cancel_response = MagicMock(
            success=True,
            subscription_id="sub_test123",
            status="canceled",
            current_tier="basic",
            message="Subscription canceled immediately.",
            effective_date=datetime.now(UTC),
        )

        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.cancel_subscription",
                return_value=cancel_response,
            ):
                response = client.post(
                    "/api/stripe/subscription/cancel",
                    headers={"Authorization": "Bearer test_token"},
                    json={"cancel_at_period_end": False},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["status"] == "canceled"
                assert data["current_tier"] == "basic"

    def test_cancel_default_behavior(self, client, mock_current_user):
        """Test cancel with default behavior (cancel at period end)"""
        cancel_response = MagicMock(
            success=True,
            subscription_id="sub_test123",
            status="cancel_scheduled",
            current_tier="pro",
            message="Subscription will be canceled at the end of the billing period.",
            effective_date=datetime(2024, 2, 1, tzinfo=UTC),
        )

        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.cancel_subscription",
                return_value=cancel_response,
            ):
                # Empty body should use defaults
                response = client.post(
                    "/api/stripe/subscription/cancel",
                    headers={"Authorization": "Bearer test_token"},
                    json={},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "cancel_scheduled"

    def test_cancel_no_subscription(self, client, mock_current_user):
        """Test canceling when user has no subscription"""
        with patch("src.routes.payments.get_current_user", return_value=mock_current_user):
            with patch(
                "src.routes.payments.stripe_service.cancel_subscription",
                side_effect=ValueError("User does not have an active subscription"),
            ):
                response = client.post(
                    "/api/stripe/subscription/cancel",
                    headers={"Authorization": "Bearer test_token"},
                    json={"cancel_at_period_end": True},
                )

                assert response.status_code == 400
                assert "does not have an active subscription" in response.json()["detail"]


class TestSubscriptionEndpointsAuthentication:
    """Tests for authentication on subscription endpoints"""

    def test_get_subscription_no_auth(self, client):
        """Test getting subscription without authentication"""
        response = client.get("/api/stripe/subscription")
        # Should return 401 or 403 depending on auth implementation
        assert response.status_code in [401, 403, 422]

    def test_upgrade_no_auth(self, client):
        """Test upgrading without authentication"""
        response = client.post(
            "/api/stripe/subscription/upgrade",
            json={
                "new_price_id": "price_max_75",
                "new_product_id": "prod_TKOraBpWMxMAIu",
            },
        )
        assert response.status_code in [401, 403, 422]

    def test_downgrade_no_auth(self, client):
        """Test downgrading without authentication"""
        response = client.post(
            "/api/stripe/subscription/downgrade",
            json={
                "new_price_id": "price_pro_8",
                "new_product_id": "prod_TKOqQPhVRxNp4Q",
            },
        )
        assert response.status_code in [401, 403, 422]

    def test_cancel_no_auth(self, client):
        """Test canceling without authentication"""
        response = client.post(
            "/api/stripe/subscription/cancel",
            json={"cancel_at_period_end": True},
        )
        assert response.status_code in [401, 403, 422]
