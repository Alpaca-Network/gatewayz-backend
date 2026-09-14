"""End-to-end Sentry privacy round-trip (threat model G5 / L5, M3 follow-up).

The unit layers already exist: tests/utils/test_sentry_scrub.py covers the
before_send hook and tests/middleware/test_auto_sentry_middleware_privacy.py
covers AutoSentryMiddleware's context extraction. What neither proves is
what actually leaves the process. This file drives the REAL sentry_sdk
pipeline:

    sentry_sdk.init(**build_sentry_init_kwargs(dsn=<dummy>, transport=<capture>))
      -> TestClient request through RequestIDMiddleware + AutoSentryMiddleware
         (the app's own middleware classes, nested as create_app() nests them)
      -> route raises
      -> SDK integrations (Starlette/FastAPI request extractors) enrich the event
      -> before_send (strip_sensitive_event)
      -> transport receives the serialized envelope

and asserts on the FINAL serialized event bytes the transport receives:
no API key (full or prefix), no client IP, no email, no request body or
prompt text — while the billing_ref tag and the error type/message survive.

Sentinels follow the threat model's canary vocabulary (§5/§6).
"""

from __future__ import annotations

import json

import pytest
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from starlette.middleware.base import BaseHTTPMiddleware

from src.middleware.auto_sentry_middleware import AutoSentryMiddleware
from src.middleware.request_id_middleware import RequestIDMiddleware
from src.utils.sentry_init import build_sentry_init_kwargs

# --- Sentinels ---------------------------------------------------------------

SENTINEL_USER_ID = 424242
SENTINEL_EMAIL = "canary-424242@example.test"
SENTINEL_API_KEY = "gw_live_CANARY424242xyzsentinelkey"
SENTINEL_API_KEY_PREFIX = SENTINEL_API_KEY[:12]
SENTINEL_IP = "203.0.113.77"
SENTINEL_PROMPT = "canary prompt fragment 424242"
CLIENT_REQUEST_ID = "client-controlled-canary-id"

ERROR_TYPE = "RuntimeError"
ERROR_MESSAGE = "upstream provider exploded (canary error message)"

# Never resolves; the capturing transport below means nothing is sent anyway.
DUMMY_DSN = "https://0123456789abcdef0123456789abcdef@o0.ingest.invalid/1"


# --- Capturing transport ------------------------------------------------------


class _CapturingTransport(Transport):
    """Drop-in for the HTTP transport: keeps every envelope in memory."""

    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def flush(self, timeout: float, callback=None) -> None:  # noqa: ARG002
        return None

    def kill(self) -> None:
        return None

    def error_events(self) -> list[dict]:
        """Every error event, decoded from the serialized envelope payload —
        i.e. exactly the bytes Sentry's ingest would have received."""
        events = []
        for envelope in self.envelopes:
            for item in envelope.items:
                if item.type == "event":
                    events.append(json.loads(item.payload.get_bytes()))
        return events


@pytest.fixture
def sentry_transport():
    """Initialise sentry_sdk with the app's real options and a capturing transport."""
    transport = _CapturingTransport()
    kwargs = build_sentry_init_kwargs(dsn=DUMMY_DSN, transport=transport)
    # Guard the premise of the test: the production options really are the
    # privacy-preserving ones (a regression here would silently weaken G5).
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    assert kwargs["before_send"].__name__ == "strip_sensitive_event"

    sentry_sdk.init(**kwargs)
    try:
        yield transport
    finally:
        sentry_sdk.flush()
        sentry_sdk.get_client().close(timeout=0)
        # Detach so later tests in this worker see the SDK as un-initialised.
        sentry_sdk.get_global_scope().set_client(None)


class _AuthStandInMiddleware(BaseHTTPMiddleware):
    """Stand-in for the auth layer: attaches identity (including the email
    the threat model forbids Sentry from seeing) to request.state so the
    AutoSentryMiddleware below it has the chance to leak it."""

    async def dispatch(self, request: Request, call_next):
        request.state.user_id = SENTINEL_USER_ID
        request.state.email = SENTINEL_EMAIL
        return await call_next(request)


def _build_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    seen: dict = {}

    @app.post("/v1/chat/completions")
    async def boom(request: Request):
        body = await request.json()
        assert body["messages"][0]["content"] == SENTINEL_PROMPT  # body really arrived
        # On the error path the X-Gatewayz-Request-Id response header is never
        # written (the exception unwinds past RequestIDMiddleware), so record
        # the server-minted ref here to compare against the Sentry tag.
        seen["billing_ref"] = request.state.billing_ref
        raise RuntimeError(ERROR_MESSAGE)

    # Starlette runs the LAST-added middleware first. create_app() adds
    # AutoSentry before RequestID, so RequestID is outer (mints billing_ref
    # before AutoSentry reads scope state); the auth stand-in sits between.
    app.add_middleware(AutoSentryMiddleware)
    app.add_middleware(_AuthStandInMiddleware)
    app.add_middleware(RequestIDMiddleware)
    return app, seen


def _trigger_error(app_and_seen: tuple[FastAPI, dict]) -> str:
    app, seen = app_and_seen
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {SENTINEL_API_KEY}",
            "X-Forwarded-For": SENTINEL_IP,
            "X-Real-IP": SENTINEL_IP,
            "X-Request-ID": CLIENT_REQUEST_ID,
        },
        json={
            "model": "gpt-4o",
            "user": SENTINEL_EMAIL,
            "messages": [{"role": "user", "content": SENTINEL_PROMPT}],
        },
    )
    assert response.status_code == 500
    sentry_sdk.flush()
    billing_ref = seen["billing_ref"]
    assert billing_ref and CLIENT_REQUEST_ID not in billing_ref
    return billing_ref


# --- Tests --------------------------------------------------------------------


class TestSentryScrubRoundTrip:
    def test_final_event_carries_no_identity_and_no_content(self, sentry_transport):
        billing_ref = _trigger_error(_build_app())

        events = sentry_transport.error_events()
        assert events, "the exception never reached the transport"

        for event in events:
            serialized = json.dumps(event)

            # Identity / secrets must be absent from the *entire* payload.
            assert SENTINEL_API_KEY not in serialized
            assert SENTINEL_API_KEY_PREFIX not in serialized
            assert SENTINEL_IP not in serialized
            assert SENTINEL_EMAIL not in serialized
            # Content must be absent: no request body, no prompt text.
            assert SENTINEL_PROMPT not in serialized
            assert "data" not in event.get("request", {})
            # No stack-frame locals anywhere (they would carry all of the above).
            for value in event.get("exception", {}).get("values", []):
                for frame in value.get("stacktrace", {}).get("frames", []):
                    assert "vars" not in frame
            # The client-settable X-Request-ID is never a Sentry correlator
            # (it may appear as a plain request header, but not as a tag).
            assert CLIENT_REQUEST_ID not in json.dumps(event.get("tags", {}))

    def test_final_event_keeps_billing_ref_and_error(self, sentry_transport):
        billing_ref = _trigger_error(_build_app())

        events = sentry_transport.error_events()
        assert events, "the exception never reached the transport"

        # The server-minted billing ref is the one correlator support may use.
        assert any(event.get("tags", {}).get("billing_ref") == billing_ref for event in events)

        # And the error itself is still debuggable: type + message survive.
        exception_values = [
            value for event in events for value in event.get("exception", {}).get("values", [])
        ]
        assert any(
            value.get("type") == ERROR_TYPE and value.get("value") == ERROR_MESSAGE
            for value in exception_values
        )

    def test_user_context_is_id_only(self, sentry_transport):
        _trigger_error(_build_app())

        events = sentry_transport.error_events()
        assert events

        users = [event["user"] for event in events if event.get("user")]
        assert users, "AutoSentryMiddleware should attach the numeric user id"
        for user in users:
            assert set(user.keys()) <= {"id"}
            assert user["id"] == SENTINEL_USER_ID
