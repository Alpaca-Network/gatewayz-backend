"""/v1/verify/* and /v1/webhooks — Gatewayz Verify (GenLayer) routes."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.routes import jobs as jobs_route
from src.routes import verify as vr
from src.routes import webhooks as whr

OWNER = {"id": 42, "key_id": 7, "key_name": "prod"}
SPEC_URI = "https://example.org/spec.md"
DELIV_URI = "https://example.org/deliverable.md"
REDACTED_URI = "https://example.org/redacted.md"
HASHES = {SPEC_URI: "0x" + "aa" * 32, DELIV_URI: "0x" + "bb" * 32, REDACTED_URI: "0x" + "cc" * 32}
JOB_ID = "0x" + "77" * 32


class World:
    def __init__(self):
        self.cases: dict[str, dict] = {}
        self.cap_ok = True
        self.charges: list = []
        self.refunds: list = []
        self.submitted: list = []
        self.jobs = {
            JOB_ID: {
                "job_id": JOB_ID,
                "user_id": 42,
                "spec_hash": HASHES[SPEC_URI],
                "usage_root": "0x" + "99" * 32,
            }
        }
        self.appeal_raises = None

    # db
    def charge_cap(self, key_id, amount, default):
        self.charges.append((key_id, amount))
        return self.cap_ok

    def refund_cap(self, key_id, amount):
        self.refunds.append((key_id, amount))

    def insert_case(self, row):
        case = {"status": "submitted", "genlayer_tx": None, "submit_attempts": 0, **row}
        self.cases[row["case_id"]] = case
        return dict(case)

    def get_case(self, case_id, user_id=None):
        c = self.cases.get(case_id)
        return dict(c) if c and (user_id is None or c["user_id"] == user_id) else None

    def update_case(self, case_id, fields):
        self.cases[case_id].update(fields)
        return dict(self.cases[case_id])

    def get_job(self, job_id, user_id):
        j = self.jobs.get(job_id)
        return j if j and j["user_id"] == user_id else None


class Client:
    network = "studionet"
    contract = "0xVJ"

    def __init__(self, world):
        self.world = world

    def min_appeal_bond(self, tx):
        return 5 * 10**18

    def appeal(self, tx, bond):
        if self.world.appeal_raises:
            raise self.world.appeal_raises
        return tx


@pytest.fixture
def world(monkeypatch):
    w = World()
    for n in ("charge_cap", "refund_cap", "insert_case", "get_case", "update_case"):
        monkeypatch.setattr(vr.db, n, getattr(w, n))
    monkeypatch.setattr(vr, "get_job", w.get_job)
    monkeypatch.setattr(vr, "fetch_hash", lambda url: HASHES[url])
    monkeypatch.setattr(vr, "is_configured", lambda: True)
    monkeypatch.setattr(vr, "get_client", lambda: Client(w))
    monkeypatch.setattr(
        vr, "quote_usd", lambda c: {"usd": "1.00", "fee_gen_wei": None, "source": "flat_price"}
    )
    monkeypatch.setattr(
        vr.cases, "submit", lambda case, client: w.submitted.append(case["case_id"])
    )
    return w


def _client(user=OWNER):
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")
    v1.include_router(vr.router)
    v1.include_router(whr.router)
    app.include_router(v1)
    app.dependency_overrides[vr.get_current_user] = lambda: user
    app.dependency_overrides[jobs_route.get_current_user] = lambda: user
    for rl in (vr.verify_create_rl, vr.verify_read_rl, whr.webhooks_rl):
        app.dependency_overrides[rl] = lambda: None
    return TestClient(app)


def _post(c, **over):
    body = {
        "spec_uri": SPEC_URI,
        "deliverable_uri": DELIV_URI,
        "rubric": "written_deliverable",
        **over,
    }
    return c.post("/v1/verify/cases", json=body)


def _code(r):
    return r.json()["detail"]["error"]["code"]


def test_dry_run_quotes_without_charging(world):
    r = _post(_client(), dry_run=True)
    assert r.status_code == 202
    d = r.json()
    assert d["quote"]["usd"] == "1.00" and d["deliverable_hash"] == HASHES[DELIV_URI]
    assert world.charges == [] and world.cases == {}


def test_open_case_charges_once_and_submits_in_background(world):
    r = _post(_client())
    assert r.status_code == 202
    v = r.json()
    assert v["status"] == "submitted" and v["schema_version"] == 1
    assert world.charges == [(7, Decimal("1.00"))]
    assert world.submitted == [v["case_id"]]


def test_private_case_needs_a_redacted_copy(world):
    assert _code(_post(_client(), private=True)) == "private_case_needs_redacted_uri"
    assert world.charges == []


def test_private_case_never_stores_the_original(world):
    r = _post(_client(), private=True, redacted_uri=REDACTED_URI)
    case = world.cases[r.json()["case_id"]]
    assert case["deliverable_uri"] == REDACTED_URI
    assert case["deliverable_hash"] == HASHES[REDACTED_URI]
    assert DELIV_URI not in str(case)


@pytest.mark.parametrize(
    "over,code",
    [
        ({"rubric": {"must_have": [], "pass_threshold": 70}}, "rubric_invalid"),
        ({"rubric": "nope"}, "rubric_invalid"),
        ({"spec_hash": "0x" + "00" * 32}, "spec_hash_mismatch"),
        ({"deliverable_hash": "0x" + "00" * 32}, "deliverable_hash_mismatch"),
        ({"deliverable_hash": "0x12"}, "invalid_deliverable_hash"),
        ({"deliverable_uri": None}, "deliverable_uri_required"),
    ],
)
def test_bad_requests_cost_nothing(world, over, code):
    r = _post(_client(), **over)
    assert r.status_code == 422 and _code(r) == code
    assert world.charges == []


def test_fetch_failure_is_a_typed_422(world, monkeypatch):
    from src.services.verify_fetch import FetchFailed

    def boom(url):
        raise FetchFailed("uri_not_allowed", "private address")

    monkeypatch.setattr(vr, "fetch_hash", boom)
    r = _post(_client())
    assert r.status_code == 422 and _code(r) == "uri_not_allowed"


def test_cap_exhausted_is_402_and_opens_nothing(world):
    world.cap_ok = False
    r = _post(_client())
    assert r.status_code == 402 and _code(r) == "verify_cap_exhausted"
    assert world.cases == {}


def test_not_configured_is_501(world, monkeypatch):
    monkeypatch.setattr(vr, "is_configured", lambda: False)
    assert _post(_client()).status_code == 501


def test_job_key_cannot_use_verify(world):
    r = _post(_client({"id": 42, "key_id": 9, "key_name": f"job:{JOB_ID}"}))
    assert r.status_code == 403


def test_job_case_carries_the_sealed_usage_root(world):
    r = _post(_client(), job_id=JOB_ID)
    v = r.json()
    assert v["case_id"] == JOB_ID and v["usage_root"] == "0x" + "99" * 32 and v["job_id"] == JOB_ID


def test_unsealed_job_is_409(world):
    world.jobs[JOB_ID]["usage_root"] = None
    assert _code(_post(_client(), job_id=JOB_ID)) == "job_not_sealed"


def test_second_case_for_a_job_is_refunded(world):
    _post(_client(), job_id=JOB_ID)
    r = _post(_client(), job_id=JOB_ID)
    assert r.status_code == 409 and _code(r) == "case_exists"
    assert world.refunds == [(7, Decimal("1.00"))]


def test_job_spec_must_match(world):
    world.jobs[JOB_ID]["spec_hash"] = "0x" + "01" * 32
    assert _code(_post(_client(), job_id=JOB_ID)) == "spec_hash_mismatch"


def test_cases_are_private_to_their_owner(world):
    case_id = _post(_client()).json()["case_id"]
    assert (
        _client({"id": 5, "key_id": 1, "key_name": "x"})
        .get(f"/v1/verify/cases/{case_id}")
        .status_code
        == 404
    )


def test_read_refreshes_a_stale_open_case(world, monkeypatch):
    case_id = _post(_client()).json()["case_id"]
    monkeypatch.setattr(
        vr.cases,
        "refresh",
        lambda case, client: {**case, "status": "decided", "pass": True, "score": 90},
    )
    v = _client().get(f"/v1/verify/cases/{case_id}").json()
    assert v["status"] == "decided" and v["pass"] is True


def test_appeal_flow(world):
    c = _client()
    case_id = _post(c).json()["case_id"]
    assert _code(c.post(f"/v1/verify/cases/{case_id}/appeal", json={})) == "case_not_final"
    world.cases[case_id].update(
        status="decided", genlayer_tx="0xtx", decided_at="2026-10-05T00:00:00+00:00"
    )
    q = c.post(f"/v1/verify/cases/{case_id}/appeal", json={}).json()
    assert q["confirm_required"] is True and q["appeal_bond_gen_wei"] == str(5 * 10**18)
    assert world.cases[case_id]["status"] == "decided"  # a quote changes nothing
    v = c.post(f"/v1/verify/cases/{case_id}/appeal", json={"confirm": True}).json()
    assert v["status"] == "appealed" and v["appeal_tx"] == "0xtx"
    assert (
        _code(c.post(f"/v1/verify/cases/{case_id}/appeal", json={"confirm": True}))
        == "appeal_not_open"
    )


def test_failed_appeal_refunds(world):
    c = _client()
    case_id = _post(c).json()["case_id"]
    world.cases[case_id].update(status="decided", genlayer_tx="0xtx")
    world.appeal_raises = RuntimeError("window closed")
    r = c.post(f"/v1/verify/cases/{case_id}/appeal", json={"confirm": True})
    assert r.status_code == 502 and world.cases[case_id]["status"] == "decided"
    assert world.refunds == [(7, Decimal("1.00"))]


# ------------------------------------------------------------------ webhooks


@pytest.fixture
def hooks(monkeypatch):
    store = []
    monkeypatch.setattr(
        whr,
        "create_hook",
        lambda uid, url, ev, enc, ver: store.append(
            {"id": "h1", "url": url, "events": ev, "active": True, "secret_enc": enc}
        )
        or {"id": "h1", "url": url, "events": ev},
    )
    monkeypatch.setattr(
        whr,
        "list_hooks",
        lambda uid: [{k: v for k, v in h.items() if k != "secret_enc"} for h in store],
    )
    monkeypatch.setattr(whr, "encrypt_api_key", lambda s: ("ENC(" + s + ")", 1))
    monkeypatch.setattr(whr, "validate_webhook_url", lambda u: u)
    return store


def test_webhook_secret_is_shown_once_and_stored_encrypted(hooks):
    c = _client()
    r = c.post(
        "/v1/webhooks", json={"url": "https://example.org/hook", "events": ["verify.case.updated"]}
    )
    assert r.status_code == 201
    secret = r.json()["secret"]
    assert secret.startswith("whsec_")
    assert hooks[0]["secret_enc"] == f"ENC({secret})"
    assert secret not in str(c.get("/v1/webhooks").json())


def test_webhook_rejects_unknown_events(hooks):
    r = _client().post("/v1/webhooks", json={"url": "https://example.org/hook", "events": ["all"]})
    assert r.status_code == 422


def test_webhook_rejects_internal_targets(monkeypatch):
    from src.services.webhook_target import validate_webhook_url

    monkeypatch.setattr(whr, "validate_webhook_url", validate_webhook_url)
    monkeypatch.setattr(whr, "list_hooks", lambda uid: [])
    for url in (
        "http://example.org/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest",
    ):
        r = _client().post("/v1/webhooks", json={"url": url, "events": ["job.closed"]})
        assert r.status_code == 422, url
