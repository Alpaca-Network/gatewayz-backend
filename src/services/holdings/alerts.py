"""Deduped ops-alert delivery.

The holdings sweep checks that used to live here (recorded-nothing and
staleness alerts, the /admin/status sweep block) are retired and removed
with holdings rewards. What remains is the delivery path, which the
delegated-staking alerts (src/services/delegation/alerts.py) import as
``_send``.

Each condition alerts at most once per ``HOLDINGS_ALERT_COOLDOWN_HOURS``,
claimed with a Redis ``SET NX EX`` so several API instances send one email
between them, with an in-process fallback when Redis is down. Delivery reuses
the provider-alert path (``resolve_ops_recipients`` + ``send_email``), and
every alert is also logged at ERROR, which Sentry's logging integration
captures -- so with no recipient configured the alert is still one ERROR,
never silence.

Callers pass counts, names and timestamps only: never a wallet address, an
RPC URL or a key.
"""

from __future__ import annotations

import html
import logging
import time

from src.config.config import Config
from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

_DEDUPE_KEY_PREFIX = "ops:alert:holdings:"

# condition -> unix time its in-process claim expires. Used only when Redis is
# unavailable; per-instance, like every other fallback store in this codebase.
_fallback_claims: dict[str, float] = {}


def _cooldown_seconds() -> int:
    return max(60, int(float(Config.HOLDINGS_ALERT_COOLDOWN_HOURS) * 3600))


def _claim(condition: str) -> bool:
    """True if this caller won the right to send ``condition`` now."""
    ttl = _cooldown_seconds()
    client = None
    try:
        client = get_redis_client()
    except Exception as e:  # noqa: BLE001 - fall back to the in-process store
        logger.debug("holdings alerts: Redis client unavailable: %s", e)

    if client is not None:
        try:
            return bool(client.set(f"{_DEDUPE_KEY_PREFIX}{condition}", "1", nx=True, ex=ttl))
        except Exception as e:  # noqa: BLE001
            logger.warning("holdings alerts: Redis dedupe failed, using in-process: %s", e)

    now = time.time()
    if _fallback_claims.get(condition, 0.0) > now:
        return False
    _fallback_claims[condition] = now + ttl
    return True


def _release(condition: str) -> None:
    """Give the claim back so the next occurrence can retry delivery."""
    _fallback_claims.pop(condition, None)
    try:
        client = get_redis_client()
        if client is not None:
            client.delete(f"{_DEDUPE_KEY_PREFIX}{condition}")
    except Exception as e:  # noqa: BLE001
        logger.debug("holdings alerts: Redis release failed: %s", e)


def _send(condition: str, subject: str, lines: list[str]) -> bool:
    """Deliver one deduped ops alert. True if it was sent (or logged with no
    recipient); False if suppressed by the cooldown. Never raises."""
    try:
        if not _claim(condition):
            logger.debug("holdings alerts: %s suppressed by cooldown", condition)
            return False

        text = "\n".join(lines)
        # Always one ERROR: it is the Sentry event, and the only signal at all
        # when no recipient is configured.
        logger.error("HOLDINGS OPS ALERT [%s] %s | %s", condition, subject, " | ".join(lines))

        from src.services.provider_alerting import resolve_ops_recipients

        recipients, _source = resolve_ops_recipients()
        if not recipients:
            logger.error(
                "HOLDINGS OPS ALERT [%s] has no recipient: set OPS_ALERT_EMAIL on the api "
                "service (or add an active admin/superadmin)",
                condition,
            )
            return True

        from src.services.email import send_email

        body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
        any_sent = False
        for to in recipients:
            result = send_email(to=to, subject=subject, html=body, text=text)
            any_sent = any_sent or bool(result.sent)
        if not any_sent:
            logger.error("HOLDINGS OPS ALERT [%s] email delivery failed", condition)
            _release(condition)
        return True
    except Exception as e:  # noqa: BLE001 - alerting must never break the job
        logger.error("holdings alerts: failed to send %s: %s", condition, e)
        return False


def _reset_for_tests() -> None:
    _fallback_claims.clear()
