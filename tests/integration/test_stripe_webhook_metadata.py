#!/usr/bin/env python3
"""
Integration tests for Stripe webhook metadata handling
Tests the fix for metadata field naming and webhook processing
"""

import json
import os
from datetime import datetime, timezone, timezone, UTC
from unittest.mock import MagicMock, patch

import pytest
import stripe

from src.schemas.payments import WebhookProcessingResult
from src.services.payments import StripeService


@pytest.fixture
def stripe_service():
    """Create a StripeService instance for testing"""
    with patch.dict(os.environ, {
        "STRIPE_SECRET_KEY": "sk_test_123456789",
        "STRIPE_WEBHOOK_SECRET": "whsec_test_123456789",
        "STRIPE_PUBLISHABLE_KEY": "pk_test_123456789",
    }):
        service = StripeService()
        yield service


class TestCheckoutSessionMetadata:
    """Test metadata handling in checkout session creation"""

    def test_checkout_session_includes_credits_cents_field(self, stripe_service):
        """Verify checkout session metadata includes 'credits_cents' field"""
        with patch("src.services.payments.get_user_by_id") as mock_get_user, \
             patch("src.services.payments.create_payment") as mock_create_payment, \
             patch("stripe.checkout.Session.create") as mock_create_session, \
             patch("src.services.payments.update_payment_status") as mock_update:

            # Setup mocks
            mock_get_user.return_value = {"id": 1, "email": "test@example.com"}
            mock_create_payment.return_value = {"id": 100, "user_id": 1}

            mock_session = MagicMock()
            mock_session.id = "cs_test_123"
            mock_session.url = "https://checkout.stripe.com/test"
            mock_session.expires_at = int((datetime.now(UTC)).timestamp()) + 86400
            mock_session.payment_intent = "pi_test_123"
            mock_create_session.return_value = mock_session

            # Create checkout session
            from src.schemas.payments import CreateCheckoutSessionRequest
            request = CreateCheckoutSessionRequest(
                amount=1000,
                currency="usd",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel"
            )

            result = stripe_service.create_checkout_session(user_id=1, request=request)

            # Verify metadata includes credits_cents
            call_kwargs = mock_create_session.call_args[1]
            metadata = call_kwargs["metadata"]

            assert "credits_cents" in metadata, "Metadata missing 'credits_cents' field"
            assert metadata["credits_cents"] == "1000", f"Expected credits_cents='1000', got {metadata['credits_cents']}"
            assert metadata["user_id"] == "1", "Metadata missing or incorrect user_id"
            assert metadata["payment_id"] == "100", "Metadata missing or incorrect payment_id"

    def test_checkout_session_includes_backward_compatible_credits_field(self, stripe_service):
        """Verify checkout session metadata includes 'credits' field for backward compatibility"""
        with patch("src.services.payments.get_user_by_id") as mock_get_user, \
             patch("src.services.payments.create_payment") as mock_create_payment, \
             patch("stripe.checkout.Session.create") as mock_create_session, \
             patch("src.services.payments.update_payment_status"):

            mock_get_user.return_value = {"id": 1, "email": "test@example.com"}
            mock_create_payment.return_value = {"id": 100, "user_id": 1}

            mock_session = MagicMock()
            mock_session.id = "cs_test_123"
            mock_session.url = "https://checkout.stripe.com/test"
            mock_session.expires_at = int((datetime.now(UTC)).timestamp()) + 86400
            mock_session.payment_intent = "pi_test_123"
            mock_create_session.return_value = mock_session

            from src.schemas.payments import CreateCheckoutSessionRequest
            request = CreateCheckoutSessionRequest(
                amount=5000,
                currency="usd"
            )

            stripe_service.create_checkout_session(user_id=1, request=request)

            call_kwargs = mock_create_session.call_args[1]
            metadata = call_kwargs["metadata"]

            assert "credits" in metadata, "Metadata missing 'credits' field for backward compatibility"
            assert metadata["credits"] == "5000", f"Expected credits='5000', got {metadata['credits']}"


class TestWebhookMetadataHandling:
    """Test webhook processing with metadata"""

    def test_webhook_parses_credits_cents_field(self, stripe_service):
        """Verify webhook correctly parses credits_cents from metadata"""
        with patch("src.services.payments.is_event_processed") as mock_is_processed, \
             patch("src.services.payments.record_processed_event") as mock_record, \
             patch("src.services.payments.get_payment_by_stripe_intent") as mock_get_payment, \
             patch("src.services.payments.add_credits_to_user") as mock_add_credits, \
             patch("src.services.payments.update_payment_status") as mock_update, \
             patch("stripe.Webhook.construct_event") as mock_construct:

            mock_is_processed.return_value = False

            # Create a webhook event with credits_cents in metadata
            event = {
                "id": "evt_test_123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_123",
                        "payment_intent": "pi_test_123",
                        "payment_status": "paid",
                        "status": "complete",
                        "metadata": {
                            "user_id": "42",
                            "payment_id": "200",
                            "credits_cents": "2500"
                        },
                        "amount_total": 2500,
                        "currency": "usd"
                    }
                },
                "account": "acct_test"
            }

            mock_construct.return_value = event

            # Process webhook
            payload = json.dumps(event).encode()
            result = stripe_service.handle_webhook(payload, "test_signature")

            # Verify it was processed
            assert result.success, f"Webhook processing failed: {result.message}"
            mock_add_credits.assert_called_once()
            call_kwargs = mock_add_credits.call_args[1]
            assert call_kwargs["user_id"] == 42, "Incorrect user_id extracted"
            assert call_kwargs["credits"] == 25.0, "Incorrect credits amount (should be cents/100)"

    def test_webhook_handles_missing_credits_cents_fallback_to_credits(self, stripe_service):
        """Verify webhook falls back to 'credits' field if 'credits_cents' missing"""
        with patch("src.services.payments.is_event_processed") as mock_is_processed, \
             patch("src.services.payments.record_processed_event"), \
             patch("src.services.payments.get_payment_by_stripe_intent") as mock_get_payment, \
             patch("src.services.payments.add_credits_to_user") as mock_add_credits, \
             patch("src.services.payments.update_payment_status"), \
             patch("stripe.Webhook.construct_event") as mock_construct:

            mock_is_processed.return_value = False

            # Create webhook event with old 'credits' field (backward compatibility)
            event = {
                "id": "evt_test_456",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_456",
                        "payment_intent": "pi_test_456",
                        "payment_status": "paid",
                        "status": "complete",
                        "metadata": {
                            "user_id": "50",
                            "payment_id": "300",
                            "credits": "1500"  # Old field name
                        },
                        "amount_total": 1500,
                        "currency": "usd"
                    }
                },
                "account": "acct_test"
            }

            mock_construct.return_value = event

            payload = json.dumps(event).encode()
            result = stripe_service.handle_webhook(payload, "test_signature")

            # Should still process successfully with fallback
            assert result.success, f"Webhook processing failed: {result.message}"
            mock_add_credits.assert_called_once()
            call_kwargs = mock_add_credits.call_args[1]
            assert call_kwargs["credits"] == 15.0, "Failed to parse credits with fallback"

    def test_webhook_always_marked_processed_even_on_error(self, stripe_service):
        """Verify webhook is marked as processed even if handler fails"""
        with patch("src.services.payments.is_event_processed") as mock_is_processed, \
             patch("src.services.payments.record_processed_event") as mock_record, \
             patch("src.services.payments.get_payment_by_stripe_intent") as mock_get_payment, \
             patch("src.services.payments.add_credits_to_user") as mock_add_credits, \
             patch("stripe.Webhook.construct_event") as mock_construct:

            mock_is_processed.return_value = False
            mock_get_payment.return_value = None  # Simulate payment not found
            mock_add_credits.side_effect = Exception("Database error")

            event = {
                "id": "evt_test_error",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_error",
                        "payment_intent": "pi_test_error",
                        "metadata": {
                            "user_id": "60",
                            "payment_id": "400",
                            "credits_cents": "3000"
                        },
                        "amount_total": 3000,
                        "currency": "usd"
                    }
                },
                "account": "acct_test"
            }

            mock_construct.return_value = event

            payload = json.dumps(event).encode()

            # Processing should raise error but record_processed_event should be called
            with pytest.raises(Exception):
                stripe_service.handle_webhook(payload, "test_signature")

            # Event should be marked as processed to prevent retries
            mock_record.assert_called_once()
            call_kwargs = mock_record.call_args[1]
            assert call_kwargs["event_id"] == "evt_test_error"


class TestWebhookHttpStatus:
    """Test webhook HTTP status code handling"""

    def test_webhook_endpoint_returns_200_on_success(self):
        """Verify webhook endpoint returns HTTP 200 on success"""
        from fastapi.testclient import TestClient
        from src.main import app

        with patch("src.routes.payments.stripe_service.handle_webhook") as mock_handle:
            result = WebhookProcessingResult(
                success=True,
                event_type="checkout.session.completed",
                event_id="evt_123",
                message="Success",
                processed_at=datetime.now(UTC)
            )
            mock_handle.return_value = result

            client = TestClient(app)
            response = client.post(
                "/api/stripe/webhook",
                json={"test": "payload"},
                headers={"stripe-signature": "test_sig"}
            )

            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            assert data["success"] is True

    def test_webhook_endpoint_returns_200_on_error(self):
        """Verify webhook endpoint returns HTTP 200 even on processing errors"""
        from fastapi.testclient import TestClient
        from src.main import app

        with patch("src.routes.payments.stripe_service.handle_webhook") as mock_handle:
            mock_handle.side_effect = ValueError("Invalid signature")

            client = TestClient(app)
            response = client.post(
                "/api/stripe/webhook",
                json={"test": "payload"},
                headers={"stripe-signature": "test_sig"}
            )

            # Should return 200 even with error
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            assert data["success"] is False
            assert "error" in data["message"].lower() or "invalid" in data["message"].lower()


class TestMetadataExtraction:
    """Test utility functions for metadata extraction"""

    def test_coerce_to_int_handles_string_numbers(self, stripe_service):
        """Verify _coerce_to_int correctly converts string numbers"""
        assert stripe_service._coerce_to_int("1000") == 1000
        assert stripe_service._coerce_to_int("2500") == 2500
        assert stripe_service._coerce_to_int("100.5") == 100  # Rounds down

    def test_coerce_to_int_handles_float_numbers(self, stripe_service):
        """Verify _coerce_to_int correctly converts floats"""
        assert stripe_service._coerce_to_int(1000.0) == 1000
        assert stripe_service._coerce_to_int(2500.7) == 2501  # Rounds

    def test_coerce_to_int_handles_none(self, stripe_service):
        """Verify _coerce_to_int handles None gracefully"""
        assert stripe_service._coerce_to_int(None) is None
        assert stripe_service._coerce_to_int("") is None

    def test_metadata_to_dict_handles_various_formats(self, stripe_service):
        """Verify _metadata_to_dict handles various metadata formats"""
        # Dict input
        result = stripe_service._metadata_to_dict({"key": "value"})
        assert result == {"key": "value"}

        # None input
        result = stripe_service._metadata_to_dict(None)
        assert result == {}

        # Object with to_dict method
        mock_obj = MagicMock()
        mock_obj.to_dict.return_value = {"key": "value"}
        result = stripe_service._metadata_to_dict(mock_obj)
        assert result == {"key": "value"}
