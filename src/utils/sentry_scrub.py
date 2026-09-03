"""Sentry before_send hook: strip PII, bodies, and secrets from outgoing events.

Threat model G5 (docs/security/ANONYMITY_THREAT_MODEL.md): Gatewayz's own error
tooling must not be able to re-link content to identity. Sentry receives no
request/response bodies, no cookies, no auth-bearing headers, and only bounded
exception text. Paired with sentry_sdk.init(send_default_pii=False) in main.py
and the user-context/tag changes in auto_sentry_middleware.py (no email, no
client IP).
"""

import logging

logger = logging.getLogger(__name__)

# Header names whose values are credentials, never useful for debugging and
# never safe to send to a third party. Matched case-insensitively.
_SENSITIVE_HEADER_KEYS = frozenset({"authorization", "cookie", "x-api-key"})

# Exception message values are truncated defensively — even after upstream
# sanitization, an unbounded message could grow to include a large chunk of
# request-derived text.
_MAX_EXCEPTION_VALUE_LENGTH = 300


def strip_sensitive_event(event: dict, hint: dict) -> dict | None:
    """sentry_sdk before_send hook.

    Removes request bodies/cookies/auth-bearing headers and truncates exception
    messages. Never raises: a scrubbing failure must not crash event
    submission, and must not let an un-scrubbed event through — on any error
    the event is dropped (fail closed) rather than sent.
    """
    try:
        request = event.get("request")
        if isinstance(request, dict):
            request.pop("data", None)
            request.pop("cookies", None)
            headers = request.get("headers")
            if isinstance(headers, dict):
                for key in list(headers.keys()):
                    if key.lower() in _SENSITIVE_HEADER_KEYS:
                        del headers[key]

        exception = event.get("exception")
        if isinstance(exception, dict):
            for value in exception.get("values") or []:
                if isinstance(value, dict) and isinstance(value.get("value"), str):
                    value["value"] = value["value"][:_MAX_EXCEPTION_VALUE_LENGTH]

        return event
    except Exception:
        logger.warning("Sentry before_send scrubbing failed; dropping event", exc_info=True)
        return None
