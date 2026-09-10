"""Tests for src/services/email.py -- the one Resend client (Phase A, A4)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.email import (
    EmailResult,
    _mask_email,
    send_email,
    send_password_reset,
    send_provider_approved,
    send_staff_invite,
)


class TestMaskEmail:
    def test_masks_local_part(self):
        assert _mask_email("someone@example.com") == "s***@example.com"

    def test_handles_missing_at(self):
        assert _mask_email("not-an-email") == "***"

    def test_handles_empty(self):
        assert _mask_email("") == "***"


class TestSendEmailNotConfigured:
    def test_no_api_key_returns_not_configured_and_never_raises(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        result = send_email("user@example.com", "Subject", "<p>hi</p>")
        assert result == EmailResult(sent=False, id=None, error="not_configured")


class TestSendEmailSuccess:
    def test_sent_true_with_id_on_200(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "email_123"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = send_email("user@example.com", "Subject", "<p>hi</p>", text="hi")
        assert result.sent is True
        assert result.id == "email_123"
        assert result.error is None
        # Never log/leak the raw recipient beyond what httpx itself sees.
        called_kwargs = mock_post.call_args.kwargs
        assert called_kwargs["json"]["to"] == ["user@example.com"]
        assert called_kwargs["headers"]["Authorization"] == "Bearer re_test_key"
        assert called_kwargs["timeout"] == 10.0


class TestSendEmailSuspendedKey:
    @pytest.mark.parametrize("status_code", [401, 403])
    def test_suspended_or_invalid_key_visible_not_swallowed(self, monkeypatch, status_code):
        monkeypatch.setenv("RESEND_API_KEY", "re_suspended_key")
        mock_response = MagicMock()
        mock_response.status_code = status_code
        with patch("httpx.post", return_value=mock_response):
            result = send_email("user@example.com", "Subject", "<p>hi</p>")
        assert result.sent is False
        assert result.error == "suspended_or_invalid_key"


class TestSendEmailOtherFailures:
    def test_generic_http_error_status(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        mock_response = MagicMock()
        mock_response.status_code = 500
        with patch("httpx.post", return_value=mock_response):
            result = send_email("user@example.com", "Subject", "<p>hi</p>")
        assert result.sent is False
        assert result.error == "http_500"

    def test_timeout_never_raises(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            result = send_email("user@example.com", "Subject", "<p>hi</p>")
        assert result.sent is False
        assert result.error == "timeout"

    def test_network_error_never_raises(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
            result = send_email("user@example.com", "Subject", "<p>hi</p>")
        assert result.sent is False
        assert result.error == "ConnectError"


class TestTemplates:
    def test_send_staff_invite_calls_send_email(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "email_1"}
        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = send_staff_invite(
                "staff@example.com",
                "https://admin.gatewayz.ai/invite/abc",
                "admin",
                "boss@example.com",
            )
        assert result.sent is True
        payload = mock_post.call_args.kwargs["json"]
        assert "admin" in payload["html"]
        assert "boss@example.com" in payload["html"]

    def test_send_password_reset_not_configured(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        result = send_password_reset("user@example.com", "https://gatewayz.ai/reset/xyz")
        assert result.sent is False
        assert result.error == "not_configured"

    def test_send_provider_approved_not_configured(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        result = send_provider_approved("provider@example.com", "Acme GPUs")
        assert result.sent is False
        assert result.error == "not_configured"
