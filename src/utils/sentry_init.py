"""Sentry SDK init options — the single source of truth for how Gatewayz
configures ``sentry_sdk.init``.

main.py calls ``sentry_sdk.init(**build_sentry_init_kwargs())`` at import
time when Sentry is enabled. Tests reuse the exact same options (overriding
only ``dsn``/``transport``) so the privacy guarantees of threat model G5
(docs/security/ANONYMITY_THREAT_MODEL.md: no email, no client IP, no
request/response bodies, no API keys in anything Sentry receives) are
verified against the real SDK pipeline, not a copy of its configuration.
"""

from __future__ import annotations

import os
from typing import Any

from src.config import Config


def sentry_traces_sampler(sampling_context: dict) -> float:
    """
    Adaptive sampling to control Sentry costs while maintaining visibility.

    Sampling strategy:
    - Development: 100% (all requests)
    - Health/metrics endpoints: 0% (skip monitoring endpoints)
    - Critical endpoints: 20% (chat)
    - Other endpoints: 10%
    - Errors: Always sampled (parent_sampled)
    """
    # Always sample errors
    if sampling_context.get("parent_sampled") is not None:
        return 1.0

    # 100% sampling in development
    if Config.SENTRY_ENVIRONMENT == "development":
        return 1.0

    # Get endpoint path
    endpoint = ""
    if "wsgi_environ" in sampling_context:
        endpoint = sampling_context["wsgi_environ"].get("PATH_INFO", "")
    elif "asgi_scope" in sampling_context:
        endpoint = sampling_context["asgi_scope"].get("path", "")

    # Skip health check and monitoring endpoints (0%)
    if endpoint in ["/health", "/metrics", "/api/health", "/api/monitoring/health"]:
        return 0.0

    # Critical inference endpoints: 20% sampling
    if endpoint in ["/v1/chat/completions", "/v1/images/generations"]:
        return 0.2

    # Admin endpoints: 50% sampling (important but lower volume)
    if endpoint.startswith("/api/admin"):
        return 0.5

    # All other endpoints: 10% sampling
    return 0.1


def build_sentry_init_kwargs(**overrides: Any) -> dict[str, Any]:
    """Build the keyword arguments Gatewayz passes to ``sentry_sdk.init``.

    ``overrides`` are applied last, so a test can substitute ``dsn`` and
    ``transport`` while keeping every privacy-relevant option
    (``send_default_pii``, ``before_send``) exactly as production uses them.
    """
    from src.utils.sentry_scrub import strip_sensitive_event

    on_vercel = bool(os.getenv("VERCEL"))
    profiles_rate = 0.0 if on_vercel else float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.05"))

    kwargs: dict[str, Any] = {
        "dsn": Config.SENTRY_DSN,
        # Threat model G5: Gatewayz's own error tooling must not be able to
        # re-link content to identity. send_default_pii would attach the raw
        # client IP and other PII the SDK collects automatically; we set our
        # own minimal, deliberate context in AutoSentryMiddleware instead.
        # before_send is a second, independent layer that strips request
        # bodies/cookies/auth headers and bounds exception text.
        "send_default_pii": False,
        # The SDK defaults to serialising every stack frame's local variables
        # into the event. In a route handler those locals ARE the request:
        # the Authorization header, the client IP, the parsed body (prompt,
        # `user` email) — every G5 forbidden item, regardless of
        # send_default_pii. Caught by tests/security/test_sentry_scrub_roundtrip.py.
        "include_local_variables": False,
        "before_send": strip_sensitive_event,
        "environment": Config.SENTRY_ENVIRONMENT,
        "release": Config.SENTRY_RELEASE,
        "profiles_sample_rate": profiles_rate,
    }
    if on_vercel:
        kwargs["traces_sample_rate"] = 0.0
    else:
        kwargs["traces_sampler"] = sentry_traces_sampler

    kwargs.update(overrides)
    return kwargs
