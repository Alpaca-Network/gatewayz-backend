"""The upstream identity firewall.

Every outbound call to a model provider goes through ``scrub_upstream_kwargs``
exactly once, at the point where the provider-facing kwargs/payload dict is
assembled (``ChatInferenceHandler`` in ``src/handlers/chat_handler.py`` for
chat/messages; ``routes/embeddings.py`` for embeddings). This is the single
scrubbing boundary described by G1 in ``docs/security/ANONYMITY_THREAT_MODEL.md``:
no client-supplied identity field reaches a provider, on any route, no matter
which of the 17 provider clients ends up serving the request.

Nothing here inspects message *content* -- if a client puts their name in the
prompt, the provider still sees it (see N2 in the threat model). This module
only strips the side-channel fields client SDKs use for abuse-monitoring
correlation (OpenAI's ``user``, Anthropic's ``metadata.user_id`` -- which is
built from the same ``user`` value downstream, see
``anthropic_native_client.py``) plus anything a client tries to smuggle
through OpenAI-SDK passthrough kwargs (``metadata``, ``extra_body``,
``extra_headers``, ``extra_query``).
"""

from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Any

from src.config import Config

# Client-supplied passthrough fields that must never reach a provider.
# `user` is OpenAI's abuse-monitoring identifier; `metadata`/`extra_body`/
# `extra_headers`/`extra_query` are OpenAI-SDK passthrough kwargs a client
# could otherwise use to smuggle arbitrary identity data into the request.
OUTBOUND_DENY_FIELDS = frozenset({"user", "metadata", "extra_body", "extra_headers", "extra_query"})

# Header names that would carry Gatewayz-side identity if a provider client
# ever forwarded them. No provider client builds these into an outbound
# request today (research.md §1: each client constructs its headers from
# scratch, never from the inbound Request) -- this is the regression guard
# tests/security/test_upstream_identity_firewall.py checks outbound headers
# against, kept here so it's a single, documented list rather than an
# ad-hoc one buried in the test.
FORBIDDEN_OUTBOUND_HEADER_NAMES = frozenset(
    {
        "x-forwarded-for",
        "x-real-ip",
        "cookie",
        "x-request-id",
        "x-correlation-id",
        "x-gatewayz-user-id",
        "x-gatewayz-api-key",
    }
)


def scrub_upstream_kwargs(
    kwargs: dict[str, Any], *, billing_ref: str | None = None
) -> dict[str, Any]:
    """Return a copy of ``kwargs`` safe to send to any model provider.

    Drops every key in ``OUTBOUND_DENY_FIELDS``. If ``Config.UPSTREAM_ABUSE_PSEUDONYM``
    is enabled and a ``billing_ref`` is given, sets ``user`` to an unlinkable
    per-request pseudonym derived from it (never the client's value, never the
    client-settable request id). Never mutates the input dict.
    """
    scrubbed = {k: v for k, v in kwargs.items() if k not in OUTBOUND_DENY_FIELDS}

    if Config.UPSTREAM_ABUSE_PSEUDONYM and billing_ref:
        scrubbed["user"] = pseudonym(billing_ref)

    return scrubbed


def pseudonym(billing_ref: str) -> str:
    """Derive an unlinkable per-request pseudonym from ``billing_ref``.

    ``gw_`` + the first 16 hex chars of HMAC-SHA256(secret, billing_ref). Two
    different ``billing_ref`` values always produce different pseudonyms, and
    the pseudonym cannot be reversed to ``billing_ref`` without the secret.
    """
    secret = Config.UPSTREAM_PSEUDONYM_SECRET
    if not secret:
        raise ValueError(
            "UPSTREAM_PSEUDONYM_SECRET must be set when UPSTREAM_ABUSE_PSEUDONYM is enabled"
        )
    digest = hmac.new(secret.encode(), billing_ref.encode(), sha256).hexdigest()
    return f"gw_{digest[:16]}"
