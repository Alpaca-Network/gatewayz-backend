"""Job keys: bounded in-flight requests (Codex P1 on #2382).

Admission (enforce_job_key -> admit_job_request) takes a lease for each billed
request; JobLeaseMiddleware releases it when the response is over. The SQL side
(atomicity under a real burst) is in tests/sql/test_job_leases_sql.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from src.middleware.job_lease_middleware import JobLeaseMiddleware
from src.services import job_usage as ju

JOB = "0x" + "cd" * 32
KEY = {"id": 9, "key_name": f"job:{JOB}", "is_active": True}


def _client(rpc_result=None, rpc_error=None):
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value
    q.execute.return_value = MagicMock(
        data=[
            {
                "status": "running",
                "cap_usd": "1",
                "spent_usd": "0",
                "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ]
    )
    rpc = client.rpc.return_value
    if rpc_error is not None:
        rpc.execute.side_effect = rpc_error
    else:
        rpc.execute.return_value = MagicMock(data=rpc_result)
    return client


def _enforce(client, method="POST", path="/v1/chat/completions", sink=None):
    sink = [] if sink is None else sink
    ju.enforce_job_key(KEY, client, method=method, path=path, lease_sink=sink)
    return sink


# --------------------------------------------------------------------------- admission


def test_billed_request_takes_a_lease_with_the_configured_bounds(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "JOB_MAX_INFLIGHT", 4)
    monkeypatch.setattr(Config, "JOB_LEASE_TTL_SECONDS", 900)
    client = _client({"job_id": JOB, "lease_id": "L1"})
    assert _enforce(client) == [(JOB, "L1")]
    client.rpc.assert_called_once_with(
        "admit_job_request", {"p_api_key_id": 9, "p_max_inflight": 4, "p_ttl_seconds": 900}
    )


def test_reads_and_callers_without_a_sink_take_no_lease():
    client = _client({"job_id": JOB, "lease_id": "L1"})
    assert _enforce(client, method="GET", path="/v1/models") == []
    ju.enforce_job_key(KEY, client, method="POST", path="/v1/chat/completions")
    client.rpc.assert_not_called()


@pytest.mark.parametrize(
    "code,message",
    [
        ("job_busy", ju.JOB_BUSY),
        ("job_cap_reached", ju.JOB_CAP_REACHED),
        ("job_not_running", ju.JOB_NOT_RUNNING),
    ],
)
def test_admission_refusals_are_raised(code, message):
    client = _client(rpc_error=Exception(f"P0001: {code}"))
    with pytest.raises(ValueError, match=message):
        _enforce(client)


def test_admission_unavailable_admits_on_the_plain_checks(caplog):
    """Migration not applied yet: the pre-lease behaviour, loudly, not an outage."""
    client = _client(rpc_error=Exception("Could not find the function admit_job_request"))
    assert _enforce(client) == []
    assert "in-flight limit NOT applied" in caplog.text


def test_ordinary_key_never_admits():
    client = MagicMock()
    sink = []
    ju.enforce_job_key(
        {"id": 1, "key_name": "prod"}, client, method="POST", path="/v1/x", lease_sink=sink
    )
    assert sink == [] and not client.rpc.called


def test_busy_is_a_429():
    from src.security.deps import ceiling_http_exception

    exc = ceiling_http_exception(ju.JOB_BUSY)
    assert exc.status_code == 429
    assert exc.detail["error"]["code"] == "job_busy"


# --------------------------------------------------------------------------- get_api_key


def _request(path="/v1/chat/completions", lease=None):
    state = SimpleNamespace()
    if lease:
        state.job_lease = lease
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host="1.2.3.4"),
        headers={},
        state=state,
    )


def test_get_api_key_keeps_the_lease_even_when_a_later_check_refuses():
    """IP allowlist / domain checks run after admission: the slot must still be freed."""
    from src.security import deps

    def validate(api_key, client_ip=None, referer=None, *, method=None, path=None, lease_sink=None):
        lease_sink.append((JOB, "L1"))
        raise ValueError("IP address not allowed for this API key")

    req = _request()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="gw_job")
    with patch.object(deps, "validate_api_key_security", validate):
        with pytest.raises(Exception):  # noqa: B017 - the HTTPException itself is not the point
            asyncio.run(deps.get_api_key(credentials=creds, request=req))
    assert req.state.job_lease == (JOB, "L1")


def test_get_api_key_takes_one_lease_per_request():
    """get_api_key can run twice for one request; the second must not admit again."""
    from src.security import deps

    seen = []

    def validate(api_key, client_ip=None, referer=None, *, method=None, path=None, lease_sink=None):
        seen.append(lease_sink)
        return api_key

    req = _request(lease=(JOB, "L1"))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="gw_job")
    with (
        patch.object(deps, "validate_api_key_security", validate),
        patch.object(deps, "get_user", lambda key: None),
    ):
        asyncio.run(deps.get_api_key(credentials=creds, request=req))
    assert seen == [None]


# --------------------------------------------------------------------------- release


def _app(fail=False):
    app = FastAPI()

    @app.post("/stream")
    async def stream(request: __import__("fastapi").Request):
        request.state.job_lease = (JOB, "L1")
        if fail:
            raise RuntimeError("boom")

        async def body():
            for part in ("a", "b", "c"):
                await asyncio.sleep(0)
                yield part

        return StreamingResponse(body())

    @app.post("/plain")
    async def plain():
        return {"ok": True}

    app.add_middleware(JobLeaseMiddleware)
    return app


def test_lease_is_released_after_the_stream_finishes():
    with patch("src.middleware.job_lease_middleware.release_job_lease") as rel:
        r = TestClient(_app()).post("/stream")
    assert r.text == "abc"
    rel.assert_called_once_with(JOB, "L1")


def test_lease_is_released_when_the_request_fails():
    with patch("src.middleware.job_lease_middleware.release_job_lease") as rel:
        TestClient(_app(fail=True), raise_server_exceptions=False).post("/stream")
    rel.assert_called_once_with(JOB, "L1")


def test_requests_without_a_lease_release_nothing():
    with patch("src.middleware.job_lease_middleware.release_job_lease") as rel:
        TestClient(_app()).post("/plain")
    rel.assert_not_called()


def test_release_never_raises(caplog):
    with patch("src.config.supabase_config.get_supabase_client", side_effect=RuntimeError("down")):
        ju.release_job_lease(JOB, "L1")
    assert "not released" in caplog.text
