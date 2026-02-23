"""
Health Monitoring Alerting Service

Sends notifications when models or providers experience issues.
Supports multiple channels: email, Slack, Discord, PagerDuty, webhooks.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AlertType(str, Enum):  # noqa: UP042
    """Types of alerts"""

    PROVIDER_DOWN = "provider_down"
    PROVIDER_DEGRADED = "provider_degraded"
    CRITICAL_MODEL_DOWN = "critical_model_down"
    MODEL_DEGRADED = "model_degraded"
    HIGH_ERROR_RATE = "high_error_rate"
    SLOW_RESPONSE = "slow_response"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


class AlertSeverity(str, Enum):  # noqa: UP042
    """Alert severity levels"""

    CRITICAL = "critical"  # Immediate action required
    HIGH = "high"  # Action required soon
    MEDIUM = "medium"  # Should be addressed
    LOW = "low"  # Informational


class AlertChannel(str, Enum):  # noqa: UP042
    """Alert notification channels"""

    EMAIL = "email"
    SLACK = "slack"
    DISCORD = "discord"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"


@dataclass
class Alert:
    """Alert data structure"""

    alert_type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    provider: str | None = None
    model: str | None = None
    gateway: str | None = None
    metrics: dict[str, Any] | None = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)


class HealthAlertingService:
    """
    Service for sending health monitoring alerts

    Implements rate limiting, de-duplication, and multi-channel support.
    """

    def __init__(self):
        self.alert_history: dict[str, datetime] = {}  # For de-duplication
        self.alert_cooldown_minutes = 30  # Minimum time between same alerts
        self.enabled_channels: list[AlertChannel] = []

        # Load configuration
        self._load_config()

    def _load_config(self):
        """Load alerting configuration"""
        from src.config import Config

        # Determine which channels are configured
        if getattr(Config, "RESEND_API_KEY", None):
            self.enabled_channels.append(AlertChannel.EMAIL)

        # Add other channels as configured
        # if getattr(Config, "SLACK_WEBHOOK_URL", None):
        #     self.enabled_channels.append(AlertChannel.SLACK)

        logger.info(f"Health alerting enabled for channels: {self.enabled_channels}")

    async def send_alert(self, alert: Alert, channels: list[AlertChannel] | None = None):
        """
        Send an alert through configured channels

        Args:
            alert: Alert to send
            channels: Specific channels to use (defaults to all enabled)
        """
        try:
            # Check if we should send this alert (de-duplication)
            if not self._should_send_alert(alert):
                logger.debug(f"Skipping duplicate alert: {alert.title}")
                return

            # Use all enabled channels if none specified
            if channels is None:
                channels = self.enabled_channels

            # Send to each channel
            tasks = []
            for channel in channels:
                if channel in self.enabled_channels:
                    tasks.append(self._send_to_channel(alert, channel))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Record alert in history
            self._record_alert(alert)

            logger.info(f"Sent alert: {alert.title} (severity: {alert.severity.value})")

        except Exception as e:
            logger.error(f"Failed to send alert: {e}", exc_info=True)

    def _should_send_alert(self, alert: Alert) -> bool:
        """Check if alert should be sent (de-duplication logic)"""
        alert_key = f"{alert.alert_type}:{alert.provider}:{alert.model}:{alert.gateway}"

        if alert_key in self.alert_history:
            last_sent = self.alert_history[alert_key]
            cooldown = timedelta(minutes=self.alert_cooldown_minutes)

            if datetime.now(UTC) - last_sent < cooldown:
                return False

        return True

    def _record_alert(self, alert: Alert):
        """Record alert in history for de-duplication"""
        alert_key = f"{alert.alert_type}:{alert.provider}:{alert.model}:{alert.gateway}"
        self.alert_history[alert_key] = datetime.now(UTC)

        # Clean up old history (keep last 24 hours)
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        self.alert_history = {
            k: v for k, v in self.alert_history.items() if v > cutoff
        }

    async def _send_to_channel(self, alert: Alert, channel: AlertChannel):
        """Send alert to a specific channel"""
        try:
            if channel == AlertChannel.EMAIL:
                await self._send_email_alert(alert)
            elif channel == AlertChannel.SLACK:
                await self._send_slack_alert(alert)
            elif channel == AlertChannel.DISCORD:
                await self._send_discord_alert(alert)
            elif channel == AlertChannel.WEBHOOK:
                await self._send_webhook_alert(alert)
            elif channel == AlertChannel.PAGERDUTY:
                await self._send_pagerduty_alert(alert)

        except Exception as e:
            logger.error(f"Failed to send alert to {channel.value}: {e}")

    async def _send_email_alert(self, alert: Alert):
        """Send alert via email"""
        try:
            import resend

            from src.config import Config

            # Get admin email from config
            to_email = getattr(Config, "ADMIN_EMAIL", None) or getattr(Config, "SUPPORT_EMAIL", None)
            from_email = getattr(Config, "FROM_EMAIL", "noreply@gatewayz.ai")
            resend_api_key = getattr(Config, "RESEND_API_KEY", None)

            if not to_email:
                logger.warning("No admin email configured for alerts")
                return

            if not resend_api_key:
                logger.warning("No Resend API key configured for email alerts")
                return

            # Set Resend API key
            resend.api_key = resend_api_key

            # Format email
            subject = f"[{alert.severity.value.upper()}] {alert.title}"

            html_body = f"""
            <h2 style="color: {self._get_severity_color(alert.severity)};">{alert.title}</h2>

            <p><strong>Severity:</strong> {alert.severity.value.upper()}</p>
            <p><strong>Type:</strong> {alert.alert_type.value}</p>
            <p><strong>Time:</strong> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>

            {f'<p><strong>Provider:</strong> {alert.provider}</p>' if alert.provider else ''}
            {f'<p><strong>Model:</strong> {alert.model}</p>' if alert.model else ''}
            {f'<p><strong>Gateway:</strong> {alert.gateway}</p>' if alert.gateway else ''}

            <h3>Details</h3>
            <p>{alert.message}</p>

            {self._format_metrics_html(alert.metrics) if alert.metrics else ''}

            <hr>
            <p style="font-size: 12px; color: #666;">
                This is an automated alert from Gatewayz Health Monitoring System.
            </p>
            """

            # Send email using Resend
            resend.Emails.send({
                "from": from_email,
                "to": to_email,
                "subject": subject,
                "html": html_body,
            })

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")

    async def _send_slack_alert(self, alert: Alert):
        """Send alert to Slack"""
        try:
            import httpx

            from src.config import Config

            webhook_url = getattr(Config, "SLACK_WEBHOOK_URL", None)
            if not webhook_url:
                return

            # Format Slack message
            color = self._get_severity_color_slack(alert.severity)

            payload = {
                "attachments": [
                    {
                        "color": color,
                        "title": alert.title,
                        "text": alert.message,
                        "fields": [
                            {"title": "Severity", "value": alert.severity.value.upper(), "short": True},
                            {"title": "Type", "value": alert.alert_type.value, "short": True},
                        ],
                        "footer": "Gatewayz Health Monitoring",
                        "ts": int(alert.timestamp.timestamp()),
                    }
                ]
            }

            if alert.provider:
                payload["attachments"][0]["fields"].append(
                    {"title": "Provider", "value": alert.provider, "short": True}
                )

            if alert.model:
                payload["attachments"][0]["fields"].append(
                    {"title": "Model", "value": alert.model, "short": True}
                )

            async with httpx.AsyncClient() as client:
                response = await client.post(webhook_url, json=payload, timeout=10)
                response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")

    async def _send_discord_alert(self, alert: Alert):
        """Send alert to Discord"""
        try:
            import httpx

            from src.config import Config

            webhook_url = getattr(Config, "DISCORD_WEBHOOK_URL", None)
            if not webhook_url:
                return

            # Format Discord message
            color = self._get_severity_color_discord(alert.severity)

            embed = {
                "title": alert.title,
                "description": alert.message,
                "color": color,
                "timestamp": alert.timestamp.isoformat(),
                "fields": [
                    {"name": "Severity", "value": alert.severity.value.upper(), "inline": True},
                    {"name": "Type", "value": alert.alert_type.value, "inline": True},
                ],
                "footer": {"text": "Gatewayz Health Monitoring"},
            }

            if alert.provider:
                embed["fields"].append(
                    {"name": "Provider", "value": alert.provider, "inline": True}
                )

            if alert.model:
                embed["fields"].append(
                    {"name": "Model", "value": alert.model, "inline": True}
                )

            payload = {"embeds": [embed]}

            async with httpx.AsyncClient() as client:
                response = await client.post(webhook_url, json=payload, timeout=10)
                response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")

    async def _send_webhook_alert(self, alert: Alert):
        """Send alert to custom webhook"""
        try:
            import httpx

            from src.config import Config

            webhook_url = getattr(Config, "ALERT_WEBHOOK_URL", None)
            if not webhook_url:
                return

            payload = {
                "alert_type": alert.alert_type.value,
                "severity": alert.severity.value,
                "title": alert.title,
                "message": alert.message,
                "provider": alert.provider,
                "model": alert.model,
                "gateway": alert.gateway,
                "metrics": alert.metrics,
                "timestamp": alert.timestamp.isoformat(),
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(webhook_url, json=payload, timeout=10)
                response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")

    async def _send_pagerduty_alert(self, alert: Alert):
        """Send alert to PagerDuty"""
        try:
            import httpx

            from src.config import Config

            integration_key = getattr(Config, "PAGERDUTY_INTEGRATION_KEY", None)
            if not integration_key:
                return

            # Only send critical and high severity to PagerDuty
            if alert.severity not in [AlertSeverity.CRITICAL, AlertSeverity.HIGH]:
                return

            payload = {
                "routing_key": integration_key,
                "event_action": "trigger",
                "payload": {
                    "summary": alert.title,
                    "severity": alert.severity.value,
                    "source": "gatewayz-health-monitor",
                    "timestamp": alert.timestamp.isoformat(),
                    "custom_details": {
                        "message": alert.message,
                        "provider": alert.provider,
                        "model": alert.model,
                        "gateway": alert.gateway,
                        "metrics": alert.metrics,
                    },
                },
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                    timeout=10,
                )
                response.raise_for_status()

        except Exception as e:
            logger.error(f"Failed to send PagerDuty alert: {e}")

    def _get_severity_color(self, severity: AlertSeverity) -> str:
        """Get HTML color for severity"""
        colors = {
            AlertSeverity.CRITICAL: "#dc3545",  # Red
            AlertSeverity.HIGH: "#fd7e14",  # Orange
            AlertSeverity.MEDIUM: "#ffc107",  # Yellow
            AlertSeverity.LOW: "#17a2b8",  # Blue
        }
        return colors.get(severity, "#6c757d")

    def _get_severity_color_slack(self, severity: AlertSeverity) -> str:
        """Get Slack color for severity"""
        colors = {
            AlertSeverity.CRITICAL: "danger",
            AlertSeverity.HIGH: "warning",
            AlertSeverity.MEDIUM: "warning",
            AlertSeverity.LOW: "#17a2b8",
        }
        return colors.get(severity, "#6c757d")

    def _get_severity_color_discord(self, severity: AlertSeverity) -> int:
        """Get Discord color (decimal) for severity"""
        colors = {
            AlertSeverity.CRITICAL: 14495300,  # Red
            AlertSeverity.HIGH: 16612380,  # Orange
            AlertSeverity.MEDIUM: 16776960,  # Yellow
            AlertSeverity.LOW: 1552639,  # Blue
        }
        return colors.get(severity, 7107965)

    def _format_metrics_html(self, metrics: dict[str, Any]) -> str:
        """Format metrics dict as HTML"""
        if not metrics:
            return ""

        html = "<h4>Metrics</h4><ul>"
        for key, value in metrics.items():
            html += f"<li><strong>{key}:</strong> {value}</li>"
        html += "</ul>"
        return html

    # Convenience methods for common alerts

    async def alert_provider_down(
        self, provider: str, gateway: str, metrics: dict[str, Any] | None = None
    ):
        """Send alert for provider outage"""
        alert = Alert(
            alert_type=AlertType.PROVIDER_DOWN,
            severity=AlertSeverity.CRITICAL,
            title=f"Provider Down: {provider} ({gateway})",
            message=f"Provider {provider} on gateway {gateway} is completely offline. "
            f"All models from this provider are unavailable.",
            provider=provider,
            gateway=gateway,
            metrics=metrics,
        )
        await self.send_alert(alert)

    async def alert_critical_model_down(
        self,
        provider: str,
        model: str,
        gateway: str,
        consecutive_failures: int,
        metrics: dict[str, Any] | None = None,
    ):
        """Send alert for critical model outage"""
        alert = Alert(
            alert_type=AlertType.CRITICAL_MODEL_DOWN,
            severity=AlertSeverity.HIGH,
            title=f"Critical Model Down: {model}",
            message=f"Critical tier model {model} from {provider} ({gateway}) has failed "
            f"{consecutive_failures} consecutive health checks and is now offline.",
            provider=provider,
            model=model,
            gateway=gateway,
            metrics=metrics,
        )
        await self.send_alert(alert)

    async def alert_high_error_rate(
        self, provider: str, error_rate: float, metrics: dict[str, Any] | None = None
    ):
        """Send alert for high error rate"""
        alert = Alert(
            alert_type=AlertType.HIGH_ERROR_RATE,
            severity=AlertSeverity.MEDIUM,
            title=f"High Error Rate: {provider}",
            message=f"Provider {provider} is experiencing a high error rate of {error_rate:.1f}%. "
            f"Service may be degraded.",
            provider=provider,
            metrics=metrics,
        )
        await self.send_alert(alert)

    async def alert_slow_response(
        self,
        provider: str,
        avg_response_time: float,
        threshold: float,
        metrics: dict[str, Any] | None = None,
    ):
        """Send alert for slow response times"""
        alert = Alert(
            alert_type=AlertType.SLOW_RESPONSE,
            severity=AlertSeverity.LOW,
            title=f"Slow Response: {provider}",
            message=f"Provider {provider} average response time is {avg_response_time:.0f}ms, "
            f"exceeding threshold of {threshold:.0f}ms.",
            provider=provider,
            metrics=metrics,
        )
        await self.send_alert(alert)


# Global instance
health_alerting_service = HealthAlertingService()
