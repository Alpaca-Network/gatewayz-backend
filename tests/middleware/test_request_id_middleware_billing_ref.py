"""Tests for the server-minted billing_ref added to RequestIDMiddleware.

Threat model L7 (docs/security/ANONYMITY_THREAT_MODEL.md): the client-settable
X-Request-ID must never be the join key between billing rows and a request.
RequestIDMiddleware mints request.state.billing_ref server-side, independent of
any client-supplied header, and echoes it as X-Gatewayz-Request-Id.
"""

import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.middleware.request_id_middleware import RequestIDMiddleware

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _make_app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return {
            "request_id": request.state.request_id,
            "billing_ref": request.state.billing_ref,
        }

    return app


def _client():
    return TestClient(_make_app())


class TestBillingRefMinting:
    def test_billing_ref_is_set_and_is_a_uuid(self):
        response = _client().get("/echo")
        assert response.status_code == 200
        body = response.json()
        assert _UUID_RE.match(body["billing_ref"])

    def test_billing_ref_response_header_present_and_matches_state(self):
        response = _client().get("/echo")
        header_ref = response.headers.get("X-Gatewayz-Request-Id")
        assert header_ref is not None
        assert header_ref == response.json()["billing_ref"]

    def test_billing_ref_is_not_derived_from_client_request_id(self):
        """Client-supplied X-Request-ID must never equal, or seed, billing_ref."""
        client_supplied = "client-controlled-canary-id"
        response = _client().get("/echo", headers={"X-Request-ID": client_supplied})
        body = response.json()

        # The echoed X-Request-ID/request_id keeps client tracing semantics
        # (middleware normalizes with a "req_" prefix; see get_request_id docstring)...
        assert body["request_id"].endswith(client_supplied)
        # ...but billing_ref is an unrelated, server-minted UUID.
        assert body["billing_ref"] != client_supplied
        assert _UUID_RE.match(body["billing_ref"])

    def test_billing_ref_differs_between_requests(self):
        client = _client()
        first = client.get("/echo").json()["billing_ref"]
        second = client.get("/echo").json()["billing_ref"]
        assert first != second
