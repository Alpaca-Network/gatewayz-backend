import asyncio
import logging
import time
from unittest.mock import patch

import pytest

import src.services.provider_alerting as pa
from src.services.email import EmailResult
from src.services.provider_alerting import (
    _last_alert_sent_at,
    alert_provider_auth_failure,
    alert_provider_budget_exhausted,
    ops_alerts_status,
)

STAFF = [
    {"email": "a@gatewayz.ai", "role": "superadmin", "is_active": True},
    {"email": "b@gatewayz.ai", "role": "admin", "is_active": None},
    {"email": "gone@gatewayz.ai", "role": "admin", "is_active": False},
]


@pytest.fixture(autouse=True)
def _reset():
    _last_alert_sent_at.clear()
    pa._recipient_cache = None
    with patch("src.utils.sentry_context.capture_provider_error") as sentry:
        pa._test_sentry = sentry
        yield
    _last_alert_sent_at.clear()
    pa._recipient_cache = None


def _env(value):
    return patch.object(pa.Config, "OPS_ALERT_EMAIL", value)


def _send(ok=True):
    return patch("src.services.email.send_email", return_value=EmailResult(sent=ok))


def test_alert_sent_when_ops_email_configured():
    with _env("ops@gatewayz.ai"), _send() as send:
        alert_provider_auth_failure("openrouter", "m", "401 User not found")
    send.assert_called_once()
    assert send.call_args.kwargs["to"] == "ops@gatewayz.ai"
    assert "openrouter" in send.call_args.kwargs["subject"]


def test_rate_limited_within_cooldown():
    with _env("ops@gatewayz.ai"), _send() as send:
        alert_provider_auth_failure("openrouter", "a", "401")
        alert_provider_auth_failure("openrouter", "b", "401")
    assert send.call_count == 1


def test_env_overrides_staff_and_supports_commas():
    with (
        _env("x@a.ai, y@a.ai"),
        _send() as send,
        patch("src.db.staff.list_staff", return_value=STAFF) as staff,
    ):
        alert_provider_auth_failure("openai", "m", "401")
    assert [c.kwargs["to"] for c in send.call_args_list] == ["x@a.ai", "y@a.ai"]
    staff.assert_not_called()


def test_falls_back_to_active_staff_when_env_unset():
    with _env(None), _send() as send, patch("src.db.staff.list_staff", return_value=STAFF):
        alert_provider_auth_failure("anthropic", "m", "401")
    assert sorted(c.kwargs["to"] for c in send.call_args_list) == ["a@gatewayz.ai", "b@gatewayz.ai"]


def test_no_recipients_is_safe_noop_warns_once_per_cooldown(caplog):
    with _env(None), _send() as send, patch("src.db.staff.list_staff", return_value=[]):
        with caplog.at_level(logging.WARNING):
            alert_provider_auth_failure("anthropic", "m", "401")
            alert_provider_auth_failure("anthropic", "m", "401")
    send.assert_not_called()
    assert sum("no recipient" in r.message for r in caplog.records) == 1


def test_staff_lookup_failure_is_safe_noop():
    with (
        _env(None),
        _send() as send,
        patch("src.db.staff.list_staff", side_effect=RuntimeError("db")),
    ):
        alert_provider_auth_failure("anthropic", "m", "401")
    send.assert_not_called()


def test_sentry_event_emitted_even_without_recipients():
    with _env(None), _send(), patch("src.db.staff.list_staff", return_value=[]):
        alert_provider_budget_exhausted("anthropic", "credit_exhausted", "claude-x")
    pa._test_sentry.assert_called_once()
    assert pa._test_sentry.call_args.kwargs["provider"] == "anthropic"


def test_budget_alert_sent_and_hourly_cooldown():
    with _env("ops@gatewayz.ai"), _send() as send:
        alert_provider_budget_exhausted("anthropic", "credit_exhausted", "claude-x")
        alert_provider_budget_exhausted("anthropic", "credit_exhausted", "claude-y")
    assert send.call_count == 1
    assert "anthropic" in send.call_args.kwargs["subject"]


def test_failed_send_allows_retry():
    with _env("ops@gatewayz.ai"), _send(ok=False) as send:
        alert_provider_auth_failure("openai", "m", "401")
        alert_provider_auth_failure("openai", "m", "401")
    assert send.call_count == 2


def test_status_reports_source_and_count():
    with _env(None), patch("src.db.staff.list_staff", return_value=STAFF):
        assert ops_alerts_status() == {"configured": True, "recipients": 2, "source": "staff"}
    pa._recipient_cache = None
    with _env(None), patch("src.db.staff.list_staff", return_value=[]):
        assert ops_alerts_status() == {"configured": False, "recipients": 0, "source": "none"}
    with _env("o@a.ai"):
        assert ops_alerts_status() == {"configured": True, "recipients": 1, "source": "env"}


@pytest.mark.asyncio
async def test_alert_does_not_block_event_loop():
    def _slow(**kwargs):
        time.sleep(0.2)
        return EmailResult(sent=True)

    with _env("ops@gatewayz.ai"), patch("src.services.email.send_email", side_effect=_slow) as send:
        start = time.monotonic()
        alert_provider_auth_failure("openrouter", "m", "401")
        assert time.monotonic() - start < 0.1
        send.assert_not_called()
        await asyncio.sleep(0.4)
        send.assert_called_once()
