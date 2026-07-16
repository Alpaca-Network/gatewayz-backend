import asyncio
import time
from unittest.mock import patch

import pytest

from src.services.provider_alerting import _last_alert_sent_at, alert_provider_auth_failure


@patch("src.services.provider_alerting.NotificationService")
@patch("src.services.provider_alerting.Config")
def test_alert_sent_when_ops_email_configured(mock_config, mock_notification_service_cls):
    mock_config.OPS_ALERT_EMAIL = "ops@gatewayz.ai"
    mock_service = mock_notification_service_cls.return_value
    mock_service.send_email_notification.return_value = True
    _last_alert_sent_at.clear()

    alert_provider_auth_failure("openrouter", "tencent/hy3:free", "401 User not found")

    mock_service.send_email_notification.assert_called_once()
    call_kwargs = mock_service.send_email_notification.call_args.kwargs
    assert call_kwargs["to_email"] == "ops@gatewayz.ai"
    assert "openrouter" in call_kwargs["subject"]


@patch("src.services.provider_alerting.NotificationService")
@patch("src.services.provider_alerting.Config")
def test_alert_rate_limited_within_15_minutes(mock_config, mock_notification_service_cls):
    mock_config.OPS_ALERT_EMAIL = "ops@gatewayz.ai"
    mock_service = mock_notification_service_cls.return_value
    _last_alert_sent_at.clear()

    alert_provider_auth_failure("openrouter", "model-a", "401")
    alert_provider_auth_failure("openrouter", "model-b", "401")  # same provider, immediately after

    assert mock_service.send_email_notification.call_count == 1


@patch("src.services.provider_alerting.NotificationService")
@patch("src.services.provider_alerting.Config")
def test_no_alert_when_ops_email_unset(mock_config, mock_notification_service_cls):
    mock_config.OPS_ALERT_EMAIL = None
    mock_service = mock_notification_service_cls.return_value
    _last_alert_sent_at.clear()

    alert_provider_auth_failure("openrouter", "model-a", "401")

    mock_service.send_email_notification.assert_not_called()


@pytest.mark.asyncio
@patch("src.services.provider_alerting.NotificationService")
@patch("src.services.provider_alerting.Config")
async def test_alert_does_not_block_event_loop(mock_config, mock_notification_service_cls):
    """The Resend send must be offloaded to a thread, not run inline on the
    event loop — otherwise a hung/slow Resend call stalls every concurrent
    request during a provider auth-failure storm."""
    mock_config.OPS_ALERT_EMAIL = "ops@gatewayz.ai"
    mock_service = mock_notification_service_cls.return_value

    def _slow_send(**kwargs):
        time.sleep(0.2)
        return True

    mock_service.send_email_notification.side_effect = _slow_send
    _last_alert_sent_at.clear()

    start = time.monotonic()
    alert_provider_auth_failure("openrouter", "tencent/hy3:free", "401 User not found")
    elapsed = time.monotonic() - start

    # Must return immediately without blocking on the slow Resend call.
    assert elapsed < 0.1
    mock_service.send_email_notification.assert_not_called()

    # Let the offloaded background thread task run to completion.
    await asyncio.sleep(0.3)

    mock_service.send_email_notification.assert_called_once()
    assert _last_alert_sent_at["openrouter"] > 0


@patch("src.services.provider_alerting.NotificationService")
@patch("src.services.provider_alerting.Config")
def test_alert_sent_inline_without_running_event_loop(mock_config, mock_notification_service_cls):
    """When called from a plain synchronous context (no running event loop),
    the alert must still be sent — falling back to an inline send rather than
    silently dropping it."""
    mock_config.OPS_ALERT_EMAIL = "ops@gatewayz.ai"
    mock_service = mock_notification_service_cls.return_value
    mock_service.send_email_notification.return_value = True
    _last_alert_sent_at.clear()

    alert_provider_auth_failure("openrouter", "tencent/hy3:free", "401 User not found")

    mock_service.send_email_notification.assert_called_once()
    assert _last_alert_sent_at["openrouter"] > 0
