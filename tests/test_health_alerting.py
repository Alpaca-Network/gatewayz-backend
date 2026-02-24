"""
Tests for the Health Alerting Service

Tests alert creation, sending, and de-duplication.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.health_alerting import (
    Alert,
    AlertChannel,
    AlertSeverity,
    AlertType,
    HealthAlertingService,
)


@pytest.fixture
def alerting_service():
    """Create an alerting service instance for testing"""
    return HealthAlertingService()


def test_alert_creation():
    """Test Alert dataclass creation"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test Alert",
        message="This is a test alert",
        provider="openai",
        model="gpt-4",
        gateway="openrouter",
        metrics={"uptime": 95.5},
    )

    assert alert.alert_type == AlertType.PROVIDER_DOWN
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.title == "Test Alert"
    assert alert.message == "This is a test alert"
    assert alert.provider == "openai"
    assert alert.model == "gpt-4"
    assert alert.gateway == "openrouter"
    assert alert.metrics == {"uptime": 95.5}
    assert alert.timestamp is not None


def test_alert_type_enum():
    """Test AlertType enum values"""
    assert AlertType.PROVIDER_DOWN.value == "provider_down"
    assert AlertType.PROVIDER_DEGRADED.value == "provider_degraded"
    assert AlertType.CRITICAL_MODEL_DOWN.value == "critical_model_down"
    assert AlertType.MODEL_DEGRADED.value == "model_degraded"
    assert AlertType.HIGH_ERROR_RATE.value == "high_error_rate"
    assert AlertType.SLOW_RESPONSE.value == "slow_response"
    assert AlertType.CIRCUIT_BREAKER_OPEN.value == "circuit_breaker_open"


def test_alert_severity_enum():
    """Test AlertSeverity enum values"""
    assert AlertSeverity.CRITICAL.value == "critical"
    assert AlertSeverity.HIGH.value == "high"
    assert AlertSeverity.MEDIUM.value == "medium"
    assert AlertSeverity.LOW.value == "low"


def test_alert_channel_enum():
    """Test AlertChannel enum values"""
    assert AlertChannel.EMAIL.value == "email"
    assert AlertChannel.SLACK.value == "slack"
    assert AlertChannel.DISCORD.value == "discord"
    assert AlertChannel.WEBHOOK.value == "webhook"
    assert AlertChannel.PAGERDUTY.value == "pagerduty"


def test_alerting_service_initialization(alerting_service):
    """Test alerting service initializes correctly"""
    assert alerting_service.alert_history == {}
    assert alerting_service.alert_cooldown_minutes == 30
    assert isinstance(alerting_service.enabled_channels, list)


def test_should_send_alert_first_time(alerting_service):
    """Test alert should be sent on first occurrence"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    assert alerting_service._should_send_alert(alert) is True


def test_should_send_alert_cooldown(alerting_service):
    """Test alert is suppressed during cooldown period"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    # First alert should be sent
    assert alerting_service._should_send_alert(alert) is True
    alerting_service._record_alert(alert)

    # Second alert immediately should be suppressed
    assert alerting_service._should_send_alert(alert) is False


def test_should_send_alert_after_cooldown(alerting_service):
    """Test alert is sent after cooldown period expires"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    # Record alert in the past (beyond cooldown)
    alerting_service._record_alert(alert)
    alert_key = f"{alert.alert_type}:{alert.provider}:{alert.model}:{alert.gateway}"
    alerting_service.alert_history[alert_key] = datetime.now(UTC) - timedelta(
        minutes=alerting_service.alert_cooldown_minutes + 1
    )

    # Should be allowed to send again
    assert alerting_service._should_send_alert(alert) is True


def test_record_alert(alerting_service):
    """Test recording alert in history"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    alerting_service._record_alert(alert)

    alert_key = f"{alert.alert_type}:{alert.provider}:{alert.model}:{alert.gateway}"
    assert alert_key in alerting_service.alert_history
    assert isinstance(alerting_service.alert_history[alert_key], datetime)


def test_record_alert_cleanup_old(alerting_service):
    """Test old alerts are cleaned up from history"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    # Add old alert (beyond 24h)
    alert_key = f"{alert.alert_type}:old:model:gateway"
    alerting_service.alert_history[alert_key] = datetime.now(UTC) - timedelta(hours=25)

    # Record new alert
    alerting_service._record_alert(alert)

    # Old alert should be cleaned up
    assert alert_key not in alerting_service.alert_history


def test_get_severity_color(alerting_service):
    """Test severity color mapping for HTML"""
    assert alerting_service._get_severity_color(AlertSeverity.CRITICAL) == "#dc3545"
    assert alerting_service._get_severity_color(AlertSeverity.HIGH) == "#fd7e14"
    assert alerting_service._get_severity_color(AlertSeverity.MEDIUM) == "#ffc107"
    assert alerting_service._get_severity_color(AlertSeverity.LOW) == "#17a2b8"


def test_get_severity_color_slack(alerting_service):
    """Test severity color mapping for Slack"""
    assert alerting_service._get_severity_color_slack(AlertSeverity.CRITICAL) == "danger"
    assert alerting_service._get_severity_color_slack(AlertSeverity.HIGH) == "warning"
    assert alerting_service._get_severity_color_slack(AlertSeverity.MEDIUM) == "warning"


def test_get_severity_color_discord(alerting_service):
    """Test severity color mapping for Discord"""
    assert alerting_service._get_severity_color_discord(AlertSeverity.CRITICAL) == 14495300
    assert alerting_service._get_severity_color_discord(AlertSeverity.HIGH) == 16612380
    assert alerting_service._get_severity_color_discord(AlertSeverity.MEDIUM) == 16776960
    assert alerting_service._get_severity_color_discord(AlertSeverity.LOW) == 1552639


def test_format_metrics_html(alerting_service):
    """Test metrics formatting for HTML"""
    metrics = {"uptime": 95.5, "response_time": 450, "error_rate": 5.2}

    html = alerting_service._format_metrics_html(metrics)

    assert "<h4>Metrics</h4>" in html
    assert "uptime" in html
    assert "95.5" in html
    assert "response_time" in html
    assert "450" in html


def test_format_metrics_html_empty(alerting_service):
    """Test metrics formatting with no metrics"""
    html = alerting_service._format_metrics_html(None)
    assert html == ""

    html = alerting_service._format_metrics_html({})
    assert html == ""


@pytest.mark.asyncio
@patch("resend.Emails.send")
async def test_send_email_alert(mock_send_email, alerting_service):
    """Test sending email alert"""
    mock_send_email.return_value = None  # Resend returns None on success

    # Patch Config where it's imported from
    with patch("src.config.Config") as mock_config:
        # Set attributes that getattr will access
        mock_config.ADMIN_EMAIL = "admin@example.com"
        mock_config.RESEND_API_KEY = "re_test_key"
        mock_config.FROM_EMAIL = "noreply@test.com"
        mock_config.SUPPORT_EMAIL = None

        alerting_service.enabled_channels = [AlertChannel.EMAIL]

        alert = Alert(
            alert_type=AlertType.PROVIDER_DOWN,
            severity=AlertSeverity.CRITICAL,
            title="Provider Down",
            message="OpenAI provider is offline",
            provider="openai",
        )

        await alerting_service._send_email_alert(alert)
        # Verify email was sent
        assert mock_send_email.called


@pytest.mark.asyncio
async def test_alert_provider_down(alerting_service):
    """Test convenience method for provider down alert"""
    with patch.object(alerting_service, "send_alert", new=AsyncMock()) as mock_send:
        await alerting_service.alert_provider_down(
            provider="openai", gateway="openrouter", metrics={"uptime": 0}
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][0]
        assert call_args.alert_type == AlertType.PROVIDER_DOWN
        assert call_args.severity == AlertSeverity.CRITICAL
        assert call_args.provider == "openai"
        assert call_args.gateway == "openrouter"


@pytest.mark.asyncio
async def test_alert_critical_model_down(alerting_service):
    """Test convenience method for critical model down alert"""
    with patch.object(alerting_service, "send_alert", new=AsyncMock()) as mock_send:
        await alerting_service.alert_critical_model_down(
            provider="openai",
            model="gpt-4",
            gateway="openrouter",
            consecutive_failures=5,
            metrics={"uptime": 80},
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][0]
        assert call_args.alert_type == AlertType.CRITICAL_MODEL_DOWN
        assert call_args.severity == AlertSeverity.HIGH
        assert call_args.model == "gpt-4"


@pytest.mark.asyncio
async def test_alert_high_error_rate(alerting_service):
    """Test convenience method for high error rate alert"""
    with patch.object(alerting_service, "send_alert", new=AsyncMock()) as mock_send:
        await alerting_service.alert_high_error_rate(provider="openai", error_rate=15.5)

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][0]
        assert call_args.alert_type == AlertType.HIGH_ERROR_RATE
        assert call_args.severity == AlertSeverity.MEDIUM


@pytest.mark.asyncio
async def test_alert_slow_response(alerting_service):
    """Test convenience method for slow response alert"""
    with patch.object(alerting_service, "send_alert", new=AsyncMock()) as mock_send:
        await alerting_service.alert_slow_response(
            provider="openai", avg_response_time=5000, threshold=2000
        )

        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][0]
        assert call_args.alert_type == AlertType.SLOW_RESPONSE
        assert call_args.severity == AlertSeverity.LOW


@pytest.mark.asyncio
async def test_send_alert_with_deduplication(alerting_service):
    """Test sending alert respects de-duplication"""
    alert = Alert(
        alert_type=AlertType.PROVIDER_DOWN,
        severity=AlertSeverity.CRITICAL,
        title="Test",
        message="Test",
        provider="openai",
    )

    with patch.object(alerting_service, "_send_to_channel", new=AsyncMock()):
        alerting_service.enabled_channels = [AlertChannel.EMAIL]

        # First send should work
        await alerting_service.send_alert(alert)

        # Second send should be skipped (de-duplicated)
        await alerting_service.send_alert(alert)

        # Only one alert should be recorded
        alert_key = f"{alert.alert_type}:{alert.provider}:{alert.model}:{alert.gateway}"
        assert alert_key in alerting_service.alert_history
