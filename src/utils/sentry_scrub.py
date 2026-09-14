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

# Header names whose values are credentials or the client's network address,
# never useful for debugging and never safe to send to a third party.
# Matched case-insensitively.
_SENSITIVE_HEADER_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        # Client-IP-bearing headers (proxies / CDNs) — threat model L5
        "x-forwarded-for",
        "x-real-ip",
        "x-client-ip",
        "cf-connecting-ip",
        "true-client-ip",
        "forwarded",
    }
)

# Exception message values are truncated defensively — even after upstream
# sanitization, an unbounded message could grow to include a large chunk of
# request-derived text.
_MAX_EXCEPTION_VALUE_LENGTH = 300


def _strip_sensitive_headers(headers: object) -> None:
    """Delete credential- and client-IP-bearing headers in place."""
    if not isinstance(headers, dict):
        return
    for key in list(headers.keys()):
        if isinstance(key, str) and key.lower() in _SENSITIVE_HEADER_KEYS:
            del headers[key]


def _strip_frame_locals(stacktrace: object) -> None:
    """Drop captured local variables from every frame of a stacktrace.

    Frame locals in a route handler are the request itself (Authorization
    header, client IP, parsed body with prompt/email). sentry_sdk.init is
    configured with include_local_variables=False so they are never
    collected; this is the independent second layer in case that option is
    ever lost or an integration attaches frames some other way.
    """
    if not isinstance(stacktrace, dict):
        return
    for frame in stacktrace.get("frames") or []:
        if isinstance(frame, dict):
            frame.pop("vars", None)


def strip_sensitive_event(event: dict, hint: dict) -> dict | None:
    """sentry_sdk before_send hook.

    Removes request bodies/cookies/auth-bearing headers, drops stack-frame
    local variables, and truncates exception messages. Never raises: a scrubbing failure must not crash event
    submission, and must not let an un-scrubbed event through — on any error
    the event is dropped (fail closed) rather than sent.
    """
    try:
        request = event.get("request")
        if isinstance(request, dict):
            request.pop("data", None)
            request.pop("cookies", None)
            _strip_sensitive_headers(request.get("headers"))

        # AutoSentryMiddleware sets its own "request" context on the scope;
        # it lands under contexts (not the top-level request) and never
        # passes through the SDK's header filter, so scrub it here too.
        contexts = event.get("contexts")
        if isinstance(contexts, dict):
            request_context = contexts.get("request")
            if isinstance(request_context, dict):
                _strip_sensitive_headers(request_context.get("headers"))

        exception = event.get("exception")
        if isinstance(exception, dict):
            for value in exception.get("values") or []:
                if not isinstance(value, dict):
                    continue
                if isinstance(value.get("value"), str):
                    value["value"] = value["value"][:_MAX_EXCEPTION_VALUE_LENGTH]
                _strip_frame_locals(value.get("stacktrace"))

        # Thread stack traces (attach_stacktrace / threads) carry frames too.
        threads = event.get("threads")
        if isinstance(threads, dict):
            for thread in threads.get("values") or []:
                if isinstance(thread, dict):
                    _strip_frame_locals(thread.get("stacktrace"))

        return event
    except Exception:
        logger.warning("Sentry before_send scrubbing failed; dropping event", exc_info=True)
        return None
