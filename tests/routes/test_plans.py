"""
Comprehensive tests for Plans routes
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes.plans import router


class TestPlansRoutes:
    """Test Plans route handlers"""

    def test_router_exists(self):
        """Test that router is defined"""
        assert router is not None
        assert hasattr(router, "routes")

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.routes.plans

        assert src.routes.plans is not None


class TestSubscriptionPlansDisabled:
    """Subscriptions are discontinued; /subscription/plans must return 410."""

    @pytest.fixture(scope="function")
    def client(self):
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_subscription_plans_returns_410(self, client):
        resp = client.get("/subscription/plans")
        assert resp.status_code == 410
        assert (
            resp.json()["detail"]
            == "Subscriptions have been discontinued. Please use credit top-ups instead."
        )
