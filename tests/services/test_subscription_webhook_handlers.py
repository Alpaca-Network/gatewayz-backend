"""
Tests for Stripe subscription webhook handlers
Tests the plan upgrade recognition fix
"""

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


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

    with patch.dict(
        "os.environ",
        {
            "STRIPE_SECRET_KEY": "sk_test_123",
            "STRIPE_WEBHOOK_SECRET": "whsec_test_123",
            "STRIPE_PUBLISHABLE_KEY": "pk_test_123",
        },
    ):
        return StripeService()


def _mock_get_plan_id_by_tier(fake_supabase, tier):
    """Mock implementation of get_plan_id_by_tier for testing"""
    for plan in fake_supabase.store["plans"]:
        if plan.get("is_active") and tier.lower() in plan.get("name", "").lower():
            return plan["id"]
    return None


class TestSubscriptionCreatedWebhook:
    """Test customer.subscription.created webhook handler"""

    def test_subscription_created_creates_user_plan_entry(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created webhook creates user_plans entry"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert(
            {
                "id": 1,
                "name": "Pro",
                "is_active": True,
                "daily_request_limit": 1000,
                "monthly_request_limit": 30000,
                "daily_token_limit": 200000,
                "monthly_token_limit": 6000000,
                "price_per_month": 29,
                "features": ["basic_models"],
            }
        ).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "subscription_status": "trial"}
        ).execute()

        # Create mock subscription object
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {"user_id": "42", "tier": "pro", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

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

    def test_subscription_created_deactivates_old_plans(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created deactivates existing user plans"""
        fake_supabase.clear_all()

        # Setup: create plans and user with existing plan
        fake_supabase.table("plans").insert(
            [
                {"id": 1, "name": "Basic", "is_active": True},
                {"id": 2, "name": "Pro", "is_active": True},
            ]
        ).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "subscription_status": "trial"}
        ).execute()

        fake_supabase.table("user_plans").insert(
            {
                "id": 100,
                "user_id": 42,
                "plan_id": 1,
                "started_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "is_active": True,
            }
        ).execute()

        # Create mock subscription for Pro upgrade
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {"user_id": "42", "tier": "pro", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

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

    def test_subscription_created_handles_missing_plan(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created handles tier with no matching plan gracefully"""
        fake_supabase.clear_all()

        # Setup: user exists but no matching plan for tier
        fake_supabase.table("users").insert({"id": 42, "email": "user@example.com"}).execute()

        # Create mock subscription for unknown tier
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_123"
        mock_subscription.metadata = {
            "user_id": "42",
            "tier": "unknown_tier",
            "product_id": "prod_test",
        }
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

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

    def test_subscription_updated_active_creates_plan_entry(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.updated with status=active creates/updates user_plans"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({"id": 2, "name": "Max", "is_active": True}).execute()

        fake_supabase.table("users").insert({"id": 42, "email": "user@example.com"}).execute()

        # Create mock subscription (status=active, tier=max)
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_456"
        mock_subscription.status = "active"
        mock_subscription.metadata = {"user_id": "42", "tier": "max", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_456"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

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

    def test_subscription_updated_past_due_does_not_create_plan(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.updated with status=past_due doesn't create plan and downgrades tier"""
        fake_supabase.clear_all()

        # Setup: user with active subscription
        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "tier": "pro", "subscription_status": "active"}
        ).execute()

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

    def test_subscription_updated_tier_change_pro_to_max(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test upgrading from Pro to Max tier"""
        fake_supabase.clear_all()

        # Setup: plans and user with Pro subscription
        fake_supabase.table("plans").insert(
            [
                {"id": 1, "name": "Pro", "is_active": True},
                {"id": 2, "name": "Max", "is_active": True},
            ]
        ).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "tier": "pro", "subscription_status": "active"}
        ).execute()

        fake_supabase.table("user_plans").insert(
            {
                "id": 100,
                "user_id": 42,
                "plan_id": 1,
                "started_at": (datetime.now(UTC) - timedelta(days=15)).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(days=15)).isoformat(),
                "is_active": True,
            }
        ).execute()

        # Create mock subscription for Max tier
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_max"
        mock_subscription.status = "active"
        mock_subscription.metadata = {"user_id": "42", "tier": "max"}
        mock_subscription.customer = "cust_test"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

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


class TestTierResolutionFromSubscriptionItems:
    """Test tier resolution from subscription items when metadata is missing/basic"""

    def test_resolve_tier_from_subscription_metadata_present(self, stripe_service_with_mock_db):
        """Test that tier from metadata is used when present"""
        mock_subscription = MagicMock()
        mock_subscription.status = "active"

        tier, product_id = stripe_service_with_mock_db._resolve_tier_from_subscription(
            mock_subscription, "pro"
        )

        assert tier == "pro"
        assert product_id is None

    def test_resolve_tier_from_subscription_metadata_missing_defaults_to_pro_for_active(
        self, stripe_service_with_mock_db
    ):
        """Test that active subscriptions default to 'pro' when tier can't be determined"""
        mock_subscription = MagicMock()
        mock_subscription.status = "active"
        mock_subscription.id = "sub_test_123"
        mock_subscription.items = None

        tier, product_id = stripe_service_with_mock_db._resolve_tier_from_subscription(
            mock_subscription, None
        )

        assert tier == "pro"
        assert product_id is None

    def test_resolve_tier_from_subscription_metadata_basic_defaults_to_pro_for_active(
        self, stripe_service_with_mock_db
    ):
        """Test that 'basic' metadata is upgraded to 'pro' for active subscriptions"""
        mock_subscription = MagicMock()
        mock_subscription.status = "active"
        mock_subscription.id = "sub_test_123"
        mock_subscription.items = None

        tier, product_id = stripe_service_with_mock_db._resolve_tier_from_subscription(
            mock_subscription, "basic"
        )

        assert tier == "pro"
        assert product_id is None

    def test_resolve_tier_from_subscription_items_with_product_lookup(
        self, stripe_service_with_mock_db, monkeypatch
    ):
        """Test tier resolution from subscription items when product_id is mapped"""
        # Mock get_tier_from_product_id to return 'max' for our test product
        monkeypatch.setattr(
            "src.services.payments.get_tier_from_product_id",
            lambda product_id: "max" if product_id == "prod_max_123" else "basic",
        )

        mock_subscription = MagicMock()
        mock_subscription.status = "active"
        mock_subscription.id = "sub_test_123"

        # Create mock subscription items structure
        mock_price = MagicMock()
        mock_price.product = "prod_max_123"

        mock_item = MagicMock()
        mock_item.price = mock_price

        mock_items = MagicMock()
        mock_items.data = [mock_item]

        mock_subscription.items = mock_items

        tier, product_id = stripe_service_with_mock_db._resolve_tier_from_subscription(
            mock_subscription, None
        )

        assert tier == "max"
        assert product_id == "prod_max_123"

    def test_subscription_created_with_missing_tier_defaults_to_pro(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created with missing tier metadata defaults to 'pro'"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({"id": 1, "name": "Pro", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "subscription_status": "trial"}
        ).execute()

        # Create mock subscription with NO tier in metadata
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_no_tier"
        mock_subscription.status = "active"
        mock_subscription.metadata = {
            "user_id": "42",
            # No "tier" key - simulating missing tier
            "product_id": "prod_unmapped",
        }
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )
        mock_subscription.items = None

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # Verify user was updated with 'pro' (default fallback)
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert len(users) == 1
        assert users[0]["tier"] == "pro"  # Should default to pro, not basic
        assert users[0]["subscription_status"] == "active"

    def test_subscription_created_with_basic_tier_defaults_to_pro(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created with tier='basic' upgrades to 'pro'"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({"id": 1, "name": "Pro", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "subscription_status": "trial"}
        ).execute()

        # Create mock subscription with tier='basic' in metadata
        # This simulates the case where product_id lookup failed
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_basic_tier"
        mock_subscription.status = "active"
        mock_subscription.metadata = {
            "user_id": "42",
            "tier": "basic",  # This shouldn't happen for a paid subscription
            "product_id": "prod_unmapped",
        }
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )
        mock_subscription.items = None

        # Call the handler
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # Verify user was updated with 'pro' (default fallback)
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert len(users) == 1
        assert users[0]["tier"] == "pro"  # Should upgrade to pro
        assert users[0]["subscription_status"] == "active"


class TestCheckoutCompletedTrialStatusClearing:
    """Test that checkout completed webhook clears trial status"""

    def test_checkout_completed_clears_trial_status(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that checkout.session.completed clears trial status for users"""
        fake_supabase.clear_all()

        # Setup: create a trial user with trial API key
        fake_supabase.table("users").insert(
            {
                "id": 42,
                "email": "trial_user@example.com",
                "subscription_status": "trial",
                "credits": 5.0,
            }
        ).execute()

        fake_supabase.table("api_keys_new").insert(
            {
                "id": 1,
                "user_id": 42,
                "api_key": "test_key_123",
                "is_trial": True,
                "trial_converted": False,
                "subscription_status": "trial",
            }
        ).execute()

        # Create mock checkout session
        mock_session = MagicMock()
        mock_session.id = "cs_test_trial_clear"
        mock_session.payment_intent = "pi_test_123"
        mock_session.metadata = {
            "user_id": "42",
            "payment_id": "100",
            "credits_cents": "1000",  # $10
        }
        mock_session.amount_total = 1000
        mock_session.currency = "usd"

        # Mock the necessary dependencies
        with (
            patch("src.services.payments.get_payment_by_stripe_intent", return_value=None),
            patch("src.services.payments.create_payment", return_value={"id": 100}),
            patch("src.services.payments.add_credits_to_user") as mock_add_credits,
            patch("src.services.payments.update_payment_status"),
        ):

            # Call the handler
            stripe_service_with_mock_db._handle_checkout_completed(mock_session)

            # Verify add_credits_to_user was called
            mock_add_credits.assert_called_once()

        # Verify user's subscription_status was updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert len(users) == 1
        assert users[0]["subscription_status"] == "active"

        # Verify API key trial status was cleared
        api_keys = fake_supabase.table("api_keys_new").select("*").eq("user_id", 42).execute().data
        assert len(api_keys) == 1
        assert api_keys[0]["is_trial"] is False
        assert api_keys[0]["trial_converted"] is True
        assert api_keys[0]["subscription_status"] == "active"

    def test_checkout_completed_clears_multiple_api_keys(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that checkout.session.completed clears trial status for all user's API keys"""
        fake_supabase.clear_all()

        # Setup: create a trial user with multiple API keys
        fake_supabase.table("users").insert(
            {
                "id": 42,
                "email": "trial_user@example.com",
                "subscription_status": "trial",
                "credits": 5.0,
            }
        ).execute()

        # Create multiple trial API keys
        fake_supabase.table("api_keys_new").insert(
            [
                {
                    "id": 1,
                    "user_id": 42,
                    "api_key": "test_key_1",
                    "is_trial": True,
                    "trial_converted": False,
                    "subscription_status": "trial",
                },
                {
                    "id": 2,
                    "user_id": 42,
                    "api_key": "test_key_2",
                    "is_trial": True,
                    "trial_converted": False,
                    "subscription_status": "trial",
                },
            ]
        ).execute()

        # Create mock checkout session
        mock_session = MagicMock()
        mock_session.id = "cs_test_multi_key"
        mock_session.payment_intent = "pi_test_456"
        mock_session.metadata = {
            "user_id": "42",
            "payment_id": "101",
            "credits_cents": "2000",  # $20
        }
        mock_session.amount_total = 2000
        mock_session.currency = "usd"

        # Mock the necessary dependencies
        with (
            patch("src.services.payments.get_payment_by_stripe_intent", return_value=None),
            patch("src.services.payments.create_payment", return_value={"id": 101}),
            patch("src.services.payments.add_credits_to_user"),
            patch("src.services.payments.update_payment_status"),
        ):

            # Call the handler
            stripe_service_with_mock_db._handle_checkout_completed(mock_session)

        # Verify all API keys had trial status cleared
        api_keys = fake_supabase.table("api_keys_new").select("*").eq("user_id", 42).execute().data
        assert len(api_keys) == 2
        for key in api_keys:
            assert key["is_trial"] is False
            assert key["trial_converted"] is True
            assert key["subscription_status"] == "active"

    def test_checkout_completed_does_not_fail_if_no_api_keys(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that checkout completed doesn't fail if user has no API keys"""
        fake_supabase.clear_all()

        # Setup: create a user without API keys
        fake_supabase.table("users").insert(
            {
                "id": 42,
                "email": "no_keys_user@example.com",
                "subscription_status": "trial",
                "credits": 0,
            }
        ).execute()

        # Create mock checkout session
        mock_session = MagicMock()
        mock_session.id = "cs_test_no_keys"
        mock_session.payment_intent = "pi_test_789"
        mock_session.metadata = {"user_id": "42", "payment_id": "102", "credits_cents": "500"}  # $5
        mock_session.amount_total = 500
        mock_session.currency = "usd"

        # Mock the necessary dependencies
        with (
            patch("src.services.payments.get_payment_by_stripe_intent", return_value=None),
            patch("src.services.payments.create_payment", return_value={"id": 102}),
            patch("src.services.payments.add_credits_to_user"),
            patch("src.services.payments.update_payment_status"),
        ):

            # Should not raise an exception
            stripe_service_with_mock_db._handle_checkout_completed(mock_session)

        # Verify user's subscription_status was still updated
        users = fake_supabase.table("users").select("*").eq("id", 42).execute().data
        assert len(users) == 1
        assert users[0]["subscription_status"] == "active"


class TestCacheInvalidationOnSubscriptionUpdates:
    """Test that cache is invalidated when subscription status changes

    These tests verify the fix for the bug where user's tier/credits page and header
    wouldn't update after payment because the user cache wasn't being invalidated.
    """

    def test_subscription_created_invalidates_cache(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created webhook invalidates user cache"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({"id": 1, "name": "Pro", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {"id": 42, "email": "user@example.com", "subscription_status": "trial"}
        ).execute()

        # Create mock subscription object
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_cache_invalidation"
        mock_subscription.metadata = {"user_id": "42", "tier": "pro", "product_id": "prod_test"}
        mock_subscription.customer = "cust_test_123"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

        # Mock invalidate_user_cache_by_id
        with patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate:
            stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

            # Verify cache was invalidated for the correct user
            mock_invalidate.assert_called_once_with(42)

    def test_subscription_updated_invalidates_cache(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.updated webhook invalidates user cache"""
        fake_supabase.clear_all()

        # Setup: create plan and user
        fake_supabase.table("plans").insert({"id": 2, "name": "Max", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {"id": 99, "email": "user@example.com", "subscription_status": "active", "tier": "pro"}
        ).execute()

        # Create mock subscription
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_updated_cache"
        mock_subscription.status = "active"
        mock_subscription.metadata = {"user_id": "99", "tier": "max"}
        mock_subscription.customer = "cust_test"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )

        # Mock invalidate_user_cache_by_id
        with patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate:
            stripe_service_with_mock_db._handle_subscription_updated(mock_subscription)

            # Verify cache was invalidated for the correct user
            mock_invalidate.assert_called_once_with(99)

    def test_subscription_deleted_invalidates_cache(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.deleted webhook invalidates user cache"""
        fake_supabase.clear_all()

        # Setup: create user with active subscription
        fake_supabase.table("users").insert(
            {"id": 77, "email": "user@example.com", "subscription_status": "active", "tier": "pro"}
        ).execute()

        # Create mock subscription for deletion
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test_deleted"
        mock_subscription.metadata = {"user_id": "77"}

        # Mock invalidate_user_cache_by_id
        with patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate:
            stripe_service_with_mock_db._handle_subscription_deleted(mock_subscription)

            # Verify cache was invalidated for the correct user
            mock_invalidate.assert_called_once_with(77)

    def test_invoice_payment_failed_invalidates_cache(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that invoice.payment_failed webhook invalidates user cache"""
        fake_supabase.clear_all()

        # Setup: create user with active subscription
        fake_supabase.table("users").insert(
            {"id": 88, "email": "user@example.com", "subscription_status": "active", "tier": "pro"}
        ).execute()

        # Create mock invoice with subscription
        mock_invoice = MagicMock()
        mock_invoice.id = "inv_test_failed"
        mock_invoice.subscription = "sub_test_88"

        # Create mock subscription that will be retrieved
        mock_subscription = MagicMock()
        mock_subscription.metadata = {"user_id": "88"}

        # Mock stripe.Subscription.retrieve and invalidate_user_cache_by_id
        with (
            patch("stripe.Subscription.retrieve", return_value=mock_subscription),
            patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate,
        ):
            stripe_service_with_mock_db._handle_invoice_payment_failed(mock_invoice)

            # Verify cache was invalidated for the correct user
            mock_invalidate.assert_called_once_with(88)

    def test_checkout_completed_invalidates_cache_after_all_updates(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that checkout.session.completed invalidates cache AFTER all user updates

        This is critical because add_credits_to_user invalidates cache early, but
        subsequent updates (subscription_status, trial status) happen after that.
        We need to ensure cache is invalidated again at the end.
        """
        fake_supabase.clear_all()

        # Setup: create a trial user
        fake_supabase.table("users").insert(
            {
                "id": 55,
                "email": "checkout_user@example.com",
                "subscription_status": "trial",
                "credits": 5.0,
            }
        ).execute()

        fake_supabase.table("api_keys_new").insert(
            {
                "id": 1,
                "user_id": 55,
                "api_key": "test_key_checkout",
                "is_trial": True,
                "trial_converted": False,
                "subscription_status": "trial",
            }
        ).execute()

        # Create mock checkout session
        mock_session = MagicMock()
        mock_session.id = "cs_test_cache_final"
        mock_session.payment_intent = "pi_test_cache"
        mock_session.metadata = {"user_id": "55", "payment_id": "200", "credits_cents": "1000"}
        mock_session.amount_total = 1000
        mock_session.currency = "usd"

        # Mock dependencies and track cache invalidation calls
        with (
            patch("src.services.payments.get_payment_by_stripe_intent", return_value=None),
            patch("src.services.payments.create_payment", return_value={"id": 200}),
            patch("src.services.payments.add_credits_to_user"),
            patch("src.services.payments.update_payment_status"),
            patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate,
        ):

            stripe_service_with_mock_db._handle_checkout_completed(mock_session)

            # Verify cache was invalidated for the correct user (at the end after all updates)
            mock_invalidate.assert_called_with(55)


class TestUserIdExtractionFallback:
    """Test user_id extraction with fallback to stripe_customer_id lookup"""

    def test_extract_user_id_from_metadata(self, stripe_service_with_mock_db, fake_supabase):
        """Test that user_id is extracted from metadata when present"""
        mock_subscription = MagicMock()
        mock_subscription.metadata = {"user_id": "42"}
        mock_subscription.customer = "cus_test_123"

        user_id = stripe_service_with_mock_db._extract_user_id_from_subscription(mock_subscription)

        assert user_id == 42

    def test_extract_user_id_fallback_to_customer_lookup(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that user_id is looked up by stripe_customer_id when metadata is missing"""
        fake_supabase.clear_all()

        # Setup: create user with stripe_customer_id
        fake_supabase.table("users").insert(
            {
                "id": 99,
                "email": "fallback_user@example.com",
                "stripe_customer_id": "cus_fallback_test",
            }
        ).execute()

        # Create mock subscription with NO user_id in metadata
        mock_subscription = MagicMock()
        mock_subscription.metadata = {}  # No user_id
        mock_subscription.customer = "cus_fallback_test"

        user_id = stripe_service_with_mock_db._extract_user_id_from_subscription(mock_subscription)

        assert user_id == 99

    def test_extract_user_id_returns_none_when_not_found(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that None is returned when user cannot be identified"""
        fake_supabase.clear_all()

        # Create mock subscription with NO user_id and unknown customer
        mock_subscription = MagicMock()
        mock_subscription.metadata = {}  # No user_id
        mock_subscription.customer = "cus_unknown"

        user_id = stripe_service_with_mock_db._extract_user_id_from_subscription(mock_subscription)

        assert user_id is None

    def test_subscription_created_uses_fallback_lookup(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created uses customer_id fallback when metadata missing"""
        fake_supabase.clear_all()

        # Setup: create plan and user with stripe_customer_id
        fake_supabase.table("plans").insert({"id": 1, "name": "Pro", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {
                "id": 77,
                "email": "fallback_test@example.com",
                "subscription_status": "trial",
                "stripe_customer_id": "cus_fallback_sub_test",
            }
        ).execute()

        # Create mock subscription with NO user_id but valid customer
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_fallback_test"
        mock_subscription.metadata = {
            # No "user_id" key!
            "tier": "pro",
            "product_id": "prod_test",
        }
        mock_subscription.customer = "cus_fallback_sub_test"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )
        mock_subscription.items = None

        # Call the handler - should succeed by looking up user via customer_id
        stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        # Verify user was updated
        users = fake_supabase.table("users").select("*").eq("id", 77).execute().data
        assert len(users) == 1
        assert users[0]["subscription_status"] == "active"
        assert users[0]["tier"] == "pro"

    def test_subscription_created_raises_when_user_not_found(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.created raises ValueError when user cannot be identified"""
        fake_supabase.clear_all()

        # Create mock subscription with NO user_id and unknown customer
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_unknown_user"
        mock_subscription.metadata = {}  # No user_id
        mock_subscription.customer = "cus_completely_unknown"
        mock_subscription.current_period_end = None
        mock_subscription.items = None

        # Call should raise ValueError
        with pytest.raises(ValueError) as excinfo:
            stripe_service_with_mock_db._handle_subscription_created(mock_subscription)

        assert "Missing user_id" in str(excinfo.value)
        assert "sub_unknown_user" in str(excinfo.value)

    def test_subscription_updated_uses_fallback_lookup(
        self, stripe_service_with_mock_db, fake_supabase
    ):
        """Test that subscription.updated uses customer_id fallback when metadata missing"""
        fake_supabase.clear_all()

        # Setup: create plan and user with stripe_customer_id
        fake_supabase.table("plans").insert({"id": 2, "name": "Max", "is_active": True}).execute()

        fake_supabase.table("users").insert(
            {
                "id": 88,
                "email": "update_fallback@example.com",
                "subscription_status": "active",
                "tier": "pro",
                "stripe_customer_id": "cus_update_fallback",
            }
        ).execute()

        # Create mock subscription with NO user_id but valid customer
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_update_fallback"
        mock_subscription.status = "active"
        mock_subscription.metadata = {
            # No "user_id" key!
            "tier": "max"
        }
        mock_subscription.customer = "cus_update_fallback"
        mock_subscription.current_period_end = int(
            (datetime.now(UTC) + timedelta(days=30)).timestamp()
        )
        mock_subscription.items = None

        # Call the handler - should succeed by looking up user via customer_id
        stripe_service_with_mock_db._handle_subscription_updated(mock_subscription)

        # Verify user was updated to max tier
        users = fake_supabase.table("users").select("*").eq("id", 88).execute().data
        assert len(users) == 1
        assert users[0]["tier"] == "max"

    def test_lookup_user_by_stripe_customer(self, stripe_service_with_mock_db, fake_supabase):
        """Test the _lookup_user_by_stripe_customer helper method"""
        fake_supabase.clear_all()

        # Setup: create user with stripe_customer_id
        fake_supabase.table("users").insert(
            {
                "id": 123,
                "email": "lookup_test@example.com",
                "stripe_customer_id": "cus_lookup_test_123",
            }
        ).execute()

        # Test successful lookup
        user_id = stripe_service_with_mock_db._lookup_user_by_stripe_customer("cus_lookup_test_123")
        assert user_id == 123

        # Test lookup with unknown customer
        user_id = stripe_service_with_mock_db._lookup_user_by_stripe_customer("cus_unknown_xyz")
        assert user_id is None

        # Test lookup with None
        user_id = stripe_service_with_mock_db._lookup_user_by_stripe_customer(None)
        assert user_id is None
