"""Tests for EnhancedNotificationService.send_email_notification delegating
to the shared src/services/email.py service (Phase A, A4)."""

from unittest.mock import patch

from src.enhanced_notification_service import EnhancedNotificationService
from src.services.email import EmailResult


def _service():
    with patch("src.config.supabase_config.get_supabase_client", side_effect=Exception("no db")):
        return EnhancedNotificationService()


class TestSendEmailNotificationDelegatesToEmailService:
    def test_success_delegates_and_returns_true(self):
        service = _service()
        with patch(
            "src.services.email.send_email",
            return_value=EmailResult(sent=True, id="email_1"),
        ) as mock_send_email:
            result = service.send_email_notification(
                "user@example.com", "Subject", "<p>hi</p>", "hi"
            )
        assert result is True
        mock_send_email.assert_called_once_with(
            "user@example.com", "Subject", "<p>hi</p>", text="hi"
        )

    def test_failure_returns_false_without_raising(self):
        service = _service()
        with patch(
            "src.services.email.send_email",
            return_value=EmailResult(sent=False, error="suspended_or_invalid_key"),
        ):
            result = service.send_email_notification("user@example.com", "Subject", "<p>hi</p>")
        assert result is False

    def test_placeholder_email_skipped_before_calling_email_service(self):
        service = _service()
        with patch("src.services.email.send_email") as mock_send_email:
            result = service.send_email_notification(
                "did:privy:abc@privy.user", "Subject", "<p>hi</p>"
            )
        assert result is True
        mock_send_email.assert_not_called()

    def test_invalid_email_skipped_before_calling_email_service(self):
        # Passes the "not a placeholder" check (_is_valid_email_for_sending)
        # but fails the stricter TLD-length check (_is_valid_email) --
        # exercises the second validation gate in send_email_notification.
        service = _service()
        with patch("src.services.email.send_email") as mock_send_email:
            result = service.send_email_notification("user@a.c", "Subject", "<p>hi</p>")
        assert result is False
        mock_send_email.assert_not_called()
