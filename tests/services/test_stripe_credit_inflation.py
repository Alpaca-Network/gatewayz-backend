"""
Regression tests for the Stripe credit-inflation / clawback security fixes.

Every Stripe object is a plain dict / MagicMock: nothing here talks to Stripe
or Supabase.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.schemas.payments import (
    CreateCheckoutSessionRequest,
    CreateRefundRequest,
    CreateSubscriptionCheckoutRequest,
    UpgradeSubscriptionRequest,
)

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
            "CREDIT_TOPUP_FEE_RATE": "0",
        },
    ):
        return payments_module.StripeService()


# ---------------------------------------------------------------------------
# Fake Supabase + ledger so clawback logic can be exercised end to end
# ---------------------------------------------------------------------------
class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op, self.payload = db, name, [], "select", None

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def limit(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        elif self.op == "insert":
            rows.append(dict(self.payload))
            hit = [rows[-1]]
        return SimpleNamespace(data=[dict(r) for r in hit])


class FakeDB:
    def __init__(self):
        self.tables = {"users": [], "credit_transactions": [], "payments": []}

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def fake_db():
    db = FakeDB()
    db.tables["users"].append(
        {"id": 1, "purchased_credits": 10.0, "subscription_allowance": 0.0, "tier": "basic"}
    )
    db.tables["payments"].append(
        {
            "id": 7,
            "user_id": 1,
            "amount_cents": 1000,
            "status": "completed",
            "stripe_payment_intent_id": "pi_1",
            "metadata": {},
        }
    )
    db.tables["credit_transactions"].append(
        {
            "id": 1,
            "user_id": 1,
            "payment_id": 7,
            "transaction_type": "purchase",
            "amount": 10.0,
            "request_id": "grant-req",
        }
    )

    def _log(**kw):
        row = dict(kw)
        db.tables["credit_transactions"].append(row)
        return row

    def _get_tx(request_id):
        for r in db.tables["credit_transactions"]:
            if r.get("request_id") == request_id:
                return r
        return None

    with (
        patch("src.config.supabase_config.get_supabase_client", return_value=db),
        patch("src.db.credit_transactions.log_credit_transaction", side_effect=_log),
        patch("src.db.credit_transactions.get_transaction_by_request_id", side_effect=_get_tx),
        patch(
            f"{MOD}.get_payment_by_stripe_intent", side_effect=lambda pi: db.tables["payments"][0]
        ),
        patch(f"{MOD}.get_payment", side_effect=lambda pid: db.tables["payments"][0]),
        patch(
            f"{MOD}.update_payment_metadata",
            side_effect=lambda pid, md: db.tables["payments"][0]["metadata"].update(md),
        ) as upm,
        patch(f"{MOD}.capture_payment_error") as cap,
        patch("src.db.users.invalidate_user_cache_by_id"),
    ):
        db.update_payment_metadata = upm
        db.capture = cap
        yield db


# ===========================================================================
# 1. Metadata injection / webhook amount enforcement
# ===========================================================================
def _checkout_req(**kw):
    base = {
        "amount": 500,
        "currency": "usd",
        "success_url": "https://x/s",
        "cancel_url": "https://x/c",
    }
    base.update(kw)
    return CreateCheckoutSessionRequest(**base)


def _create_checkout(svc, request):
    session = SimpleNamespace(
        id="cs_1", url="https://stripe/x", expires_at=2_000_000_000, payment_intent=None
    )
    with (
        patch(f"{MOD}.get_user_by_id", return_value={"id": 1, "email": "a@b.co"}),
        patch(f"{MOD}.create_payment", return_value={"id": 42}) as cp,
        patch(f"{MOD}.update_payment_status"),
        patch("stripe.checkout.Session.create", return_value=session) as create,
    ):
        svc.create_checkout_session(1, request)
    return create.call_args.kwargs, cp.call_args.kwargs


def test_client_metadata_cannot_override_server_fields(svc):
    kwargs, _ = _create_checkout(
        svc,
        _checkout_req(
            metadata={
                "credits_cents": "99999999",
                "credits": "99999999",
                "user_id": "2",
                "payment_id": "999",
                "note": "hello",
            }
        ),
    )
    md = kwargs["metadata"]
    assert md["credits_cents"] == "500"
    assert md["credits"] == "500"
    assert md["user_id"] == "1"
    assert md["payment_id"] == "42"
    assert md["note"] == "hello"  # harmless keys still pass through
    assert kwargs["payment_intent_data"]["metadata"]["credits_cents"] == "500"


def test_client_metadata_reserved_keys_not_stored_on_payment_record(svc):
    _, payment_kwargs = _create_checkout(
        svc, _checkout_req(metadata={"credits_cents": "99999999", "note": "x"})
    )
    assert "credits_cents" not in payment_kwargs["metadata"]
    assert payment_kwargs["metadata"]["note"] == "x"


# ===========================================================================
# 2. credit_value can no longer inflate credits
# ===========================================================================
def test_credit_value_3x_ignored_for_unlisted_amount(svc):
    kwargs, _ = _create_checkout(svc, _checkout_req(amount=500, credit_value=15))
    assert kwargs["metadata"]["credits_cents"] == "500"


def test_credit_value_honoured_only_for_server_side_package(svc):
    # $9 -> $10 is a real promo (frontend Starter tier); $45 -> $50 (backend pro pack)
    kwargs, _ = _create_checkout(svc, _checkout_req(amount=900, credit_value=10))
    assert kwargs["metadata"]["credits_cents"] == "1000"
    kwargs, _ = _create_checkout(svc, _checkout_req(amount=4500, credit_value=50))
    assert kwargs["metadata"]["credits_cents"] == "5000"
    # same package price but client asks for more than the package gives
    kwargs, _ = _create_checkout(svc, _checkout_req(amount=900, credit_value=27))
    assert kwargs["metadata"]["credits_cents"] == "1000"


def _completed_session(amount_total, credits_cents, subtotal=None):
    return {
        "id": "cs_1",
        "mode": "payment",
        "payment_intent": "pi_1",
        "amount_total": amount_total,
        "amount_subtotal": subtotal if subtotal is not None else amount_total,
        "metadata": {"user_id": "1", "payment_id": "42", "credits_cents": str(credits_cents)},
    }


def _run_completed(svc, session):
    with (
        patch(f"{MOD}.get_payment", return_value={"id": 42, "status": "pending"}),
        patch(f"{MOD}.update_payment_status"),
        patch(f"{MOD}.add_credits_to_user") as add,
        patch(f"{MOD}.capture_payment_error") as cap,
        patch("src.config.supabase_config.get_supabase_client", return_value=MagicMock()),
        patch("src.db.users.invalidate_user_cache_by_id"),
    ):
        svc._handle_checkout_completed(session)
    return add, cap


def test_webhook_caps_grant_to_amount_paid(svc):
    add, cap = _run_completed(svc, _completed_session(500, 99999999))
    purchase = [c for c in add.call_args_list if c.kwargs["transaction_type"] == "purchase"]
    assert len(purchase) == 1
    assert purchase[0].kwargs["credits"] == pytest.approx(5.0)
    assert cap.called  # mismatch flagged


def test_webhook_grants_package_bonus_when_paid_matches_package(svc):
    add, cap = _run_completed(svc, _completed_session(900, 1000))
    purchase = [c for c in add.call_args_list if c.kwargs["transaction_type"] == "purchase"]
    assert purchase[0].kwargs["credits"] == pytest.approx(10.0)
    assert not cap.called


def test_webhook_package_bonus_not_extendable_via_metadata(svc):
    add, _ = _run_completed(svc, _completed_session(900, 5000))
    purchase = [c for c in add.call_args_list if c.kwargs["transaction_type"] == "purchase"]
    assert purchase[0].kwargs["credits"] == pytest.approx(10.0)


def test_webhook_tax_inclusive_total_does_not_shortchange_package(svc):
    # total includes tax, subtotal is the package price
    add, _ = _run_completed(svc, _completed_session(972, 1000, subtotal=900))
    purchase = [c for c in add.call_args_list if c.kwargs["transaction_type"] == "purchase"]
    assert purchase[0].kwargs["credits"] == pytest.approx(10.0)


def test_verify_payment_amount_enforces_cap(svc):
    result = svc._verify_payment_amount(
        amount_cents=500, session_id="cs_1", user_id=1, claimed_credits_cents=99999999
    )
    assert result["allowed_credits_cents"] == 500
    assert result["credits_capped"] is True
    ok = svc._verify_payment_amount(amount_cents=500, claimed_credits_cents=500)
    assert ok["allowed_credits_cents"] == 500 and ok["credits_capped"] is False


# ===========================================================================
# 3. Subscription checkout: tier bound to the price actually purchased
# ===========================================================================
def _sub_req(**kw):
    base = {
        "price_id": "price_cheap",
        "product_id": "prod_max",
        "success_url": "https://x/s",
        "cancel_url": "https://x/c",
    }
    base.update(kw)
    return CreateSubscriptionCheckoutRequest(**base)


TIERS = {"prod_cheap": "pro", "prod_max": "max"}


def _sub_checkout(svc, request, price_product="prod_cheap"):
    price = {"id": request.price_id, "product": price_product, "active": True}
    session = SimpleNamespace(id="cs_s", url="https://stripe/s", status="open")
    with (
        patch(
            f"{MOD}.get_user_by_id",
            return_value={"id": 1, "email": "a@b.co", "stripe_customer_id": "cus_1"},
        ),
        patch(f"{MOD}.get_tier_from_product_id", side_effect=lambda p: TIERS.get(p, "basic")),
        patch("stripe.Price.retrieve", return_value=price),
        patch("stripe.checkout.Session.create", return_value=session) as create,
    ):
        svc.create_subscription_checkout(1, request)
    return create.call_args.kwargs


def test_subscription_checkout_rejects_price_product_mismatch(svc):
    with pytest.raises(ValueError):
        _sub_checkout(svc, _sub_req(product_id="prod_max"), price_product="prod_cheap")


def test_subscription_checkout_tier_comes_from_price_and_not_client_metadata(svc):
    kwargs = _sub_checkout(
        svc,
        _sub_req(product_id="prod_cheap", metadata={"tier": "max", "user_id": "9"}),
        price_product="prod_cheap",
    )
    assert kwargs["metadata"]["tier"] == "pro"
    assert kwargs["metadata"]["user_id"] == "1"
    assert kwargs["subscription_data"]["metadata"]["tier"] == "pro"


def test_subscription_checkout_rejects_unknown_price_product(svc):
    with pytest.raises(ValueError):
        _sub_checkout(svc, _sub_req(product_id="prod_x"), price_product="prod_x")


def test_webhook_tier_resolved_from_items_not_metadata(svc):
    sub = {
        "id": "sub_1",
        "status": "active",
        "items": {"data": [{"price": {"id": "price_cheap", "product": "prod_cheap"}}]},
    }
    with patch(f"{MOD}.get_tier_from_product_id", side_effect=lambda p: TIERS.get(p, "basic")):
        tier, _ = svc._resolve_tier_from_subscription(sub, "max")
    assert tier == "pro"


# ===========================================================================
# 4. Tier change: forced proration, price-bound tier, allowance delta once/period
# ===========================================================================
def _paid_sub(period_start=1_700_000_000, metadata=None):
    return {
        "id": "sub_1",
        "status": "active",
        "current_period_start": period_start,
        "metadata": metadata or {},
        "items": {"data": [{"id": "si_1", "price": {"id": "price_pro", "product": "prod_pro"}}]},
        "latest_invoice": {"id": "in_1", "status": "paid", "amount_due": 0},
    }


ALLOW = {"pro": 10.0, "max": 50.0}


def _apply(svc, sub, from_tier, to_tier, remaining):
    """Run the shared allowance helper; returns (target allowance or None, modify mock)."""
    with (
        patch(
            "src.db.subscription_products.get_allowance_from_tier",
            side_effect=lambda t: ALLOW.get(t, 0.0),
        ),
        patch(
            "src.db.users.get_user_by_id",
            return_value={"subscription_allowance": remaining, "purchased_credits": 0},
        ),
        patch("src.db.users.reset_subscription_allowance", return_value=True) as reset,
        patch("stripe.Subscription.modify") as modify,
    ):
        svc._apply_tier_change_allowance(1, sub, from_tier, to_tier)
    target = reset.call_args.args[1] if reset.called else None
    return target, modify


def test_upgrade_grants_only_delta_and_records_high_water_mark(svc):
    target, modify = _apply(svc, _paid_sub(), "pro", "max", remaining=3.0)
    assert target == pytest.approx(43.0)  # 3 remaining + (50 - 10) delta
    md = modify.call_args.kwargs["metadata"]
    assert md["allowance_hw"] == "50.0"


def test_ping_pong_does_not_refill_allowance(svc):
    # already upgraded this period (hw=50), user spent everything, downgrades then upgrades again
    sub = _paid_sub(metadata={"allowance_period_start": "1700000000", "allowance_hw": "50.0"})
    target, _ = _apply(svc, sub, "max", "pro", remaining=0.0)
    assert target is None or target == pytest.approx(0.0)
    target, _ = _apply(svc, sub, "pro", "max", remaining=0.0)
    assert target is None or target == pytest.approx(0.0)  # no second grant this period


def test_downgrade_never_refills_up_to_new_allowance(svc):
    target, _ = _apply(svc, _paid_sub(), "max", "pro", remaining=2.0)
    assert target is None or target <= 2.0


def test_new_billing_period_resets_high_water_mark(svc):
    sub = _paid_sub(
        period_start=1_800_000_000,
        metadata={"allowance_period_start": "1700000000", "allowance_hw": "50.0"},
    )
    target, _ = _apply(svc, sub, "pro", "max", remaining=10.0)
    assert target == pytest.approx(50.0)


def _upgrade(svc, request, price_product="prod_max", invoice_status="paid"):
    sub = _paid_sub()
    updated = dict(sub)
    updated["latest_invoice"] = {"id": "in_2", "status": invoice_status, "amount_due": 0}
    client = MagicMock()
    with (
        patch(
            f"{MOD}.get_user_by_id",
            return_value={"id": 1, "tier": "pro", "stripe_subscription_id": "sub_1"},
        ),
        patch(f"{MOD}.get_tier_from_product_id", side_effect=lambda p: TIERS.get(p, "basic")),
        patch(
            "stripe.Subscription.retrieve",
            return_value=SimpleNamespace(
                **{"status": "active", "items": SimpleNamespace(data=[SimpleNamespace(id="si_1")])}
            ),
        ),
        patch(
            "stripe.Price.retrieve",
            return_value={"id": request.new_price_id, "product": price_product, "active": True},
        ),
        patch(
            "stripe.Subscription.modify",
            return_value=SimpleNamespace(
                id="sub_1",
                status="active",
                latest_invoice=updated["latest_invoice"],
                current_period_start=1_700_000_000,
                metadata={},
            ),
        ) as modify,
        patch.object(svc, "_get_stripe_proration_amount", return_value=None),
        patch.object(svc, "_apply_tier_change_allowance") as apply_allow,
        patch("src.config.supabase_config.get_supabase_client", return_value=client),
        patch("src.db.plans.get_plan_id_by_tier", return_value=None),
        patch("src.db.users.invalidate_user_cache_by_id"),
    ):
        svc.upgrade_subscription(1, request)
    return modify, apply_allow


def test_upgrade_forces_always_invoice_and_error_if_incomplete(svc):
    req = UpgradeSubscriptionRequest(
        new_price_id="price_max", new_product_id="prod_max", proration_behavior="none"
    )
    modify, _ = _upgrade(svc, req)
    kwargs = modify.call_args.kwargs
    assert kwargs["proration_behavior"] == "always_invoice"
    assert kwargs["payment_behavior"] == "error_if_incomplete"


def test_upgrade_rejects_price_product_mismatch(svc):
    req = UpgradeSubscriptionRequest(new_price_id="price_cheap", new_product_id="prod_max")
    with pytest.raises(ValueError):
        _upgrade(svc, req, price_product="prod_cheap")


def test_upgrade_allowance_only_after_invoice_paid(svc):
    req = UpgradeSubscriptionRequest(new_price_id="price_max", new_product_id="prod_max")
    _, apply_allow = _upgrade(svc, req, invoice_status="open")
    assert not apply_allow.called
    _, apply_allow = _upgrade(svc, req, invoice_status="paid")
    assert apply_allow.called


# ===========================================================================
# 5. Refund / chargeback clawback
# ===========================================================================
def _charge(refunded=1000, refunds=None):
    return {
        "id": "ch_1",
        "payment_intent": "pi_1",
        "amount": 1000,
        "amount_refunded": refunded,
        "refunds": {
            "data": refunds if refunds is not None else [{"id": "re_1", "amount": refunded}]
        },
    }


def _purchased(db):
    return db.tables["users"][0]["purchased_credits"]


def _refund_rows(db):
    return [r for r in db.tables["credit_transactions"] if r["transaction_type"] == "refund"]


def test_charge_refunded_reverses_credits_once(fake_db, svc):
    svc._handle_charge_refunded(_charge())
    assert _purchased(fake_db) == pytest.approx(0.0)
    assert len(_refund_rows(fake_db)) == 1
    svc._handle_charge_refunded(_charge())  # duplicate delivery
    assert _purchased(fake_db) == pytest.approx(0.0)
    assert len(_refund_rows(fake_db)) == 1


def test_partial_refund_reverses_proportionally(fake_db, svc):
    svc._handle_charge_refunded(_charge(refunded=250, refunds=[{"id": "re_p", "amount": 250}]))
    assert _purchased(fake_db) == pytest.approx(7.5)


def test_refund_shortfall_is_recorded_not_negative(fake_db, svc):
    fake_db.tables["users"][0]["purchased_credits"] = 3.0  # user already spent most of it
    svc._handle_charge_refunded(_charge())
    assert _purchased(fake_db) == pytest.approx(0.0)
    row = _refund_rows(fake_db)[0]
    assert row["amount"] == pytest.approx(-3.0)
    assert row["metadata"]["clawback_shortfall"] == pytest.approx(7.0)
    assert fake_db.tables["payments"][0]["metadata"]["needs_review"] is True
    assert fake_db.capture.called


def test_dispute_created_reverses_and_closed_lost_is_idempotent(fake_db, svc):
    dispute = {
        "id": "dp_1",
        "charge": "ch_1",
        "payment_intent": "pi_1",
        "amount": 1000,
        "status": "needs_response",
    }
    svc._handle_dispute_created(dispute)
    assert _purchased(fake_db) == pytest.approx(0.0)
    svc._handle_dispute_closed({**dispute, "status": "lost"})
    assert _purchased(fake_db) == pytest.approx(0.0)
    assert len(_refund_rows(fake_db)) == 1


def test_dispute_closed_won_reinstates_credits(fake_db, svc):
    dispute = {
        "id": "dp_2",
        "charge": "ch_1",
        "payment_intent": "pi_1",
        "amount": 1000,
        "status": "needs_response",
    }
    svc._handle_dispute_created(dispute)
    with patch(f"{MOD}.add_credits_to_user") as add:
        svc._handle_dispute_closed({**dispute, "status": "won"})
    assert add.call_args.kwargs["credits"] == pytest.approx(10.0)


def test_webhook_dispatches_refund_and_dispute_events(svc):
    for etype, handler in (
        ("charge.refunded", "_handle_charge_refunded"),
        ("charge.dispute.created", "_handle_dispute_created"),
        ("charge.dispute.closed", "_handle_dispute_closed"),
    ):
        event = {"id": f"evt_{etype}", "type": etype, "data": {"object": {"id": "x"}}}
        with (
            patch("stripe.Webhook.construct_event", return_value=event),
            patch(f"{MOD}.claim_event", return_value="claimed"),
            patch.object(svc, handler) as h,
        ):
            svc.handle_webhook(b"{}", "sig")
        assert h.called, etype


def test_refund_route_reverses_credits(fake_db, svc):
    refund = SimpleNamespace(
        id="re_9",
        payment_intent="pi_1",
        amount=1000,
        currency="usd",
        status="succeeded",
        reason=None,
        created=1_700_000_000,
    )
    with patch("stripe.Refund.create", return_value=refund):
        svc.create_refund(CreateRefundRequest(payment_intent_id="pi_1"))
    assert _purchased(fake_db) == pytest.approx(0.0)
    # the later charge.refunded webhook for the same refund id must not double reverse
    svc._handle_charge_refunded(_charge(refunds=[{"id": "re_9", "amount": 1000}]))
    assert len(_refund_rows(fake_db)) == 1
