"""Billing identity hardening for the profile and checkout routes.

- GET /user/profile returns the account's own ``stripe_customer_id``.
- PUT /user/profile cannot change the account email (no verification there);
  re-sending the current email is a no-op.
- Subscription checkout creates the Stripe customer with the account's stored
  email, never a client-supplied ``customer_email``.

Nothing here talks to Stripe or Supabase.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.db.users as users_db
from src.main import app
from src.routes.users import EMAIL_CHANGE_NOT_SUPPORTED
from src.schemas.payments import CreateSubscriptionCheckoutRequest

client = TestClient(app)

API_KEY = "gw_live_test_key_billing_identity_123456"


def _user(**overrides):
    base = {
        "id": 7,
        "email": "owner@example.com",
        "username": "owner",
        "api_key": API_KEY,
        "credits": 5.0,
        "subscription_allowance": 0,
        "purchased_credits": 5.0,
        "auth_method": "privy",
        "subscription_status": "active",
        "tier": "basic",
        "trial_expires_at": None,
        "subscription_end_date": None,
        "is_active": True,
        "registration_date": "2026-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "settings": None,
        "stripe_customer_id": "cus_owner",
    }
    base.update(overrides)
    return base


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


# ---------------------------------------------------------------------------
# GET /user/profile
# ---------------------------------------------------------------------------
class TestProfileStripeCustomerId:
    def test_db_profile_includes_stripe_customer_id(self):
        with patch.object(users_db, "get_user", return_value=_user()):
            profile = users_db.get_user_profile(API_KEY)
        assert profile["stripe_customer_id"] == "cus_owner"

    def test_db_profile_stripe_customer_id_none_when_unset(self):
        with patch.object(users_db, "get_user", return_value=_user(stripe_customer_id=None)):
            profile = users_db.get_user_profile(API_KEY)
        assert profile["stripe_customer_id"] is None

    def test_get_profile_returns_own_stripe_customer_id(self):
        with patch("src.db.users.get_user", return_value=_user()):
            response = client.get("/user/profile", headers=_auth())
        assert response.status_code == 200
        assert response.json()["stripe_customer_id"] == "cus_owner"


# ---------------------------------------------------------------------------
# PUT /user/profile
# ---------------------------------------------------------------------------
class TestProfileEmailNotChangeable:
    def _put(self, body, user=None):
        user = user or _user()
        update = MagicMock(return_value=user)
        with (
            patch("src.db.users.get_user", return_value=user),
            patch("src.db.users.update_user_profile", update),
        ):
            response = client.put("/user/profile", json=body, headers=_auth())
        return response, update

    def test_changing_email_is_refused(self):
        response, update = self._put({"email": "attacker@example.com"})
        assert response.status_code == 403
        assert response.json()["error"]["message"] == EMAIL_CHANGE_NOT_SUPPORTED
        update.assert_not_called()

    def test_changing_email_with_other_fields_is_refused_entirely(self):
        response, update = self._put({"email": "attacker@example.com", "name": "New Name"})
        assert response.status_code == 403
        update.assert_not_called()

    def test_clearing_email_is_refused(self):
        response, update = self._put({"email": None, "name": "New Name"})
        assert response.status_code == 403
        update.assert_not_called()

    def test_resending_current_email_is_a_no_op(self):
        response, update = self._put({"email": " Owner@Example.com "})
        assert response.status_code == 200
        assert response.json()["email"] == "owner@example.com"
        update.assert_not_called()

    def test_current_email_is_dropped_from_other_updates(self):
        response, update = self._put({"email": "owner@example.com", "name": "New Name"})
        assert response.status_code == 200
        update.assert_called_once()
        fields = update.call_args.args[1]
        assert fields == {"name": "New Name"}

    def test_settings_update_still_works(self):
        response, update = self._put({"settings": {"low_balance_notifications": True}})
        assert response.status_code == 200
        assert update.call_args.args[1] == {"settings": {"low_balance_notifications": True}}

    def test_db_layer_never_writes_email(self):
        """Defence in depth: the DB helper drops ``email`` even if a caller passes it."""
        table = MagicMock()
        table.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": 7}]
        )
        supabase = MagicMock()
        supabase.table.return_value = table
        with (
            patch.object(users_db, "get_supabase_client", return_value=supabase),
            patch.object(users_db, "get_user", return_value=_user()),
        ):
            users_db.update_user_profile(API_KEY, {"email": "x@example.com", "name": "N"})
        written = table.update.call_args.args[0]
        assert "email" not in written
        assert written["name"] == "N"

    def test_db_layer_email_only_update_is_rejected(self):
        supabase = MagicMock()
        with (
            patch.object(users_db, "get_supabase_client", return_value=supabase),
            patch.object(users_db, "get_user", return_value=_user()),
            pytest.raises(RuntimeError),
        ):
            users_db.update_user_profile(API_KEY, {"email": "x@example.com"})
        supabase.table.return_value.update.assert_not_called()


# ---------------------------------------------------------------------------
# Subscription checkout: customer created with the stored email
# ---------------------------------------------------------------------------
MOD = "src.services.billing.payments"


@pytest.fixture
def svc():
    payments_module = importlib.import_module(MOD)
    with patch.dict(
        "os.environ",
        {
            "STRIPE_SECRET_KEY": "sk_test_123",
            "STRIPE_WEBHOOK_SECRET": "whsec_test_123",
            "STRIPE_PUBLISHABLE_KEY": "pk_test_123",
            "FRONTEND_URL": "https://test.gatewayz.ai",
        },
    ):
        return payments_module.StripeService()


@pytest.mark.parametrize(
    ("stored_email", "expected"),
    [("owner@example.com", "owner@example.com"), ("did:privy:abc", None)],
)
def test_subscription_customer_uses_stored_email(svc, stored_email, expected):
    request = CreateSubscriptionCheckoutRequest(
        price_id="price_pro",
        product_id="prod_pro",
        customer_email="someone-else@example.com",
        success_url="https://x/s",
        cancel_url="https://x/c",
    )
    price = {"id": "price_pro", "product": "prod_pro", "active": True}
    session = SimpleNamespace(id="cs_s", url="https://stripe/s", status="open")
    supabase = MagicMock()
    with (
        patch(
            f"{MOD}.get_user_by_id",
            return_value={"id": 7, "email": stored_email, "stripe_customer_id": None},
        ),
        patch(f"{MOD}.get_tier_from_product_id", return_value="pro"),
        patch("stripe.Price.retrieve", return_value=price),
        patch("stripe.Customer.create", return_value=SimpleNamespace(id="cus_new")) as create,
        patch("stripe.checkout.Session.create", return_value=session) as checkout,
        patch("src.config.supabase_config.get_supabase_client", return_value=supabase),
    ):
        svc.create_subscription_checkout(7, request)

    assert create.call_args.kwargs["email"] == expected
    assert checkout.call_args.kwargs["customer"] == "cus_new"
    assert "someone-else@example.com" not in str(create.call_args)
    assert "someone-else@example.com" not in str(checkout.call_args)
