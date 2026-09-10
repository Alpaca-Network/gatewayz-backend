"""One email service for the whole backend (gatewayz-backend Phase A, A4).

Every previous email send site rolled its own Resend client: health
alerting called the ``resend`` SDK directly, the notification service kept
a second ``resend`` SDK instance with its own rate limiting, and the admin
panel had a third integration entirely. That meant "is email broken?" had
no single answer -- and it silently was: the live Resend key is currently
suspended, and nothing surfaced that except scattered warning logs.

This module is the one place that talks to Resend. It never raises --
callers (background tasks, request handlers) must never crash because an
email failed to send -- and it never logs a full email address, only a
masked form, so `grep`-ing logs can't leak who we emailed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0
_DEFAULT_FROM_EMAIL = "noreply@gatewayz.ai"


@dataclass
class EmailResult:
    """Outcome of a single send_email call. Never an exception."""

    sent: bool
    id: str | None = None
    error: str | None = None


def _mask_email(to: str) -> str:
    """Mask an email address for logging: ``a***@domain``.

    Never log a full recipient address -- see module docstring.
    """
    if not to or "@" not in to:
        return "***"
    local, _, domain = to.partition("@")
    first = local[0] if local else ""
    return f"{first}***@{domain}"


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    tags: list[dict[str, str]] | None = None,
) -> EmailResult:
    """Send one email via Resend. Never raises.

    - ``RESEND_API_KEY`` unset -> ``EmailResult(sent=False, error="not_configured")``.
    - Resend returns 401/403 (the key is suspended or invalid) ->
      ``error="suspended_or_invalid_key"`` -- this must stay visible, not be
      swallowed, since it has already happened once in production.
    - Any other failure (timeout, network error, non-2xx) -> a short,
      machine-readable error string; the exception class or HTTP status,
      never the full response body (which could echo back the recipient).
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning("send_email: RESEND_API_KEY not configured, skipping send")
        return EmailResult(sent=False, error="not_configured")

    from_email = os.environ.get("FROM_EMAIL", _DEFAULT_FROM_EMAIL)

    payload: dict[str, object] = {
        "from": from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if tags:
        payload["tags"] = tags

    try:
        response = httpx.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        logger.warning("send_email: timed out sending to %s", _mask_email(to))
        return EmailResult(sent=False, error="timeout")
    except httpx.HTTPError as e:
        logger.warning(
            "send_email: request failed sending to %s: %s", _mask_email(to), type(e).__name__
        )
        return EmailResult(sent=False, error=type(e).__name__)

    if response.status_code in (401, 403):
        logger.warning(
            "send_email: Resend rejected the API key (status=%s) sending to %s",
            response.status_code,
            _mask_email(to),
        )
        return EmailResult(sent=False, error="suspended_or_invalid_key")

    if response.status_code >= 400:
        logger.warning(
            "send_email: Resend returned status=%s sending to %s",
            response.status_code,
            _mask_email(to),
        )
        return EmailResult(sent=False, error=f"http_{response.status_code}")

    try:
        data = response.json()
    except ValueError:
        data = {}

    return EmailResult(sent=True, id=data.get("id"))


# --- Templates -----------------------------------------------------------
#
# Small functions that build the subject/html for one notification and hand
# it to send_email. Kept intentionally minimal -- richer HTML templates
# (branding, layout) live in src/services/professional_email_templates.py
# and are unaffected by this module.


def send_staff_invite(to: str, invite_link: str, role: str, invited_by_email: str) -> EmailResult:
    """Invite email for a new admin/superadmin staff member (Phase A, A2)."""
    subject = "You've been invited to Gatewayz staff"
    html = f"""
    <h2>You've been invited to Gatewayz staff</h2>
    <p>{invited_by_email} invited you to join Gatewayz as <strong>{role}</strong>.</p>
    <p><a href="{invite_link}">Accept invite</a></p>
    <p style="font-size: 12px; color: #666;">If you weren't expecting this, you can ignore this email.</p>
    """
    text = (
        f"You've been invited to Gatewayz staff as {role} by {invited_by_email}.\n"
        f"Accept: {invite_link}"
    )
    return send_email(
        to, subject, html, text=text, tags=[{"name": "category", "value": "staff_invite"}]
    )


def send_password_reset(to: str, link: str) -> EmailResult:
    """Password reset email (break-glass TOTP login path)."""
    subject = "Reset your Gatewayz password"
    html = f"""
    <h2>Reset your password</h2>
    <p>Click the link below to reset your password. This link expires in 1 hour.</p>
    <p><a href="{link}">Reset password</a></p>
    <p style="font-size: 12px; color: #666;">If you didn't request this, you can ignore this email.</p>
    """
    text = f"Reset your Gatewayz password: {link}\nThis link expires in 1 hour."
    return send_email(
        to, subject, html, text=text, tags=[{"name": "category", "value": "password_reset"}]
    )


def send_provider_approved(to: str, display_name: str) -> EmailResult:
    """Notify a GPU provider that their application was approved."""
    subject = "Your GPU provider application was approved"
    html = f"""
    <h2>You're approved!</h2>
    <p>Hi {display_name},</p>
    <p>Your GPU provider application has been approved. You can now register nodes and start earning.</p>
    """
    text = (
        f"Hi {display_name},\n\nYour GPU provider application has been approved. "
        "You can now register nodes and start earning."
    )
    return send_email(
        to, subject, html, text=text, tags=[{"name": "category", "value": "provider_approved"}]
    )
