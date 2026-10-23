"""/v1/jobs — job-scoped keys and sealed usage records (inference escrow)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.routes import jobs as jobs_route
from src.services import job_usage as ju

SPEC = "0x" + "22" * 32
OWNER = {"id": 42, "key_name": "prod"}


def _entries(n):
    return [
        {
            "ts": f"2026-10-05T12:00:{i:02d}Z",
            "model": "openai/gpt-5-mini",
            "provider": "openai",
            "tokens_in": 10 + i,
            "tokens_out": 5,
            "cost_usd": "0.01",
            "commit": f"r{i}",
        }
        for i in range(n)
    ]


class FakeStore:
    """In-memory stand-in for src.db.inference_jobs, recording call order."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.usage: dict[str, list[dict]] = {}
        self.calls: list[str] = []
        self.keys: list[dict] = []

    def create_api_key(self, user_id, key_name, expiration_days=None, scope_permissions=None, **kw):
        self.keys.append(
            {
                "user_id": user_id,
                "key_name": key_name,
                "expiration_days": expiration_days,
                "scope_permissions": scope_permissions,
            }
        )
        return "gw_live_jobkey", 900 + len(self.keys)

    def insert_job(self, row):
        job = {
            "status": "running",
            "spent_usd": "0",
            "usage_root": None,
            "created_at": "now",
            **row,
        }
        self.jobs[row["job_id"]] = job
        self.usage[row["job_id"]] = []
        return job

    def get_job(self, job_id, user_id):
        j = self.jobs.get(job_id)
        return j if j and j["user_id"] == user_id else None

    def list_usage(self, job_id):
        self.calls.append("list_usage")
        return list(self.usage[job_id])

    def mark_closed(self, job_id, api_key_id):
        self.calls.append("mark_closed")
        self.jobs[job_id]["status"] = "closed"

    def store_seal(self, job_id, sealed):
        self.calls.append("store_seal")
        j = self.jobs[job_id]
        if j["usage_root"] is None:
            j.update(
                usage_root=sealed["root"],
                usage_requests=sealed["requests"],
                usage_tokens_in=sealed["tokens_in"],
                usage_tokens_out=sealed["tokens_out"],
                usage_cost_usd=sealed["cost_usd"],
            )
        return j


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    for name in (
        "create_api_key",
        "insert_job",
        "get_job",
        "list_usage",
        "mark_closed",
        "store_seal",
    ):
        monkeypatch.setattr(jobs_route, name, getattr(s, name))
    return s


def _client(user=OWNER):
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")
    v1.include_router(jobs_route.router)
    app.include_router(v1)
    app.dependency_overrides[jobs_route.get_current_user] = lambda: user
    app.dependency_overrides[jobs_route.jobs_create_rl] = lambda: None
    app.dependency_overrides[jobs_route.jobs_read_rl] = lambda: None
    return TestClient(app)


def _create(c, **over):
    body = {
        "spec_hash": SPEC,
        "cap_usd": "2.50",
        "deadline": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
        **over,
    }
    return c.post("/v1/jobs", json=body)


def test_create_issues_a_capped_inference_only_key(store):
    r = _create(_client(), buyer="0xBuyer", seller="0xSeller")
    assert r.status_code == 201
    d = r.json()
    assert d["api_key"] == "gw_live_jobkey"
    assert d["status"] == "running" and d["cap_usd"] == "2.50"
    assert d["job_id"].startswith("0x") and len(d["job_id"]) == 66
    key = store.keys[0]
    assert key["key_name"] == f"job:{d['job_id']}"
    assert key["scope_permissions"] == ju.JOB_KEY_SCOPES
    assert key["expiration_days"] == 2


@pytest.mark.parametrize(
    "over",
    [
        {"spec_hash": "0x1234"},
        {"cap_usd": "0"},
        {"cap_usd": "-1"},
        {"cap_usd": "NaN"},
        {"cap_usd": "1000000"},
        {"deadline": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
        {"deadline": (datetime.now(UTC) + timedelta(days=31)).isoformat()},
    ],
)
def test_create_validates(store, over):
    assert _create(_client(), **over).status_code == 422
    assert store.keys == []


def test_a_job_key_cannot_manage_jobs(store):
    c = _client({"id": 42, "key_name": f"job:0x{'ab' * 32}"})
    r = _create(c)
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "job_key_forbidden"
    assert store.keys == []


def test_jobs_are_private_to_their_owner(store):
    job_id = _create(_client()).json()["job_id"]
    assert _client({"id": 7, "key_name": "other"}).get(f"/v1/jobs/{job_id}").status_code == 404
    assert _client().get("/v1/jobs/not-a-job").status_code == 404


def test_close_stops_appends_before_reading_the_log(store):
    c = _client()
    job_id = _create(c).json()["job_id"]
    store.usage[job_id] = _entries(3)
    r = c.post(f"/v1/jobs/{job_id}/close")
    assert r.status_code == 200
    assert store.calls == ["mark_closed", "list_usage", "store_seal"]
    u = r.json()["usage"]
    assert u["root"] == ju.seal(_entries(3))["root"] and u["requests"] == 3 and u["sealed"]


def test_close_is_idempotent_and_root_is_stable(store):
    c = _client()
    job_id = _create(c).json()["job_id"]
    store.usage[job_id] = _entries(2)
    first = c.post(f"/v1/jobs/{job_id}/close").json()["usage"]["root"]
    store.calls.clear()
    second = c.post(f"/v1/jobs/{job_id}/close").json()["usage"]["root"]
    assert first == second
    assert store.calls == []  # already sealed: nothing re-read, nothing re-written


def test_usage_preview_while_running_then_sealed(store):
    c = _client()
    job_id = _create(c).json()["job_id"]
    store.usage[job_id] = _entries(4)
    live = c.get(f"/v1/jobs/{job_id}/usage").json()
    assert live["sealed"] is False and live["requests"] == 4
    c.post(f"/v1/jobs/{job_id}/close")
    sealed = c.get(f"/v1/jobs/{job_id}/usage?full=true").json()
    assert sealed["sealed"] is True and sealed["root"] == live["root"]
    assert len(sealed["entries"]) == 4


def test_a_tampered_log_is_reported_not_served(store):
    c = _client()
    job_id = _create(c).json()["job_id"]
    store.usage[job_id] = _entries(3)
    c.post(f"/v1/jobs/{job_id}/close")
    store.usage[job_id][1]["tokens_out"] = 999
    r = c.get(f"/v1/jobs/{job_id}/usage")
    assert r.status_code == 500
    assert r.json()["detail"]["error"]["code"] == "usage_root_mismatch"


def test_proof_needs_a_sealed_job_and_verifies(store):
    c = _client()
    job_id = _create(c).json()["job_id"]
    store.usage[job_id] = _entries(5)
    assert (
        c.get(f"/v1/jobs/{job_id}/usage/proof?i=0").json()["detail"]["error"]["code"]
        == "job_not_sealed"
    )
    c.post(f"/v1/jobs/{job_id}/close")
    p = c.get(f"/v1/jobs/{job_id}/usage/proof?i=2").json()
    h = bytes.fromhex(p["leaf"][2:])
    for sib in p["proof"]:
        h = ju._node(h, bytes.fromhex(sib[2:]))
    assert "0x" + h.hex() == p["root"]
    assert c.get(f"/v1/jobs/{job_id}/usage/proof?i=5").status_code == 404
