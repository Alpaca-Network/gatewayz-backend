"""Customer webhooks for job and Verify case state changes (PRD: "HMAC-signed").

Delivery: POST JSON with
    X-Gatewayz-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, f"{t}." + body)>
    X-Gatewayz-Event: <event type>
Receivers recompute the HMAC over the RAW body and reject stale timestamps.
Targets pass the same SSRF fence as budget webhooks at registration AND at send
time (DNS can change in between). Up to 3 attempts; an endpoint failing 20
deliveries in a row is switched off rather than retried forever.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from typing import Any

import httpx

from src.utils.ssrf_guard import PinnedPublicIPTransport

logger = logging.getLogger(__name__)

EVENTS = frozenset({"job.closed", "verify.case.updated"})
MAX_CONSECUTIVE_FAILURES = 20
_BACKOFF_S = (0.0, 2.0, 8.0)


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, ts: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(secret: str, header: str, body: bytes, tolerance_s: int = 300) -> bool:
    """Reference receiver check (also what the docs tell customers to do)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except Exception:
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, ts, body), header)


def build_event(event_type: str, data: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "id": f"evt_{uuid.uuid4().hex}",
            "type": event_type,
            "created_at": int(time.time()),
            "data": data,
        },
        separators=(",", ":"),
        default=str,
    ).encode()


def _deliver_one(
    hook: dict, secret: str, event_type: str, body: bytes, sleep=time.sleep
) -> int | None:
    status = None
    for delay in _BACKOFF_S:
        if delay:
            sleep(delay)
        ts = int(time.time())
        try:
            with httpx.Client(transport=PinnedPublicIPTransport(), timeout=10.0) as c:
                r = c.post(
                    hook["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Gatewayz-Event": event_type,
                        "X-Gatewayz-Signature": sign(secret, ts, body),
                        "User-Agent": "gatewayz-webhooks",
                    },
                )
            status = r.status_code
            if 200 <= status < 300:
                return status
            if 400 <= status < 500 and status not in (408, 429):
                return status  # the receiver refused it; retrying won't change that
        except Exception as e:
            logger.info("webhook %s delivery error: %s", hook.get("id"), type(e).__name__)
            status = None
    return status


def emit(user_id: int, event_type: str, data: dict[str, Any], sleep=time.sleep) -> int:
    """Deliver an event to every active hook of user_id subscribed to it. Blocking —
    callers run it in a background task/thread. Returns the number delivered."""
    from src.db.outbound_webhooks import hooks_for, record_attempt
    from src.utils.crypto import decrypt_api_key

    if event_type not in EVENTS:
        raise ValueError(f"unknown event {event_type}")
    try:
        hooks = hooks_for(user_id, event_type)
    except Exception as e:  # a webhook outage must never fail the job/case it reports on
        logger.error("webhook lookup failed for user %s event %s: %s", user_id, event_type, e)
        return 0
    delivered = 0
    for hook in hooks:
        try:
            secret = decrypt_api_key(hook["secret_enc"], hook.get("key_version"))
        except Exception as e:
            logger.error("webhook %s secret undecryptable: %s", hook["id"], e)
            continue
        body = build_event(event_type, data)
        status = _deliver_one(hook, secret, event_type, body, sleep=sleep)
        ok = status is not None and 200 <= status < 300
        delivered += ok
        record_attempt(hook, ok, status, MAX_CONSECUTIVE_FAILURES)
    return delivered
