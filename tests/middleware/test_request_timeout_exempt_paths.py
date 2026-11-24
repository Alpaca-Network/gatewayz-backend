"""Streaming inference routes must not be cut by the request timeout.

/v1/messages was missing from the exemption list, so an Anthropic-format stream
that ran past the default limit was dropped mid-answer with no error event.
"""

import asyncio

import pytest

from src.middleware.request_timeout_middleware import (
    TIMEOUT_EXEMPT_PATHS,
    RequestTimeoutMiddleware,
)


def _scope(path: str) -> dict:
    return {"type": "http", "method": "POST", "path": path, "headers": []}


async def _run(path: str) -> list[dict]:
    sent: list[dict] = []

    async def slow_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0.2)
        await send({"type": "http.response.body", "body": b"done"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = RequestTimeoutMiddleware(slow_app, timeout_seconds=0.05)
    await middleware(_scope(path), receive, send)
    return sent


def test_both_inference_routes_are_exempt():
    assert "/v1/chat/completions" in TIMEOUT_EXEMPT_PATHS
    assert "/v1/messages" in TIMEOUT_EXEMPT_PATHS


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/chat/completions"])
def test_exempt_stream_outlives_the_timeout(path):
    sent = asyncio.run(_run(path))
    assert sent[-1] == {"type": "http.response.body", "body": b"done"}


def test_other_routes_are_still_limited():
    sent = asyncio.run(_run("/v1/some-other-route"))
    assert {"type": "http.response.body", "body": b"done"} not in sent
