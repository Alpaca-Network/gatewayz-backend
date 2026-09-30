"""Rate-limited operational alerting for provider-level failures.

Emails go out through ``src.services.email.send_email`` (Resend). Recipients:

1. ``OPS_ALERT_EMAIL`` (comma-separated allowed) when set;
2. otherwise the emails of active ``admin``/``superadmin`` staff. Prod ran with
   OPS_ALERT_EMAIL unset, so alerts silently never fired and Anthropic ran out of
   credit unnoticed -- the fallback exists so that mistake can't repeat.

If neither yields a recipient the alert is a safe no-op that logs a WARNING (once
per cooldown). A Sentry event is captured regardless of email. State is visible
on ``GET /admin/status`` via ``ops_alerts_status()``.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time

from src.config import Config

logger = logging.getLogger(__name__)

_ALERT_COOLDOWN_SECONDS = 15 * 60
_BUDGET_ALERT_COOLDOWN_SECONDS = 60 * 60
_RECIPIENT_CACHE_TTL_SECONDS = 5 * 60

# (kind, provider) -> last alert unix timestamp. In-process only (per-instance);
# acceptable because the goal is "don't spam", not exactly-once delivery.
_last_alert_sent_at: dict[str, float] = {}
_recipient_cache: tuple[float, list[str]] | None = None


def _configured_recipients() -> list[str]:
    raw = Config.OPS_ALERT_EMAIL or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def _staff_recipients() -> list[str]:
    """Emails of active admin/superadmin staff. Cached briefly; [] on any failure."""
    global _recipient_cache
    now = time.time()
    if _recipient_cache and (now - _recipient_cache[0]) < _RECIPIENT_CACHE_TTL_SECONDS:
        return list(_recipient_cache[1])
    try:
        from src.db.staff import list_staff

        emails = sorted(
            {
                str(row["email"]).strip()
                for row in list_staff()
                if row.get("email") and row.get("is_active") is not False
            }
        )
    except Exception as e:  # noqa: BLE001 - alerting must never break the request path
        logger.warning("Could not load staff emails for ops alert fallback: %s", e)
        return []
    _recipient_cache = (now, emails)
    return list(emails)


def resolve_ops_recipients() -> tuple[list[str], str]:
    """(recipients, source) where source is 'env' | 'staff' | 'none'."""
    configured = _configured_recipients()
    if configured:
        return configured, "env"
    staff = _staff_recipients()
    return staff, ("staff" if staff else "none")


def ops_alerts_status() -> dict[str, object]:
    """For GET /admin/status: is anyone going to be told? Never exposes addresses."""
    recipients, source = resolve_ops_recipients()
    return {"configured": bool(recipients), "recipients": len(recipients), "source": source}


def _dispatch(
    kind: str,
    provider: str,
    subject: str,
    html_body: str,
    text_body: str,
    model: str | None,
    error_detail: str,
    cooldown: float,
) -> None:
    key = f"{kind}:{provider}"
    now = time.time()
    last_sent = _last_alert_sent_at.get(key)
    if last_sent is not None and (now - last_sent) < cooldown:
        logger.debug(
            "Suppressing duplicate %s alert for %s (%.0fs ago)", kind, provider, now - last_sent
        )
        return
    # Claim the slot up front so concurrent failures don't all send.
    _last_alert_sent_at[key] = now

    def _send() -> None:
        try:
            try:
                from src.utils.sentry_context import capture_provider_error

                capture_provider_error(
                    RuntimeError(f"{kind}: {provider}: {error_detail[:200]}"),
                    provider=provider,
                    model=model,
                    extra_context={"alert_kind": kind},
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Sentry capture for ops alert failed: %s", e)

            recipients, _source = resolve_ops_recipients()
            if not recipients:
                logger.warning(
                    "OPS ALERT (%s, provider=%s) has no recipient: set OPS_ALERT_EMAIL or "
                    "add an active admin/superadmin. Detail: %s",
                    kind,
                    provider,
                    error_detail[:200],
                )
                return

            from src.services.email import send_email

            any_sent = False
            for to in recipients:
                result = send_email(to=to, subject=subject, html=html_body, text=text_body)
                any_sent = any_sent or bool(result.sent)
            if not any_sent:
                logger.error("Ops alert (%s, provider=%s) email send failed", kind, provider)
                _last_alert_sent_at.pop(key, None)  # allow a retry on the next failure
        except Exception as e:  # noqa: BLE001 - alerting must never break the request path
            logger.error("Failed to send provider ops alert: %s", e)
            _last_alert_sent_at.pop(key, None)

    # send_email() makes a synchronous HTTP call; this is reached from the async
    # request path, so offload to a worker thread. Falls back to an inline send
    # when there is no running loop (sync callers/tests).
    try:
        asyncio.get_running_loop()
        asyncio.create_task(asyncio.to_thread(_send))
    except RuntimeError:
        _send()


def alert_provider_auth_failure(provider: str, model: str, error_detail: str) -> None:
    """Alert ops when a provider rejects our credentials (once per provider per 15 min)."""
    detail = html.escape(error_detail or "")
    _dispatch(
        "auth_failure",
        provider,
        f"[Gatewayz] {provider} authentication failure",
        (
            f"<p>Provider <b>{html.escape(provider)}</b> rejected a request for model "
            f"<b>{html.escape(model)}</b>:</p><pre>{detail}</pre>"
            "<p>This usually means the provider's API key was rotated/revoked. "
            "Check Railway env vars for the corresponding key.</p>"
        ),
        f"Provider {provider} rejected a request for model {model}: {error_detail}\n"
        "This usually means the provider's API key was rotated/revoked.",
        model,
        error_detail or "",
        _ALERT_COOLDOWN_SECONDS,
    )


def alert_provider_budget_exhausted(provider: str, reason: str, model: str | None) -> None:
    """Alert ops when one of OUR provider accounts is out of credit/quota (hourly)."""
    _dispatch(
        "budget_exhausted",
        provider,
        f"[Gatewayz] {provider} credit/quota exhausted",
        (
            f"<p>Provider <b>{html.escape(provider)}</b> is refusing requests: "
            f"<b>{html.escape(reason)}</b> (e.g. model {html.escape(model or 'unknown')}).</p>"
            "<p>Top up the provider account. Users only see a generic capacity message.</p>"
        ),
        f"Provider {provider} is refusing requests: {reason} (model {model}). "
        "Top up the provider account.",
        model,
        reason,
        _BUDGET_ALERT_COOLDOWN_SECONDS,
    )
