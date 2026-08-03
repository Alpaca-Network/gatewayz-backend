"""Rate-limited operational alerting for provider-level failures.

Reuses EnhancedNotificationService's existing Resend email integration —
this is a separate ops channel to OPS_ALERT_EMAIL, not a user-facing
notification.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.config import Config
from src.enhanced_notification_service import EnhancedNotificationService

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

    def _send() -> None:
        try:
            service = EnhancedNotificationService()
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

    # NotificationService.send_email_notification() makes a synchronous,
    # un-timed HTTP call to Resend. This function is reached from the async
    # request-handling path (dispatch_streaming -> map_provider_error), so
    # run the blocking send in a worker thread instead of stalling the event
    # loop for every concurrent request during an auth-failure storm. Falls
    # back to an inline (blocking) send when there's no running event loop
    # (e.g. sync callers/tests) so the alert is still attempted.
    try:
        asyncio.get_running_loop()
        asyncio.create_task(asyncio.to_thread(_send))
    except RuntimeError:
        _send()
