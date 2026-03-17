"""
CM-16 Webhooks & Events

Tests covering webhook HMAC signing, credits.low / credits.depleted events,
retry with exponential backoff, and Stripe webhook 200-always behaviour.
"""

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CM-16.1  Webhook payload HMAC-signed
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1601WebhookPayloadHmacSigned:
    def test_webhook_payload_hmac_signed(self):
        """generate_webhook_signature produces a valid HMAC-SHA256 hex digest
        for a given payload and secret."""
        from src.utils.security_validators import generate_webhook_signature

        payload = '{"event": "credits.low", "user_id": 42}'
        secret = "whsec_test_secret_key"

        signature = generate_webhook_signature(payload, secret)

        # Independently compute the expected HMAC
        expected = hmac.new(
            secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        assert signature == expected, (
            f"generate_webhook_signature must produce HMAC-SHA256. "
            f"Got {signature}, expected {expected}"
        )
        # Must be a 64-char hex string (SHA-256)
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)


# ---------------------------------------------------------------------------
# CM-16.2  credits.low event triggered
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1602WebhookCreditsLowEventTriggered:
    def test_webhook_credits_low_event_triggered(self):
        """NotificationService has check_low_balance_alert and
        send_low_balance_alert methods for credits.low events."""
        from src.services.notification import NotificationService

        assert hasattr(NotificationService, "check_low_balance_alert")
        assert hasattr(NotificationService, "send_low_balance_alert")
        assert callable(NotificationService.check_low_balance_alert)
        assert callable(NotificationService.send_low_balance_alert)


# ---------------------------------------------------------------------------
# CM-16.3  credits.depleted event triggered
# ---------------------------------------------------------------------------
@pytest.mark.cm_gap
@pytest.mark.xfail(reason="No distinct credits.depleted event, only LOW_BALANCE")
class TestCM1603WebhookCreditsDepletedEventTriggered:
    def test_webhook_credits_depleted_event_triggered(self):
        """There should be a distinct credits.depleted webhook event type,
        separate from the low-balance alert. Currently only LOW_BALANCE exists."""
        from src.services.notification import NotificationService

        # Look for a depleted-specific method or event type
        assert hasattr(NotificationService, "send_credits_depleted_alert"), (
            "NotificationService should have a send_credits_depleted_alert method "
            "for a distinct credits.depleted event"
        )


# ---------------------------------------------------------------------------
# CM-16.4  Webhook retry with exponential backoff
# ---------------------------------------------------------------------------
@pytest.mark.cm_gap
@pytest.mark.xfail(reason="No retry loop in send_webhook_notification, feature absent")
class TestCM1604WebhookRetryExponentialBackoff:
    def test_webhook_retry_exponential_backoff(self):
        """send_webhook_notification should retry failed deliveries with
        exponential backoff. Currently it sends once with no retry loop."""
        import inspect

        from src.services.notification import NotificationService

        source = inspect.getsource(NotificationService.send_webhook_notification)
        # A retry loop would have retry/backoff/sleep patterns
        assert "retry" in source.lower() and (
            "backoff" in source.lower() or "sleep" in source.lower()
        ), "send_webhook_notification should implement retry with exponential backoff"


# ---------------------------------------------------------------------------
# CM-16.5  Stripe webhook always returns 200
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1605StripeWebhookAlwaysReturns200:
    @pytest.mark.asyncio
    async def test_stripe_webhook_always_returns_200(self):
        """The Stripe webhook handler returns HTTP 200 even when processing
        encounters errors (e.g. bad signature)."""
        from starlette.responses import JSONResponse

        from src.routes.payments import stripe_webhook

        # Create a mock request with invalid payload
        mock_request = MagicMock()
        mock_request.body = AsyncMock(return_value=b"invalid payload")

        # Call with a bad signature - should still return 200
        with patch("src.routes.payments.stripe_service") as mock_service:
            mock_service.handle_webhook.side_effect = ValueError("Invalid signature")
            response = await stripe_webhook(mock_request, stripe_signature="bad_sig")

        assert isinstance(response, JSONResponse)
        assert (
            response.status_code == 200
        ), f"Stripe webhook must always return 200, got {response.status_code}"
