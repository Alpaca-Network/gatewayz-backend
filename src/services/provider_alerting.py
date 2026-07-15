"""Rate-limited operational alerting for provider-level failures.

Reuses NotificationService's existing Resend email integration — this is
NOT the user-facing notification system (src/schemas/notification.py
NotificationType enum), it's a separate ops channel to OPS_ALERT_EMAIL.
"""

from __future__ import annotations

import logging
import time

from src.config import Config
from src.services.notification import NotificationService

logger = logging.getLogger(__name__)

_ALERT_COOLDOWN_SECONDS = 15 * 60

# provider name -> last alert unix timestamp. In-process only (per-instance);
# acceptable because the goal is "don't spam", not exactly-once delivery.
_last_alert_sent_at: dict[str, float] = {}


def alert_provider_auth_failure(provider: str, model: str, error_detail: str) -> None:
    """Send an ops email when a provider rejects our credentials.

    Rate-limited to one email per provider per 15 minutes. No-op if
    OPS_ALERT_EMAIL is not configured.
    """
    if not Config.OPS_ALERT_EMAIL:
        return

    now = time.time()
    last_sent = _last_alert_sent_at.get(provider)
    if last_sent is not None and (now - last_sent) < _ALERT_COOLDOWN_SECONDS:
        logger.debug(
            "Suppressing duplicate provider auth alert for %s (last sent %.0fs ago)",
            provider,
            now - last_sent,
        )
        return

    try:
        service = NotificationService()
        sent = service.send_email_notification(
            to_email=Config.OPS_ALERT_EMAIL,
            subject=f"[Gatewayz] {provider} authentication failure",
            html_content=(
                f"<p>Provider <b>{provider}</b> rejected a request for model "
                f"<b>{model}</b>:</p><pre>{error_detail}</pre>"
                f"<p>This usually means the provider's API key was rotated/revoked. "
                f"Check Railway env vars for the corresponding key.</p>"
            ),
            text_content=(
                f"Provider {provider} rejected a request for model {model}: {error_detail}\n"
                f"This usually means the provider's API key was rotated/revoked."
            ),
        )
        if sent:
            _last_alert_sent_at[provider] = now
    except Exception as e:  # noqa: BLE001 - alerting must never break the request path
        logger.error("Failed to send provider auth failure alert: %s", e)
