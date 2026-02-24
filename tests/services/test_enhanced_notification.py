#!/usr/bin/env python3
"""
Tests for EnhancedNotificationService email validation.

Tests cover:
- Privy fallback email detection
- Valid email format detection
- Invalid email format detection
- Email sending skip behavior for invalid emails
"""

from unittest.mock import patch

import pytest


class TestEmailValidation:
    """Test email validation logic for EnhancedNotificationService"""

    @pytest.fixture
    def notification_service(self):
        """EnhancedNotificationService instance with mocked dependencies"""
        with (
            patch("src.enhanced_notification_service.supabase_config"),
            patch("src.enhanced_notification_service.resend"),
        ):
            from src.enhanced_notification_service import EnhancedNotificationService

            service = EnhancedNotificationService()
            service.resend_api_key = "test_key"
            service.from_email = "test@example.com"
            service.app_name = "Test App"
            service.email_client_available = True
            return service

    def test_valid_email_passes_validation(self, notification_service):
        """Test that valid emails pass validation"""
        valid_emails = [
            "user@example.com",
            "test.user@domain.org",
            "name+tag@company.co.uk",
            "simple@test.io",
        ]
        for email in valid_emails:
            assert (
                notification_service._is_valid_email_for_sending(email) is True
            ), f"Expected {email} to be valid"

    def test_privy_fallback_email_rejected(self, notification_service):
        """Test that Privy fallback emails are rejected"""
        privy_emails = [
            "did:privy:cmjlc79wn01m9l70bqdnfkzqi@privy.user",
            "did:privy:abc123@privy.user",
            "anything@privy.user",
        ]
        for email in privy_emails:
            assert (
                notification_service._is_valid_email_for_sending(email) is False
            ), f"Expected {email} to be invalid (Privy fallback)"

    def test_empty_email_rejected(self, notification_service):
        """Test that empty/None emails are rejected"""
        assert notification_service._is_valid_email_for_sending("") is False
        assert notification_service._is_valid_email_for_sending(None) is False

    def test_invalid_email_format_rejected(self, notification_service):
        """Test that invalid email formats are rejected"""
        invalid_emails = [
            "not-an-email",
            "missing@tld",
            "@no-local-part.com",
        ]
        for email in invalid_emails:
            assert (
                notification_service._is_valid_email_for_sending(email) is False
            ), f"Expected {email} to be invalid"

    @patch("src.enhanced_notification_service.resend")
    def test_send_welcome_email_skips_privy_fallback(self, mock_resend, notification_service):
        """Test that welcome email is skipped for Privy fallback addresses"""
        result = notification_service.send_welcome_email(
            user_id=123, username="testuser", email="did:privy:abc123@privy.user", credits=5
        )

        # Should return True (success) without attempting to send
        assert result is True
        # Resend should NOT be called
        mock_resend.Emails.send.assert_not_called()

    @patch("src.enhanced_notification_service.resend")
    def test_send_email_notification_skips_privy_fallback(self, mock_resend, notification_service):
        """Test that email notification is skipped for Privy fallback addresses"""
        result = notification_service.send_email_notification(
            to_email="did:privy:cmjlc79wn01m9l70bqdnfkzqi@privy.user",
            subject="Test Subject",
            html_content="<p>Test</p>",
            text_content="Test",
        )

        # Should return True (success) without attempting to send
        assert result is True
        # Resend should NOT be called
        mock_resend.Emails.send.assert_not_called()

    @patch("src.enhanced_notification_service.resend")
    def test_send_email_notification_sends_valid_email(self, mock_resend, notification_service):
        """Test that valid emails are sent normally"""
        mock_resend.Emails.send.return_value = {"id": "test-email-id"}

        result = notification_service.send_email_notification(
            to_email="valid@example.com",
            subject="Test Subject",
            html_content="<p>Test</p>",
            text_content="Test",
        )

        # Should return True and call Resend
        assert result is True
        mock_resend.Emails.send.assert_called_once()


class TestEdgeCases:
    """Test edge cases for email validation"""

    @pytest.fixture
    def notification_service(self):
        """EnhancedNotificationService instance with mocked dependencies"""
        with (
            patch("src.enhanced_notification_service.supabase_config"),
            patch("src.enhanced_notification_service.resend"),
        ):
            from src.enhanced_notification_service import EnhancedNotificationService

            service = EnhancedNotificationService()
            return service

    def test_email_ending_with_privy_user_substring(self, notification_service):
        """Test emails that contain but don't end with @privy.user"""
        # This email happens to contain 'privy.user' but in a different context
        # It should still be rejected as it ends with @privy.user
        email = "privy.user.fan@privy.user"
        assert notification_service._is_valid_email_for_sending(email) is False

    def test_similar_but_valid_domain(self, notification_service):
        """Test emails with similar-looking but valid domains"""
        # These are NOT @privy.user so should be valid
        valid_emails = [
            "user@privy.users.com",
            "user@notprivy.user.com",
            "user@privyuser.com",
        ]
        for email in valid_emails:
            assert (
                notification_service._is_valid_email_for_sending(email) is True
            ), f"Expected {email} to be valid (not @privy.user domain)"

    def test_password_reset_validates_email_before_token_creation(self, notification_service):
        """Test that password reset validates email BEFORE creating token"""
        with patch.object(notification_service, "_is_valid_email_for_sending", return_value=False):
            # Should return None without creating token
            result = notification_service.send_password_reset_email(
                user_id=1, username="test", email="invalid@privy.user"
            )
            assert result is None
