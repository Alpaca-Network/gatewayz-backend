import time
from unittest.mock import patch

from src.services.provider_alerting import alert_provider_auth_failure, _last_alert_sent_at


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
