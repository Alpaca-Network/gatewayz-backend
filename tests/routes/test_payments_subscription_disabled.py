"""
Tests confirming subscription purchase/management endpoints are disabled (HTTP 410).

Per North Star, Gatewayz is a prepaid-credits-only platform. Subscription
create/upgrade/downgrade/cancel/get-current endpoints must return 410 Gone
instead of performing any Stripe subscription operation. Credit top-up
(/api/stripe/checkout-session) is NOT covered here and must remain unaffected.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes.payments import get_current_user, router

EXPECTED_DETAIL = "Subscriptions have been discontinued. Please use credit top-ups instead."


@pytest.fixture(scope="function")
def client():
    app = FastAPI()
    app.include_router(router)

    async def mock_get_current_user():
        return {"id": 1, "email": "test@example.com"}

    app.dependency_overrides[get_current_user] = mock_get_current_user
    return TestClient(app)


class TestSubscriptionEndpointsDisabled:
    def test_subscription_checkout_returns_410(self, client):
        resp = client.post(
            "/api/stripe/subscription-checkout",
            json={
                "price_id": "price_123",
                "product_id": "prod_123",
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
            },
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == EXPECTED_DETAIL

    def test_get_current_subscription_returns_410(self, client):
        resp = client.get("/api/stripe/subscription")
        assert resp.status_code == 410
        assert resp.json()["detail"] == EXPECTED_DETAIL

    def test_upgrade_subscription_returns_410(self, client):
        resp = client.post(
            "/api/stripe/subscription/upgrade",
            json={"new_price_id": "price_456", "new_product_id": "prod_456"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == EXPECTED_DETAIL

    def test_downgrade_subscription_returns_410(self, client):
        resp = client.post(
            "/api/stripe/subscription/downgrade",
            json={"new_price_id": "price_789", "new_product_id": "prod_789"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == EXPECTED_DETAIL

    def test_cancel_subscription_returns_410(self, client):
        resp = client.post("/api/stripe/subscription/cancel", json={})
        assert resp.status_code == 410
        assert resp.json()["detail"] == EXPECTED_DETAIL
