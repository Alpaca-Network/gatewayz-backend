import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.utils import request_timing as rt


async def _slow_step():
    await asyncio.sleep(0.01)
    return "ok"


def _thread_step():
    return "db"


async def chat(request):
    step = rt.timed(_slow_step)
    to_thread = rt.timed_to_thread(asyncio.to_thread)
    await step()
    await step()
    await to_thread(_thread_step)
    return JSONResponse({"ok": True})


async def other(request):
    return PlainTextResponse("x")


@pytest.fixture
def client():
    app = Starlette(
        routes=[Route("/v1/chat/completions", chat, methods=["POST"]), Route("/health", other)]
    )
    app.add_middleware(rt.RequestTimingMiddleware)
    return TestClient(app)


def test_inference_routes_get_server_timing(client):
    r = client.post("/v1/chat/completions")
    assert r.status_code == 200
    header = r.headers["server-timing"]
    assert "slow_step;dur=" in header  # repeated calls are summed into one entry
    assert header.count("slow_step") == 1
    assert "thread_step;dur=" in header
    assert header.endswith(tuple(f"{d}" for d in "0123456789")) and "total;dur=" in header


def test_other_routes_are_untouched(client):
    assert "server-timing" not in client.get("/health").headers


def test_recording_outside_a_request_is_a_noop():
    rt.record("x", 1.0)  # no active request: must not raise
    assert asyncio.run(rt.timed(_slow_step)()) == "ok"


def test_wrapping_is_idempotent_and_skips_sync_functions():
    once = rt.timed(_slow_step)
    assert rt.timed(once) is once
    assert rt.timed(_thread_step) is _thread_step


def test_slow_requests_are_logged(client, monkeypatch, caplog):
    monkeypatch.setattr(rt, "SLOW_REQUEST_MS", 0.0)
    with caplog.at_level("WARNING", logger="src.utils.request_timing"):
        client.post("/v1/chat/completions")
    assert any("slow inference request" in m for m in caplog.messages)
