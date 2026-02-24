"""
Comprehensive payment processing tests - CRITICAL
Tests Stripe integration for checkout sessions, payment intents, webhooks
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, call, patch

import pytest
import stripe

from src.schemas.payments import (
    CreateCheckoutSessionRequest,
    CreatePaymentIntentRequest,
    CreateRefundRequest,
    StripeCurrency,
    StripePaymentMethodType,
)
from src.services.payments import StripeService


@pytest.fixture
def stripe_service():
    """Create StripeService instance"""
    with patch.dict(
        "os.environ",
        {
            "STRIPE_SECRET_KEY": "sk_test_123",
            "STRIPE_WEBHOOK_SECRET": "whsec_test_123",
            "STRIPE_PUBLISHABLE_KEY": "pk_test_123",
            "FRONTEND_URL": "https://test.gatewayz.ai",
        },
    ):
        return StripeService()


@pytest.fixture
def mock_user():
    """Mock user data"""
    return {"id": 1, "email": "test@example.com", "credits": 100.0, "subscription_status": "active"}


@pytest.fixture
def mock_payment():
    """Mock payment record"""
    return {
        "id": 1,
        "user_id": 1,
        "amount": 10.00,
        "currency": "usd",
        "status": "pending",
        "payment_method": "stripe",
    }


class TestStripeServiceInitialization:
    """Test StripeService initialization"""

    def test_init_success(self):
        """Test successful initialization"""
        with patch.dict(
            "os.environ",
            {
                "STRIPE_SECRET_KEY": "sk_test_123",
                "STRIPE_WEBHOOK_SECRET": "whsec_test_123",
                "STRIPE_PUBLISHABLE_KEY": "pk_test_123",
            },
        ):
            service = StripeService()
            assert service.api_key == "sk_test_123"
            assert service.webhook_secret == "whsec_test_123"
            assert service.min_amount == 50  # $0.50 minimum
            assert service.max_amount == 99999999

    def test_init_missing_api_key(self):
        """Test initialization fails without API key"""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="STRIPE_SECRET_KEY not found"):
                StripeService()


class TestCheckoutSession:
    """Test checkout session creation"""

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.services.payments.update_payment_status")
    def test_create_checkout_session_success(
        self,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test successful checkout session creation"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        # Mock Stripe session
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = None
        mock_stripe_create.return_value = mock_session

        # Create request
        request = CreateCheckoutSessionRequest(
            amount=1000,  # $10.00 in cents
            currency=StripeCurrency.USD,
            description="Test purchase",
            customer_email="test@example.com",
        )

        # Execute
        response = stripe_service.create_checkout_session(user_id=1, request=request)

        # Verify
        assert response.session_id == "cs_test_123"
        assert response.url == "https://checkout.stripe.com/pay/cs_test_123"
        assert response.payment_id == 1
        assert response.amount == 1000

        # Verify Stripe was called correctly
        mock_stripe_create.assert_called_once()
        call_kwargs = mock_stripe_create.call_args[1]
        assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 1000
        assert call_kwargs["customer_email"] == "test@example.com"
        assert call_kwargs["metadata"]["user_id"] == "1"
        assert call_kwargs["metadata"]["credits"] == "1000"
        assert "payment_intent_data" in call_kwargs
        assert call_kwargs["payment_intent_data"]["metadata"] == call_kwargs["metadata"]

        expected_update_kwargs = {
            "payment_id": 1,
            "status": "pending",
            "stripe_session_id": "cs_test_123",
        }
        if mock_session.payment_intent:
            expected_update_kwargs["stripe_payment_intent_id"] = mock_session.payment_intent
        mock_update_payment.assert_called_once_with(**expected_update_kwargs)

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.services.payments.update_payment_status")
    def test_create_checkout_session_with_discounted_credit_value(
        self,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test checkout session with discounted credit_value (e.g., $9 for $10 credits)"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        # Mock Stripe session
        mock_session = Mock()
        mock_session.id = "cs_test_discount"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_discount"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = None
        mock_stripe_create.return_value = mock_session

        # Create request with discounted pricing:
        # User pays $9 (900 cents) but gets $10 worth of credits
        request = CreateCheckoutSessionRequest(
            amount=900,  # $9.00 in cents (payment amount)
            credit_value=10.0,  # $10 worth of credits
            currency=StripeCurrency.USD,
            description="Discounted credits purchase",
            customer_email="test@example.com",
        )

        # Execute
        response = stripe_service.create_checkout_session(user_id=1, request=request)

        # Verify response
        assert response.session_id == "cs_test_discount"
        assert response.amount == 900  # Payment amount in cents

        # Verify Stripe was called with correct values
        mock_stripe_create.assert_called_once()
        call_kwargs = mock_stripe_create.call_args[1]

        # Payment amount should be $9 (900 cents)
        assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 900

        # But credits_cents metadata should be $10 (1000 cents)
        assert call_kwargs["metadata"]["credits_cents"] == "1000"
        assert call_kwargs["metadata"]["credits"] == "1000"

        # Description should show the credit value
        assert "$10" in call_kwargs["line_items"][0]["price_data"]["product_data"]["description"]

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.services.payments.update_payment_status")
    def test_create_checkout_session_large_discounted_package(
        self,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test checkout session with larger discounted package ($75 for $100 credits)"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        mock_session = Mock()
        mock_session.id = "cs_test_large_discount"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_large_discount"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = None
        mock_stripe_create.return_value = mock_session

        # User pays $75 but gets $100 worth of credits (33% bonus)
        request = CreateCheckoutSessionRequest(
            amount=7500,  # $75.00 in cents
            credit_value=100.0,  # $100 worth of credits
            currency=StripeCurrency.USD,
        )

        # Execute and verify response
        response = stripe_service.create_checkout_session(user_id=1, request=request)
        assert response.session_id == "cs_test_large_discount"
        assert response.amount == 7500

        call_kwargs = mock_stripe_create.call_args[1]
        assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 7500
        assert call_kwargs["metadata"]["credits_cents"] == "10000"  # $100 in cents

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.services.payments.update_payment_status")
    def test_create_checkout_session_persists_payment_intent(
        self,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Ensure checkout session stores payment_intent when provided by Stripe."""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        mock_session = Mock()
        mock_session.id = "cs_test_456"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_456"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = "pi_cs_test_456"
        mock_stripe_create.return_value = mock_session

        request = CreateCheckoutSessionRequest(
            amount=2000,
            currency=StripeCurrency.USD,
            description="Another purchase",
            customer_email="intent@example.com",
        )

        stripe_service.create_checkout_session(user_id=1, request=request)

        mock_update_payment.assert_called_once_with(
            payment_id=1,
            status="pending",
            stripe_session_id="cs_test_456",
            stripe_payment_intent_id="pi_cs_test_456",
        )

    @patch("src.services.payments.get_user_by_id")
    def test_create_checkout_session_user_not_found(self, mock_get_user, stripe_service):
        """Test checkout session fails for non-existent user"""

        mock_get_user.return_value = None

        request = CreateCheckoutSessionRequest(amount=1000, currency=StripeCurrency.USD)

        with pytest.raises(ValueError, match="User .* not found"):
            stripe_service.create_checkout_session(user_id=999, request=request)

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    def test_create_checkout_session_stripe_error(
        self,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test checkout session handles Stripe errors"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment
        mock_stripe_create.side_effect = stripe.StripeError("Card declined")

        request = CreateCheckoutSessionRequest(amount=1000, currency=StripeCurrency.USD)

        with pytest.raises(Exception, match="Payment processing error"):
            stripe_service.create_checkout_session(user_id=1, request=request)

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_create_checkout_session_with_privy_did(
        self,
        mock_get_supabase_client,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_payment,
    ):
        """Test checkout session handles Privy DID emails"""

        # User with Privy DID as email
        privy_user = {"id": 1, "email": "did:privy:abc123", "credits": 100.0}

        # Mock Supabase client for Privy DID lookup
        mock_supabase_client = Mock()
        mock_supabase_client.table().select().eq().execute.return_value = Mock(
            data=[{"privy_user_id": "privy_123"}]
        )
        mock_get_supabase_client.return_value = mock_supabase_client

        mock_get_user.return_value = privy_user
        mock_create_payment.return_value = mock_payment

        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = None
        mock_stripe_create.return_value = mock_session

        request = CreateCheckoutSessionRequest(
            amount=1000,
            currency=StripeCurrency.USD,
            customer_email="real@example.com",  # Real email provided
        )

        response = stripe_service.create_checkout_session(user_id=1, request=request)

        assert response.session_id == "cs_test_123"
        # Should use provided customer_email
        call_kwargs = mock_stripe_create.call_args[1]
        assert call_kwargs["customer_email"] == "real@example.com"


class TestPaymentIntents:
    """Test payment intent creation"""

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.PaymentIntent.create")
    @patch("src.services.payments.update_payment_status")
    def test_create_payment_intent_success(
        self,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test successful payment intent creation"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        # Mock Stripe payment intent
        mock_intent = Mock()
        mock_intent.id = "pi_test_123"
        mock_intent.client_secret = "pi_test_123_secret_abc"
        mock_intent.status = "requires_payment_method"
        mock_intent.amount = 1000
        mock_intent.currency = "usd"
        mock_intent.next_action = None
        mock_stripe_create.return_value = mock_intent

        request = CreatePaymentIntentRequest(
            amount=1000,
            currency=StripeCurrency.USD,
            description="Test payment",
            automatic_payment_methods=True,
        )

        response = stripe_service.create_payment_intent(user_id=1, request=request)

        assert response.payment_intent_id == "pi_test_123"
        assert response.client_secret == "pi_test_123_secret_abc"
        assert response.amount == 1000

        # Verify Stripe was called correctly
        call_kwargs = mock_stripe_create.call_args[1]
        assert call_kwargs["amount"] == 1000
        assert call_kwargs["currency"] == "usd"
        assert call_kwargs["automatic_payment_methods"] == {"enabled": True}
        assert call_kwargs["metadata"]["user_id"] == "1"

    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.PaymentIntent.create")
    def test_create_payment_intent_with_specific_payment_methods(
        self,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test payment intent with specific payment methods"""

        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        mock_intent = Mock()
        mock_intent.id = "pi_test_123"
        mock_intent.client_secret = "pi_test_123_secret"
        mock_intent.status = "requires_payment_method"
        mock_intent.amount = 1000
        mock_intent.currency = "usd"
        mock_intent.next_action = None
        mock_stripe_create.return_value = mock_intent

        request = CreatePaymentIntentRequest(
            amount=1000,
            currency=StripeCurrency.USD,
            payment_method_types=[StripePaymentMethodType.CARD],
            automatic_payment_methods=False,
        )

        response = stripe_service.create_payment_intent(user_id=1, request=request)

        # Verify payment_method_types was used
        call_kwargs = mock_stripe_create.call_args[1]
        assert "payment_method_types" in call_kwargs
        assert call_kwargs["payment_method_types"] == ["card"]


class TestWebhooks:
    """Test webhook processing"""

    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("stripe.Webhook.construct_event")
    @patch.object(StripeService, "_handle_checkout_completed")
    def test_handle_checkout_completed_webhook(
        self,
        mock_handle_checkout,
        mock_construct_event,
        mock_is_processed,
        mock_record_event,
        stripe_service,
    ):
        """Test checkout.session.completed webhook"""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        mock_event = {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_123"}},
        }
        mock_construct_event.return_value = mock_event

        payload = b"test_payload"
        signature = "test_signature"

        result = stripe_service.handle_webhook(payload, signature)

        assert result.success is True
        assert result.event_type == "checkout.session.completed"
        assert result.event_id == "evt_test_123"
        mock_handle_checkout.assert_called_once_with({"id": "cs_test_123"})

    @patch("stripe.Webhook.construct_event")
    def test_handle_webhook_invalid_signature(self, mock_construct_event, stripe_service):
        """Test webhook with invalid signature"""

        mock_construct_event.side_effect = ValueError("Invalid signature")

        with pytest.raises(ValueError, match="Invalid signature"):
            stripe_service.handle_webhook(b"payload", "bad_signature")

    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("stripe.Webhook.construct_event")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_adds_credits(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_construct_event,
        mock_is_processed,
        mock_record_event,
        stripe_service,
    ):
        """Test checkout completed webhook adds credits to user"""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        # Create a Mock object instead of dict to support attribute access
        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.payment_intent = "pi_test_123"
        mock_session.metadata = {
            "user_id": "1",
            "credits": "1000",  # 1000 cents = $10
            "payment_id": "1",
        }

        mock_event = {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {"object": mock_session},
        }
        mock_construct_event.return_value = mock_event

        result = stripe_service.handle_webhook(b"payload", "signature")

        # Verify credits were added (1000 cents / 100 = $10)
        mock_add_credits.assert_called_once()
        call_args = mock_add_credits.call_args[1]
        assert call_args["user_id"] == 1
        assert call_args["credits"] == 10.0  # $10
        assert call_args["transaction_type"] == "purchase"

        # Verify payment updated
        mock_update_payment.assert_called_once_with(
            payment_id=1,
            status="completed",
            stripe_payment_intent_id="pi_test_123",
            stripe_session_id="cs_test_123",
        )

    @patch("stripe.checkout.Session.retrieve")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_refetches_metadata_when_missing(
        self, mock_update_payment, mock_add_credits, mock_session_retrieve, stripe_service
    ):
        """Ensure checkout handler refetches the session when metadata is missing"""

        # Partial session payload received from Stripe webhook (missing metadata)
        partial_session = Mock()
        partial_session.id = "cs_missing_meta"
        partial_session.payment_intent = None
        partial_session.metadata = None

        # Full session returned by retrieve()
        full_session = Mock()
        full_session.id = "cs_missing_meta"
        full_session.payment_intent = "pi_full"
        full_session.metadata = {"user_id": "1", "credits": "2500", "payment_id": "42"}
        mock_session_retrieve.return_value = full_session

        stripe_service._handle_checkout_completed(partial_session)

        mock_session_retrieve.assert_called_once_with("cs_missing_meta", expand=["metadata"])
        mock_add_credits.assert_called_once()
        assert mock_add_credits.call_args[1]["credits"] == 25.0
        mock_update_payment.assert_called_once_with(
            payment_id=42,
            status="completed",
            stripe_payment_intent_id="pi_full",
            stripe_session_id="cs_missing_meta",
        )

    @patch("stripe.PaymentIntent.retrieve")
    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_recovers_metadata_from_payment_intent(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        mock_intent_retrieve,
        stripe_service,
    ):
        """Verify fallback metadata retrieval pulls from the related payment intent."""

        session = Mock()
        session.id = "cs_pi_meta"
        session.payment_intent = "pi_meta_only"
        session.metadata = None
        session.client_reference_id = None
        session.amount_total = 3000
        session.amount_subtotal = None

        mock_get_payment.side_effect = [None, None]

        mock_intent = Mock()
        mock_intent.metadata = {
            "user_id": "77",
            "payment_id": "555",
            "credits_cents": "3000",
            "credits": "3000",
        }
        mock_intent_retrieve.return_value = mock_intent

        stripe_service._handle_checkout_completed(session)

        mock_intent_retrieve.assert_called_once_with("pi_meta_only", expand=["metadata"])
        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["user_id"] == 77
        assert add_kwargs["payment_id"] == 555
        assert add_kwargs["credits"] == 30.0

        mock_update_payment.assert_called_once_with(
            payment_id=555,
            status="completed",
            stripe_payment_intent_id="pi_meta_only",
            stripe_session_id="cs_pi_meta",
        )

    @patch("stripe.PaymentIntent.retrieve")
    @patch("stripe.checkout.Session.retrieve")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_hydrates_metadata_from_payment_intent(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_session_retrieve,
        mock_payment_intent_retrieve,
        stripe_service,
    ):
        """Ensure metadata can be recovered from the PaymentIntent when absent on the session."""

        webhook_session = {
            "id": "cs_missing_everything",
            "payment_intent": "pi_metadata_source",
            "metadata": None,
            "client_reference_id": "9",
        }

        refreshed_session = {
            "id": "cs_missing_everything",
            "payment_intent": "pi_metadata_source",
            "metadata": None,
        }
        mock_session_retrieve.return_value = refreshed_session

        intent = Mock()
        intent.metadata = {
            "user_id": "9",
            "payment_id": "321",
            "credits_cents": "4200",
            "credits": "4200",
        }
        mock_payment_intent_retrieve.return_value = intent

        stripe_service._handle_checkout_completed(webhook_session)

        mock_session_retrieve.assert_called_once_with("cs_missing_everything", expand=["metadata"])
        mock_payment_intent_retrieve.assert_called_once_with(
            "pi_metadata_source", expand=["metadata"]
        )

        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["user_id"] == 9
        assert add_kwargs["payment_id"] == 321
        assert add_kwargs["credits"] == 42.0

        mock_update_payment.assert_called_once_with(
            payment_id=321,
            status="completed",
            stripe_payment_intent_id="pi_metadata_source",
            stripe_session_id="cs_missing_everything",
        )

    def test_checkout_completed_raises_when_metadata_and_id_missing(self, stripe_service):
        """Ensure handler fails fast when both metadata and session id are missing"""
        session_without_data = Mock()
        session_without_data.id = None
        session_without_data.metadata = None

        with pytest.raises(ValueError, match="missing metadata and session id"):
            stripe_service._handle_checkout_completed(session_without_data)

    @patch("stripe.Webhook.construct_event")
    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_recovers_missing_metadata(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        mock_is_processed,
        mock_record_event,
        mock_construct_event,
        stripe_service,
    ):
        """Ensure webhook handler falls back to Supabase when metadata is absent."""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        missing_metadata_session = {
            "id": "cs_missing_meta",
            "payment_intent": "pi_missing_meta",
            "metadata": None,
            "client_reference_id": "1",
            "amount_total": 2500,
        }

        def _lookup(identifier):
            if identifier in {"pi_missing_meta", "cs_missing_meta"}:
                return {
                    "id": 42,
                    "user_id": 1,
                    "credits_purchased": 2500,
                }
            return None

        mock_get_payment.side_effect = _lookup

        mock_event = {
            "id": "evt_missing_meta",
            "type": "checkout.session.completed",
            "data": {"object": missing_metadata_session},
        }
        mock_construct_event.return_value = mock_event

        result = stripe_service.handle_webhook(b"payload", "signature")

        assert result.success is True
        mock_add_credits.assert_called_once()
        add_call = mock_add_credits.call_args[1]
        assert add_call["user_id"] == 1
        assert add_call["payment_id"] == 42
        assert add_call["credits"] == 25.0  # 2500 cents → $25

        mock_update_payment.assert_called_once_with(
            payment_id=42,
            status="completed",
            stripe_payment_intent_id="pi_missing_meta",
            stripe_session_id="cs_missing_meta",
        )

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_recovers_via_session_id_when_intent_lookup_fails(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        stripe_service,
    ):
        """Ensure fallback lookup retries with the checkout session ID."""

        session = Mock()
        session.id = "cs_lookup_only"
        session.payment_intent = "pi_lookup_missing"
        session.metadata = {
            "user_id": "7",
            "credits": "5000",
        }
        session.client_reference_id = "7"
        session.amount_total = None

        mock_get_payment.side_effect = [
            None,
            {"id": 99, "user_id": 7, "credits_purchased": 5000},
        ]

        stripe_service._handle_checkout_completed(session)

        assert mock_get_payment.call_args_list == [
            call("pi_lookup_missing"),
            call("cs_lookup_only"),
        ]

        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["user_id"] == 7
        assert add_kwargs["payment_id"] == 99
        assert add_kwargs["credits"] == 50.0

        mock_update_payment.assert_called_once_with(
            payment_id=99,
            status="completed",
            stripe_payment_intent_id="pi_lookup_missing",
            stripe_session_id="cs_lookup_only",
        )

    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("stripe.Webhook.construct_event")
    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.update_payment_status")
    @patch("src.services.payments.add_credits_to_user")
    def test_payment_intent_succeeded_webhook(
        self,
        mock_add_credits,
        mock_update_payment,
        mock_get_payment,
        mock_construct_event,
        mock_is_processed,
        mock_record_event,
        stripe_service,
    ):
        """Test payment_intent.succeeded webhook"""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        mock_get_payment.return_value = {"id": 1, "user_id": 1, "amount": 10.0, "status": "pending"}

        # Create a Mock object instead of dict to support attribute access
        mock_payment_intent = Mock()
        mock_payment_intent.id = "pi_test_123"

        mock_event = {
            "id": "evt_test_123",
            "type": "payment_intent.succeeded",
            "data": {"object": mock_payment_intent},
        }
        mock_construct_event.return_value = mock_event

        result = stripe_service.handle_webhook(b"payload", "signature")

        assert result.success is True
        mock_update_payment.assert_called_once_with(payment_id=1, status="completed")
        mock_add_credits.assert_called_once()

    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("stripe.Webhook.construct_event")
    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.update_payment_status")
    def test_payment_intent_failed_webhook(
        self,
        mock_update_payment,
        mock_get_payment,
        mock_construct_event,
        mock_is_processed,
        mock_record_event,
        stripe_service,
    ):
        """Test payment_intent.payment_failed webhook"""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        mock_get_payment.return_value = {"id": 1, "user_id": 1, "amount": 10.0, "status": "pending"}

        # Create a Mock object instead of dict to support attribute access
        mock_payment_intent = Mock()
        mock_payment_intent.id = "pi_test_123"

        mock_event = {
            "id": "evt_test_123",
            "type": "payment_intent.payment_failed",
            "data": {"object": mock_payment_intent},
        }
        mock_construct_event.return_value = mock_event

        result = stripe_service.handle_webhook(b"payload", "signature")

        assert result.success is True
        mock_update_payment.assert_called_once_with(payment_id=1, status="failed")


class TestCreditPackages:
    """Test credit package listings"""

    def test_get_credit_packages(self, stripe_service):
        """Test get credit packages"""

        response = stripe_service.get_credit_packages()

        assert len(response.packages) >= 2
        assert response.currency == StripeCurrency.USD

        # Check starter pack
        starter = next((p for p in response.packages if p.id == "starter"), None)
        assert starter is not None
        assert starter.credits == 1000
        assert starter.amount == 1000

        # Check professional pack
        pro = next((p for p in response.packages if p.id == "professional"), None)
        assert pro is not None
        assert pro.credits == 5000
        assert pro.amount == 4500
        assert pro.discount_percentage == 10.0
        assert pro.popular is True


class TestRefunds:
    """Test refund processing"""

    @patch("stripe.Refund.create")
    def test_create_refund_success(self, mock_stripe_refund, stripe_service):
        """Test successful refund creation"""

        mock_refund = Mock()
        mock_refund.id = "re_test_123"
        mock_refund.payment_intent = "pi_test_123"
        mock_refund.amount = 1000
        mock_refund.currency = "usd"
        mock_refund.status = "succeeded"
        mock_refund.reason = "requested_by_customer"
        mock_refund.created = int(datetime.now(UTC).timestamp())
        mock_stripe_refund.return_value = mock_refund

        request = CreateRefundRequest(
            payment_intent_id="pi_test_123", amount=1000, reason="requested_by_customer"
        )

        response = stripe_service.create_refund(request)

        assert response.refund_id == "re_test_123"
        assert response.payment_intent_id == "pi_test_123"
        assert response.amount == 1000
        assert response.status == "succeeded"

    @patch("stripe.Refund.create")
    def test_create_refund_stripe_error(self, mock_stripe_refund, stripe_service):
        """Test refund handles Stripe errors"""

        mock_stripe_refund.side_effect = stripe.StripeError("Refund failed")

        request = CreateRefundRequest(
            payment_intent_id="pi_test_123", amount=1000, reason="requested_by_customer"
        )

        with pytest.raises(Exception, match="Refund failed"):
            stripe_service.create_refund(request)


class TestSessionRetrieval:
    """Test session and intent retrieval"""

    @patch("stripe.checkout.Session.retrieve")
    def test_retrieve_checkout_session(self, mock_retrieve, stripe_service):
        """Test retrieve checkout session"""

        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.payment_status = "paid"
        mock_session.status = "complete"
        mock_session.amount_total = 1000
        mock_session.currency = "usd"
        mock_session.customer_email = "test@example.com"
        mock_session.payment_intent = "pi_test_123"
        mock_session.metadata = {"user_id": "1"}
        mock_retrieve.return_value = mock_session

        result = stripe_service.retrieve_checkout_session("cs_test_123")

        assert result["id"] == "cs_test_123"
        assert result["payment_status"] == "paid"
        assert result["amount_total"] == 1000

    @patch("stripe.PaymentIntent.retrieve")
    def test_retrieve_payment_intent(self, mock_retrieve, stripe_service):
        """Test retrieve payment intent"""

        mock_intent = Mock()
        mock_intent.id = "pi_test_123"
        mock_intent.status = "succeeded"
        mock_intent.amount = 1000
        mock_intent.currency = "usd"
        mock_intent.customer = "cus_test_123"
        mock_intent.payment_method = "pm_test_123"
        mock_intent.metadata = {"user_id": "1"}
        mock_retrieve.return_value = mock_intent

        result = stripe_service.retrieve_payment_intent("pi_test_123")

        assert result["id"] == "pi_test_123"
        assert result["status"] == "succeeded"
        assert result["amount"] == 1000


@pytest.mark.integration
class TestPaymentIntegration:
    """Integration tests for complete payment flows"""

    @patch("src.services.payments.record_processed_event")
    @patch("src.services.payments.is_event_processed")
    @patch("src.services.payments.get_user_by_id")
    @patch("src.services.payments.create_payment")
    @patch("stripe.checkout.Session.create")
    @patch("src.services.payments.update_payment_status")
    @patch("stripe.Webhook.construct_event")
    @patch("src.services.payments.add_credits_to_user")
    def test_complete_payment_flow(
        self,
        mock_add_credits,
        mock_construct_event,
        mock_update_payment,
        mock_stripe_create,
        mock_create_payment,
        mock_get_user,
        mock_is_processed,
        mock_record_event,
        stripe_service,
        mock_user,
        mock_payment,
    ):
        """Test complete payment flow: create session → webhook → credits added"""

        mock_is_processed.return_value = False
        mock_record_event.return_value = True

        # Step 1: Create checkout session
        mock_get_user.return_value = mock_user
        mock_create_payment.return_value = mock_payment

        mock_session = Mock()
        mock_session.id = "cs_test_123"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_session.expires_at = int((datetime.now(UTC) + timedelta(hours=24)).timestamp())
        mock_session.payment_intent = None
        mock_stripe_create.return_value = mock_session

        request = CreateCheckoutSessionRequest(
            amount=1000, currency=StripeCurrency.USD, customer_email="test@example.com"
        )

        session_response = stripe_service.create_checkout_session(user_id=1, request=request)
        assert session_response.session_id == "cs_test_123"

        # Step 2: Process webhook (customer completed payment)
        # Create a Mock object instead of dict to support attribute access
        mock_webhook_session = Mock()
        mock_webhook_session.id = "cs_test_123"
        mock_webhook_session.payment_intent = "pi_test_123"
        mock_webhook_session.metadata = {"user_id": "1", "credits": "1000", "payment_id": "1"}

        mock_event = {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {"object": mock_webhook_session},
        }
        mock_construct_event.return_value = mock_event

        webhook_result = stripe_service.handle_webhook(b"payload", "signature")

        # Verify credits were added
        assert webhook_result.success is True
        mock_add_credits.assert_called_once()
        assert mock_add_credits.call_args[1]["credits"] == 10.0  # $10

        assert mock_update_payment.call_count == 2
        pending_call = mock_update_payment.call_args_list[0].kwargs
        assert pending_call == {
            "payment_id": 1,
            "status": "pending",
            "stripe_session_id": "cs_test_123",
        }
        completion_call = mock_update_payment.call_args_list[1].kwargs
        assert completion_call == {
            "payment_id": 1,
            "status": "completed",
            "stripe_payment_intent_id": "pi_test_123",
            "stripe_session_id": "cs_test_123",
        }

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_missing_credits_uses_amount_total(
        self, mock_update_payment, mock_add_credits, mock_get_payment, stripe_service
    ):
        """Ensure handler falls back to session amount when credits metadata is missing"""

        # Return None from payment lookup so we use amount_total fallback
        mock_get_payment.return_value = None

        session = Mock()
        session.metadata = {"user_id": "1", "payment_id": "1", "credits": None}
        session.amount_total = 2500  # cents
        session.amount_subtotal = None
        session.id = "cs_test_fallback"
        session.payment_intent = "pi_test_fallback"

        stripe_service._handle_checkout_completed(session)

        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["credits"] == 25.0  # 2500 cents → $25
        mock_update_payment.assert_called_once_with(
            payment_id=1,
            status="completed",
            stripe_payment_intent_id="pi_test_fallback",
            stripe_session_id="cs_test_fallback",
        )

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_missing_ids_uses_payment_lookup(
        self, mock_update_payment, mock_add_credits, mock_get_payment, stripe_service
    ):
        """Ensure handler can recover user/payment IDs from Supabase when metadata is incomplete"""

        session = Mock()
        session.metadata = {"credits": "1500"}
        session.id = "cs_missing_ids"
        session.payment_intent = "pi_missing_ids"
        session.amount_total = None

        mock_get_payment.return_value = {"id": 42, "user_id": 7, "amount_usd": 15.0}

        stripe_service._handle_checkout_completed(session)

        mock_get_payment.assert_called_once_with("pi_missing_ids")
        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["user_id"] == 7
        assert add_kwargs["credits"] == 15.0
        mock_update_payment.assert_called_once_with(
            payment_id=42,
            status="completed",
            stripe_payment_intent_id="pi_missing_ids",
            stripe_session_id="cs_missing_ids",
        )

    @patch("src.services.payments.create_payment")
    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    def test_checkout_completed_creates_fallback_payment_when_missing_metadata(
        self,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        mock_create_payment,
        stripe_service,
    ):
        """Ensure handler creates a fallback payment record when payment_id cannot be recovered."""

        session = Mock()
        session.metadata = {
            "user_id": "7",
            "credits": "5000",
        }
        session.id = "cs_missing_payment"
        session.payment_intent = "pi_missing_payment"
        session.currency = "usd"
        session.amount_total = None
        session.amount_subtotal = None

        mock_get_payment.return_value = None
        mock_create_payment.return_value = {
            "id": 555,
            "user_id": 7,
            "amount_usd": 50.0,
        }

        stripe_service._handle_checkout_completed(session)

        mock_create_payment.assert_called_once()
        create_kwargs = mock_create_payment.call_args.kwargs
        assert create_kwargs["user_id"] == 7
        assert create_kwargs["amount"] == 50.0
        assert create_kwargs["stripe_session_id"] == "cs_missing_payment"
        assert create_kwargs["metadata"]["created_via"] == "stripe_webhook_fallback"

        mock_add_credits.assert_called_once()
        add_kwargs = mock_add_credits.call_args[1]
        assert add_kwargs["payment_id"] == 555
        assert add_kwargs["user_id"] == 7
        assert add_kwargs["credits"] == 50.0
        mock_update_payment.assert_called_once_with(
            payment_id=555,
            status="completed",
            stripe_payment_intent_id="pi_missing_payment",
            stripe_session_id="cs_missing_payment",
        )


class TestCheckoutCompletedSubscriptionStatus:
    """Test that checkout completed sets subscription_status correctly"""

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_checkout_completed_sets_inactive_status_for_trial_user(
        self,
        mock_get_supabase_client,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        stripe_service,
    ):
        """Test that checkout completed sets subscription_status to 'inactive' for trial users.

        This test verifies the fix for the bug where credit purchases were setting
        subscription_status to 'active', causing a mismatch with tier='basic'.

        Credit purchasers should have:
        - subscription_status: 'inactive' (not 'active' which implies a Pro/Max subscription)
        - tier: 'basic' (pay-per-use, not subscribed)
        """

        mock_get_payment.return_value = None

        # Mock session
        session = Mock()
        session.metadata = {
            "user_id": "1",
            "payment_id": "1",
            "credits": "1000",
        }
        session.id = "cs_test_trial_user"
        session.payment_intent = "pi_test_trial_user"
        session.amount_total = 1000
        session.amount_subtotal = None
        session.currency = "usd"

        # Mock Supabase client
        mock_client = Mock()
        mock_get_supabase_client.return_value = mock_client

        # User is on trial with basic tier
        mock_client.table().select().eq().execute.return_value = Mock(
            data=[{"subscription_status": "trial", "tier": "basic"}]
        )
        mock_client.table().update().eq().execute.return_value = Mock(data=[{}])

        stripe_service._handle_checkout_completed(session)

        # Verify that subscription_status is updated to 'inactive', NOT 'active'
        # The log confirms: "User 1 subscription_status updated to 'inactive' after credit purchase"
        # Find the update call that contains subscription_status
        update_calls = mock_client.table.return_value.update.call_args_list
        found_inactive_update = False
        for call in update_calls:  # noqa: F402
            # call is either call((arg,), {}) or call(key=value)
            if call.args:
                update_data = call.args[0]
            elif call.kwargs:
                update_data = call.kwargs
            else:
                continue
            if (
                isinstance(update_data, dict)
                and update_data.get("subscription_status") == "inactive"
            ):
                found_inactive_update = True
                break
        assert (
            found_inactive_update
        ), f"Expected subscription_status='inactive' update, but got calls: {update_calls}"

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_checkout_completed_preserves_active_subscription_status(
        self,
        mock_get_supabase_client,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        stripe_service,
    ):
        """Test that checkout completed does NOT change subscription_status for users with active subscriptions.

        Users with Pro/Max subscriptions who purchase additional credits should
        keep their 'active' subscription_status and their pro/max tier.
        """

        mock_get_payment.return_value = None

        # Mock session
        session = Mock()
        session.metadata = {
            "user_id": "2",
            "payment_id": "2",
            "credits": "2000",
        }
        session.id = "cs_test_pro_user"
        session.payment_intent = "pi_test_pro_user"
        session.amount_total = 2000
        session.amount_subtotal = None
        session.currency = "usd"

        # Mock Supabase client
        mock_client = Mock()
        mock_get_supabase_client.return_value = mock_client

        # User already has active subscription with pro tier
        mock_client.table().select().eq().execute.return_value = Mock(
            data=[{"subscription_status": "active", "tier": "pro"}]
        )
        mock_client.table().update().eq().execute.return_value = Mock(data=[{}])

        stripe_service._handle_checkout_completed(session)

        # Verify that NO update contains subscription_status for Pro/Max users
        # Both users table and api_keys_new should preserve the 'active' status
        for call in mock_client.table.return_value.update.call_args_list:  # noqa: F402
            update_data = call[0][0] if call[0] else {}
            if isinstance(update_data, dict) and "subscription_status" in update_data:
                raise AssertionError(
                    f"Should not update subscription_status for pro users, but got: {update_data}"
                )

        # Verify that api_keys_new WAS updated with is_trial=False and trial_converted=True
        # but NOT with subscription_status
        found_api_key_update = False
        for call in mock_client.table.return_value.update.call_args_list:
            update_data = call[0][0] if call[0] else {}
            if isinstance(update_data, dict):
                if (
                    update_data.get("is_trial") is False
                    and update_data.get("trial_converted") is True
                ):
                    found_api_key_update = True
                    # Ensure subscription_status is NOT in this update
                    assert (
                        "subscription_status" not in update_data
                    ), f"api_keys_new should not have subscription_status for pro users, but got: {update_data}"
        assert (
            found_api_key_update
        ), "Expected api_keys_new to be updated with is_trial=False and trial_converted=True"

    @patch("src.services.payments.get_payment_by_stripe_intent")
    @patch("src.services.payments.add_credits_to_user")
    @patch("src.services.payments.update_payment_status")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_checkout_completed_sets_inactive_for_expired_trial_user(
        self,
        mock_get_supabase_client,
        mock_update_payment,
        mock_add_credits,
        mock_get_payment,
        stripe_service,
    ):
        """Test that checkout completed sets subscription_status to 'inactive' for expired trial users.

        Users with expired trials who purchase credits should transition to 'inactive'
        (not 'active' which would incorrectly indicate a subscription).
        """

        mock_get_payment.return_value = None

        # Mock session
        session = Mock()
        session.metadata = {
            "user_id": "3",
            "payment_id": "3",
            "credits": "500",
        }
        session.id = "cs_test_expired_user"
        session.payment_intent = "pi_test_expired_user"
        session.amount_total = 500
        session.amount_subtotal = None
        session.currency = "usd"

        # Mock Supabase client
        mock_client = Mock()
        mock_get_supabase_client.return_value = mock_client

        # User has expired trial
        mock_client.table().select().eq().execute.return_value = Mock(
            data=[{"subscription_status": "expired", "tier": "basic"}]
        )
        mock_client.table().update().eq().execute.return_value = Mock(data=[{}])

        stripe_service._handle_checkout_completed(session)

        # Verify that subscription_status is updated to 'inactive'
        # The log confirms: "User 3 subscription_status updated to 'inactive' after credit purchase"
        update_calls = mock_client.table.return_value.update.call_args_list
        found_inactive_update = False
        for call in update_calls:  # noqa: F402
            if call.args:
                update_data = call.args[0]
            elif call.kwargs:
                update_data = call.kwargs
            else:
                continue
            if (
                isinstance(update_data, dict)
                and update_data.get("subscription_status") == "inactive"
            ):
                found_inactive_update = True
                break
        assert (
            found_inactive_update
        ), f"Expected subscription_status='inactive' update, but got calls: {update_calls}"
