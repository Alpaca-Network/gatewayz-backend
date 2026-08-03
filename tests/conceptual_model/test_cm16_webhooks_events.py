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
# GAP: The MVP refactor (commit 8fa2e773 "refactor(mvp): remove notifications")
# deleted src/services/notification.py entirely, including NotificationService
# and check_low_balance_alert / LowBalanceAlert. The surviving
# EnhancedNotificationService only handles welcome / password-reset / plan-upgrade
# / api-key emails — it has no low-balance alerting equivalent. The spec still
# lists the credits.low event, so this is a documented code-vs-spec gap.
@pytest.mark.cm_gap
@pytest.mark.xfail(
    reason="Low-balance alerting removed in MVP refactor (8fa2e773); no surviving equivalent",
    raises=(ModuleNotFoundError, ImportError),
)
class TestCM1602WebhookCreditsLowEventTriggered:
    def test_webhook_credits_low_event_triggered(self):
        """check_low_balance_alert returns a LowBalanceAlert when the user's
        credits fall below the $5 threshold."""
        from src.services.notification import NotificationService

        mock_supabase = MagicMock()

        # User with credits below the $5 threshold
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": 1,
                    "subscription_allowance": 0.0,
                    "purchased_credits": 2.0,
                    "api_key": "gw_test",
                }
            ]
        )

        # Patch get_supabase_client so __init__ uses our mock
        with patch(
            "src.services.notification.get_supabase_client",
            return_value=mock_supabase,
        ):
            svc = NotificationService()

        # Mock preferences to allow email notifications
        mock_prefs = MagicMock()
        mock_prefs.email_notifications = True

        with (
            patch.object(svc, "get_user_preferences", return_value=mock_prefs),
            patch.object(svc, "_has_recent_notification", return_value=False),
            patch(
                "src.services.notification.validate_trial_access",
                return_value={"is_trial": False},
            ),
            patch(
                "src.services.notification.get_user_plan",
                return_value={"plan_name": "Pro"},
            ),
        ):
            alert = svc.check_low_balance_alert(user_id=1)

        assert alert is not None, "Alert should fire when credits ($2) < threshold ($5)"
        assert alert.current_credits == 2.0
        assert alert.threshold == 5.0
        assert alert.user_id == 1

    def test_no_alert_when_credits_above_threshold(self):
        """check_low_balance_alert returns None when credits are above $5."""
        from src.services.notification import NotificationService

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": 1,
                    "subscription_allowance": 0.0,
                    "purchased_credits": 50.0,
                    "api_key": "gw_test",
                }
            ]
        )

        with patch(
            "src.services.notification.get_supabase_client",
            return_value=mock_supabase,
        ):
            svc = NotificationService()

        mock_prefs = MagicMock()
        mock_prefs.email_notifications = True

        with patch.object(svc, "get_user_preferences", return_value=mock_prefs):
            alert = svc.check_low_balance_alert(user_id=1)

        assert alert is None, "No alert when credits ($50) > threshold ($5)"


# ---------------------------------------------------------------------------
# CM-16.3  credits.depleted event triggered
# ---------------------------------------------------------------------------
# GAP: Same removal as CM-16.2 — check_low_balance_alert no longer exists after
# the MVP notifications purge. Spec still lists credits.depleted, so this is a
# documented code-vs-spec gap rather than a regression.
@pytest.mark.cm_gap
@pytest.mark.xfail(
    reason="Low-balance alerting removed in MVP refactor (8fa2e773); no surviving equivalent",
    raises=(ModuleNotFoundError, ImportError),
)
class TestCM1603WebhookCreditsDepletedEventTriggered:
    def test_webhook_credits_depleted_event_triggered(self):
        """The low balance alert fires when credits are 0 (depleted),
        since check_low_balance_alert uses <= comparison against the threshold."""
        from src.services.notification import NotificationService

        mock_supabase = MagicMock()
        # User with 0 credits (depleted)
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": 1, "credits": 0.0, "api_key": "gw_test"}]
        )

        with patch(
            "src.services.notification.get_supabase_client",
            return_value=mock_supabase,
        ):
            svc = NotificationService()

        mock_prefs = MagicMock()
        mock_prefs.email_notifications = True

        with (
            patch.object(svc, "get_user_preferences", return_value=mock_prefs),
            patch.object(svc, "_has_recent_notification", return_value=False),
            patch(
                "src.services.notification.validate_trial_access",
                return_value={"is_trial": False},
            ),
            patch(
                "src.services.notification.get_user_plan",
                return_value={"plan_name": "Pro"},
            ),
        ):
            alert = svc.check_low_balance_alert(user_id=1)

        assert alert is not None, "Alert should fire when credits are 0 (depleted)"
        assert alert.current_credits == 0.0


# ---------------------------------------------------------------------------
# CM-16.4  Webhook retry with exponential backoff
# ---------------------------------------------------------------------------
@pytest.mark.cm_gap
@pytest.mark.xfail(reason="No retry loop in send_webhook_notification, feature absent")
class TestCM1604WebhookRetryExponentialBackoff:
    def test_webhook_retry_exponential_backoff(self):
        """send_webhook_notification should retry failed deliveries with
        exponential backoff. Currently it sends once with no retry loop."""
        from src.services.notification import NotificationService

        mock_supabase = MagicMock()
        with patch(
            "src.services.notification.get_supabase_client",
            return_value=mock_supabase,
        ):
            svc = NotificationService()

        # Mock a webhook notification that fails on first attempt
        with patch("src.services.notification.requests.post") as mock_post:
            mock_post.side_effect = [
                Exception("Connection refused"),  # 1st attempt fails
                MagicMock(status_code=200),  # 2nd attempt would succeed
            ]
            # If retry is implemented, the second call should succeed
            result = svc.send_webhook_notification(
                url="https://example.com/webhook",
                payload={"event": "credits.low"},
                webhook_secret="test_secret",
            )
            # With retry, post should be called more than once
            assert mock_post.call_count > 1, "send_webhook_notification should retry on failure"


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
