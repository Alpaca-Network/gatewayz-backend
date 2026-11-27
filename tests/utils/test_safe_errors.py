"""Generic client errors: raw exception text is logged, never returned.

Routes that used to put ``str(e)`` into a response could leak database hosts,
Stripe ids or provider internals. ``log_internal_error`` logs the detail with
the request id and returns a fixed message naming that id.
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.middleware.request_id_middleware import RequestIDMiddleware, current_request_id
from src.utils.safe_errors import log_internal_error, public_error_message

SECRET_TEXT = "connection to db.internal-host:5432 failed for sk_live_abc123"


def test_message_is_fixed_and_omits_exception_text(caplog):
    with caplog.at_level(logging.ERROR):
        message, request_id = log_internal_error(
            "Failed to do the thing", RuntimeError(SECRET_TEXT)
        )

    assert SECRET_TEXT not in message
    assert "sk_live" not in message
    assert message == public_error_message("Failed to do the thing", request_id)
    assert request_id.startswith("req_")
    # The detail is kept server-side, tagged with the same id.
    assert SECRET_TEXT in caplog.text
    assert request_id in caplog.text


def test_current_request_id_is_none_outside_a_request():
    assert current_request_id() is None


def _app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/boom")
    async def boom():
        message, request_id = log_internal_error("Boom failed", ValueError(SECRET_TEXT))
        return {"message": message, "request_id": request_id}

    @app.get("/boom-http")
    async def boom_http():
        message, _ = log_internal_error("Boom failed", ValueError(SECRET_TEXT))
        raise HTTPException(status_code=500, detail=message)

    return app


def test_uses_the_middleware_request_id():
    client = TestClient(_app())
    response = client.get("/boom", headers={"X-Request-ID": "req_trace123"})

    body = response.json()
    assert body["request_id"] == "req_trace123"
    assert response.headers["X-Request-ID"] == "req_trace123"
    assert "req_trace123" in body["message"]
    assert SECRET_TEXT not in body["message"]


def test_http_exception_detail_has_no_exception_text():
    client = TestClient(_app())
    response = client.get("/boom-http")

    assert response.status_code == 500
    assert SECRET_TEXT not in response.text
    assert response.headers["X-Request-ID"] in response.json()["detail"]
