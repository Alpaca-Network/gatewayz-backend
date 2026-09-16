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


class TestPlansNullColumns:
    """Regression: NULL columns on the `plans` table must not 500 GET /plans.

    plan_type was added as a bare nullable TEXT column, so 6 of 7 production
    rows held SQL NULL while PlanResponse declared `plan_type: str`. A pydantic
    default does not rescue an explicitly-passed None, so serialization raised
    ValidationError and every caller of this PUBLIC endpoint got a 500.
    """

    @pytest.fixture(scope="function")
    def client(self):
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    @staticmethod
    def _row(**overrides):
        row = {
            "id": 1,
            "name": "Free",
            "description": "Free tier",
            "plan_type": None,
            "daily_request_limit": 100,
            "monthly_request_limit": 1000,
            "daily_token_limit": 10000,
            "monthly_token_limit": 100000,
            "price_per_month": 0,
            "features": ["basic_access"],
            "is_active": True,
        }
        row.update(overrides)
        return row

    def test_get_plans_with_null_plan_type_returns_200(self, client):
        with patch("src.routes.plans.get_all_plans", return_value=[self._row()]):
            resp = client.get("/plans")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["plan_type"] == "free"

    def test_get_plans_with_all_nullable_columns_null_returns_200(self, client):
        """Every column that Postgres allows to be NULL must degrade, not 500."""
        row = self._row(
            description=None,
            plan_type=None,
            daily_request_limit=None,
            monthly_request_limit=None,
            daily_token_limit=None,
            monthly_token_limit=None,
            price_per_month=None,
            features=None,
            is_active=None,
            max_concurrent_requests=None,
            is_pay_as_you_go=None,
        )
        with patch("src.routes.plans.get_all_plans", return_value=[row]):
            resp = client.get("/plans")

        assert resp.status_code == 200
        plan = resp.json()[0]
        assert plan["plan_type"] == "free"
        assert plan["description"] == ""
        assert plan["features"] == []
        assert plan["is_active"] is True
        assert plan["price_per_month"] == 0.0
        assert plan["max_concurrent_requests"] == 5
        assert plan["is_pay_as_you_go"] is False

    def test_get_plans_preserves_non_null_plan_type(self, client):
        rows = [
            self._row(id=7, name="Admin", plan_type="admin"),
            self._row(id=1, name="Free", plan_type=None),
        ]
        with patch("src.routes.plans.get_all_plans", return_value=rows):
            resp = client.get("/plans")

        assert resp.status_code == 200
        by_id = {p["id"]: p["plan_type"] for p in resp.json()}
        assert by_id == {7: "admin", 1: "free"}

    def test_get_plans_sorts_by_production_plan_type_vocabulary(self, client):
        """The sort table must be the vocabulary the `plans` table actually holds.

        Production plan_type values are free, trial, starter, professional,
        business, enterprise, admin (backfilled from plans.name by
        20260915164500). The route used to sort on free/dev/team/customize -- the
        old PlanType enum -- of which only "free" ever appeared in the table, so
        six of seven rows tied on the fallback key and the ordering was
        effectively "Free first, then whatever order the database returned".
        """
        rows = [
            self._row(id=7, name="Admin", plan_type="admin"),
            self._row(id=4, name="Professional", plan_type="professional"),
            self._row(id=1, name="Free", plan_type=None),
            self._row(id=3, name="Starter", plan_type="starter"),
            self._row(id=2, name="Free Trial", plan_type="trial"),
        ]
        with patch("src.routes.plans.get_all_plans", return_value=rows):
            resp = client.get("/plans")

        assert resp.status_code == 200
        assert [p["id"] for p in resp.json()] == [1, 2, 3, 4, 7]

    def test_get_plans_sorts_unknown_plan_type_last(self, client):
        rows = [
            self._row(id=99, name="Mystery", plan_type="not_a_known_tier"),
            self._row(id=1, name="Free", plan_type=None),
        ]
        with patch("src.routes.plans.get_all_plans", return_value=rows):
            resp = client.get("/plans")

        # "free" is in the sort table, "not_a_known_tier" is not.
        assert [p["id"] for p in resp.json()] == [1, 99]

    def test_get_plan_by_id_with_null_plan_type_returns_200(self, client):
        with patch("src.routes.plans.get_plan_by_id", return_value=self._row()):
            resp = client.get("/plans/1")

        assert resp.status_code == 200
        assert resp.json()["plan_type"] == "free"

    def test_features_dict_is_coerced_to_list(self, client):
        row = self._row(features={"streaming": True, "tools": True})
        with patch("src.routes.plans.get_all_plans", return_value=[row]):
            resp = client.get("/plans")

        assert resp.status_code == 200
        assert sorted(resp.json()[0]["features"]) == ["streaming", "tools"]
