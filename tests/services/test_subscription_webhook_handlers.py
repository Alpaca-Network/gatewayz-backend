"""
Tests for Stripe subscription webhook handlers
Tests the plan upgrade recognition fix
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import sys
import types
import importlib

# Mock Supabase for testing
class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._filters = []
        self._last_update = None

    def select(self, _cols="*"):
        return self

    def eq(self, field, value):
        self._filters.append((field, "eq", value))
        return self

    def update(self, patch):
        out = []
        for r in self.store[self.name]:
            if self._match(r):
                r.update(patch)
                out.append(r)
        self._last_update = out
        return self

    def insert(self, data):
        rows = data if isinstance(data, list) else [data]
        for r in rows:
            if "id" not in r:
                r["id"] = len(self.store[self.name]) + 1
            self.store[self.name].append(r)
        return self

    def _match(self, row):
        for field, op, val in self._filters:
            if op == "eq":
                if row.get(field) != val:
                    return False
        return True

    def execute(self):
        if self._last_update is not None:
            return _Result(self._last_update)
        rows = [r for r in self.store[self.name] if self._match(r)]
        return _Result(rows)


class FakeSupabase:
    def __init__(self):
        self.store = {
            "users": [],
            "user_plans": [],
            "plans": [],
            "api_keys_new": [],
        }

    def table(self, name):
        if name not in self.store:
            self.store[name] = []
        return _Table(self.store, name)

    def clear_all(self):
        for key in self.store:
            self.store[key].clear()


@pytest.fixture
def fake_supabase():
    sb = FakeSupabase()
    yield sb
    sb.clear_all()


@pytest.fixture
def stripe_service_with_mock_db(fake_supabase, monkeypatch):
    """Create StripeService with mocked database"""
    # Stub out get_supabase_client
    supabase_mod = types.SimpleNamespace(get_supabase_client=lambda: fake_supabase)
    monkeypatch.setitem(sys.modules, "src.config.supabase_config", supabase_mod)

    # Stub out get_plan_id_by_tier from plans module
    plans_mod = types.SimpleNamespace(
        get_plan_id_by_tier=lambda tier: _mock_get_plan_id_by_tier(fake_supabase, tier)
    )
    monkeypatch.setitem(sys.modules, "src.db.plans", plans_mod)

    # Import and reload the payments service
    from src.services.payments import StripeService

    with patch.dict('os.environ', {
        'STRIPE_SECRET_KEY': 'sk_test_123',
        'STRIPE_WEBHOOK_SECRET': 'whsec_test_123',
        'STRIPE_PUBLISHABLE_KEY': 'pk_test_123'
    }):
        return StripeService()


def _mock_get_plan_id_by_tier(fake_supabase, tier):
    """Mock implementation of get_plan_id_by_tier for testing"""
    for plan in fake_supabase.store["plans"]:
        if plan.get("is_active") and tier.lower() in plan.get("name", "").lower():
            return plan["id"]
    return None


class TestSubscriptionCreatedWebhook:
    """Test customer.subscription.created webhook handler"""

    def test_subscription_created_creates_user_plan_entry(self, stripe_service_with_mock_db, fake_supabase):
        """Test that subscription.created webhook creates user_plans entry"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({
            "id": 1,
            "name": "Pro",
            "is_active": True,
            "daily_request_limit": 1000,
            "monthly_request_limit": 30000,
            "daily_token_limit": 200000,
            "monthly_token_limit": 6000000,
            "price_per_month": 29,
            "features": ["basic_models"]
        }).execute()

        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com",
            "subscription_status": "trial"
        }).execute()

        # Create mock subscription object
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {
            "user_id": "42",
            "tier": "pro",
            "product_id": "prod_test"
        }
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # Verify user was updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert len(users) == 1
        assert users[0]["tier"] == "pro"
        assert users[0]["subscription_status"] == "active"

        # Verify user_plans entry was created
        user_plans = fake_supabase.table("user_plans").select("*").eq("user_id", 42).execute().data
        assert len(user_plans) == 1
        assert user_plans[0]["plan_id"] == 1
        assert user_plans[0]["is_active"] is True
        assert user_plans[0]["user_id"] == 42


    def test_subscription_created_deactivates_old_plans(self, stripe_service_with_mock_db, fake_supabase):
        """Test that subscription.created deactivates existing user plans"""
        fake_supabase.clear_all()

        # Setup: create plans and user with existing plan
        fake_supabase.table("plans").insert([
            {"id": 1, "name": "Basic", "is_active": True},
            {"id": 2, "name": "Pro", "is_active": True},
        ]).execute()

        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com",
            "subscription_status": "trial"
        }).execute()

        fake_supabase.table("user_plans").insert({
            "id": 100,
            "user_id": 42,
            "plan_id": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "is_active": True
        }).execute()

        # Create mock subscription for Pro upgrade
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {"user_id": "42", "tier": "pro", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # Verify old plan was deactivated
        old_plans = fake_supabase.table("user_plans").select("*").eq("id", 100).execute().data
        assert len(old_plans) == 1
        assert old_plans[0]["is_active"] is False

        # Verify new plan was created
        new_plans = fake_supabase.table("user_plans").select("*").eq("plan_id", 2).execute().data
        assert len(new_plans) == 1
        assert new_plans[0]["is_active"] is True


    def test_subscription_created_handles_missing_plan(self, stripe_service_with_mock_db, fake_supabase):
        """Test that subscription.created handles tier with no matching plan gracefully"""
        fake_supabase.clear_all()

        # Setup: user exists but no matching plan for tier
        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com"
        }).execute()

        # Create mock subscription for unknown tier
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {"user_id": "42", "tier": "unknown_tier", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        # Call should not raise even if plan not found
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # User should still be updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert users[0]["tier"] == "unknown_tier"
        assert users[0]["subscription_status"] == "active"

        # But no user_plans entry should be created
        user_plans = fake_supabase.table("user_plans").select("*").eq("user_id", 42).execute().data
        assert len(user_plans) == 0


class TestSubscriptionUpdatedWebhook:
    """Test customer.subscription.updated webhook handler"""

    def test_subscription_updated_active_creates_plan_entry(self, stripe_service_with_mock_db, fake_supabase):
        """Test that subscription.updated with status=active creates/updates user_plans"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({
            "id": 2,
            "name": "Max",
            "is_active": True
        }).execute()

        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com"
        }).execute()

        # Create mock subscription (status=active, tier=max)
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_456"
        mock_subscription.status = "active"
        mock_subscription.metadata = {"user_id": "42", "tier": "max", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_456"
        mock_subscription.current_period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_updated(mock_subscription)

        # Verify user was updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert users[0]["tier"] == "max"
        assert users[0]["subscription_status"] == "active"

        # Verify user_plans entry was created
        user_plans = fake_supabase.table("user_plans").select("*").eq("user_id", 42).execute().data
        assert len(user_plans) == 1
        assert user_plans[0]["plan_id"] == 2
        assert user_plans[0]["is_active"] is True


    def test_subscription_updated_past_due_does_not_create_plan(self, stripe_service_with_mock_db, fake_supabase):
        """Test that subscription.updated with status=past_due doesn't create plan and downgrades tier"""
        fake_supabase.clear_all()

        # Setup: user with active subscription
        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com",
            "tier": "pro",
            "subscription_status": "active"
        }).execute()

        # Create mock subscription (status=past_due)
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_789"
        mock_subscription.status = "past_due"
        mock_subscription.metadata = {"user_id": "42", "tier": "pro"}
        mock_subscription.customer = "cust_test_789"
        mock_subscription.current_period_end = None

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_updated(mock_subscription)

        # Verify user was downgraded to basic
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert users[0]["tier"] == "basic"
        assert users[0]["subscription_status"] == "past_due"

        # Verify no user_plans entry was created (status != active)
        user_plans = fake_supabase.table("user_plans").select("*").eq("user_id", 42).execute().data
        assert len(user_plans) == 0


    def test_subscription_updated_tier_change_pro_to_max(self, stripe_service_with_mock_db, fake_supabase):
        """Test upgrading from Pro to Max tier"""
        fake_supabase.clear_all()

        # Setup: plans and user with Pro subscription
        fake_supabase.table("plans").insert([
            {"id": 1, "name": "Pro", "is_active": True},
            {"id": 2, "name": "Max", "is_active": True},
        ]).execute()

        fake_supabase.table("users").insert({
            "id": 42,
            "email": "user@example.com",
            "tier": "pro",
            "subscription_status": "active"
        }).execute()

        fake_supabase.table("user_plans").insert({
            "id": 100,
            "user_id": 42,
            "plan_id": 1,
            "started_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
            "is_active": True
        }).execute()

        # Create mock subscription for Max tier
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_max"
        mock_subscription.status = "active"
        mock_subscription.metadata = {"user_id": "42", "tier": "max"}
        mock_subscription.customer = "cust_test"
        mock_subscription.current_period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_updated(mock_subscription)

        # Verify old plan was deactivated
        old_plans = fake_supabase.table("user_plans").select("*").eq("id", 100).execute().data
        assert old_plans[0]["is_active"] is False

        # Verify new plan was created for Max
        new_plans = fake_supabase.table("user_plans").select("*").eq("plan_id", 2).execute().data
        assert len(new_plans) == 1
        assert new_plans[0]["is_active"] is True

        # Verify user tier was updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert users[0]["tier"] == "max"
