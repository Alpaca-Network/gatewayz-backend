"""Gatewayz Verify: rubric parity, case state machine, webhook signing."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from src.services import outbound_webhooks as wh
from src.services import verify_cases as vc
from src.services.genlayer_verify import TxState
from src.services.verify_rubric import TEMPLATES, RubricInvalid, resolve, rubric_hash

# rubric_hash of Alpaca-Network/gatewayz-genlayer rubrics/*.json; the written one is
# also what VerifyJob computed ON-CHAIN on Studionet (2026-10-05).
PUBLIC_REPO_HASHES = {
    "written_deliverable": "0xfbba4117ee63d9bb8e1b474022537b243e0d47333e111684835026e15746537f",
    "code_deliverable": "0x22bc64211e6d780a82e4c11006f11e8c079b4f5c138a6fd374257bbe6aeaa9b5",
    "data_extraction": "0xb58b88cf35c9301f6f0fa0cd090ad172a3e3fb78ba88f8ac297d88a944b10528",
}


@pytest.mark.parametrize("name,expected", sorted(PUBLIC_REPO_HASHES.items()))
def test_templates_hash_like_the_contract(name, expected):
    assert rubric_hash(TEMPLATES[name]) == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"must_have": [], "pass_threshold": 70},
        {"must_have": ["x"], "pass_threshold": 101},
        {"must_have": ["x"], "pass_threshold": True},
        {"must_have": ["x"], "pass_threshold": 70, "score_tolerance": 51},
        {"must_have": ["x"], "pass_threshold": 70, "surprise": 1},
        {"must_have": ["x" * 301], "pass_threshold": 70},
        "no_such_template",
        42,
    ],
)
def test_bad_rubrics_are_refused_before_any_fee(bad):
    with pytest.raises(RubricInvalid):
        resolve(bad)


def test_template_by_name_is_a_copy():
    r = resolve("written_deliverable")
    r["must_have"].append("mutated")
    assert "mutated" not in TEMPLATES["written_deliverable"]["must_have"]


# ------------------------------------------------------------------ state machine

CASE = {
    "case_id": "0x" + "11" * 32,
    "user_id": 42,
    "api_key_id": 7,
    "job_id": None,
    "spec_uri": "https://example.org/spec",
    "spec_hash": "0x" + "22" * 32,
    "deliverable_uri": "https://example.org/d",
    "deliverable_hash": "0x" + "33" * 32,
    "usage_root": "0x" + "00" * 32,
    "rubric": TEMPLATES["written_deliverable"],
    "rubric_hash": PUBLIC_REPO_HASHES["written_deliverable"],
    "status": "submitted",
    "genlayer_tx": None,
    "charged_usd": "1.00",
    "submit_attempts": 0,
    "network": "studionet",
    "contract_address": "0xVJ",
}


class FakeDB:
    def __init__(self, case):
        self.case = copy.deepcopy(case)
        self.refunds = []
        self.job_tx = None

    def update_case(self, case_id, fields):
        self.case.update(fields)
        return dict(self.case)

    def refund_cap(self, key_id, amount):
        self.refunds.append((key_id, amount))

    def set_job_genlayer_tx(self, job_id, tx):
        self.job_tx = (job_id, tx)


class FakeClient:
    contract = "0xVJ"

    def __init__(self, submit=None, state=None, verdict=None):
        self._submit, self.state, self.verdict, self.reads = submit, state, verdict, []

    def submit_case(self, args):
        if isinstance(self._submit, Exception):
            raise self._submit
        self.args = args
        return self._submit

    def tx_state(self, tx):
        return self.state

    def read_verdict(self, case_id, final):
        self.reads.append(final)
        return self.verdict


@pytest.fixture
def fdb(monkeypatch):
    f = FakeDB(CASE)
    for n in ("update_case", "refund_cap", "set_job_genlayer_tx"):
        monkeypatch.setattr(vc.db, n, getattr(f, n))
    return f


def test_submit_sends_the_exact_contract_args(fdb):
    c = FakeClient(submit="0xtx")
    out = vc.submit(fdb.case, c)
    assert out["status"] == "adjudicating" and out["genlayer_tx"] == "0xtx"
    assert c.args[:6] == [
        CASE["case_id"],
        CASE["spec_uri"],
        CASE["spec_hash"],
        CASE["deliverable_uri"],
        CASE["deliverable_hash"],
        CASE["usage_root"],
    ]
    assert '"pass_threshold":70' in c.args[6]


def test_submit_links_the_job(fdb):
    fdb.case["job_id"] = CASE["case_id"]
    vc.submit(fdb.case, FakeClient(submit="0xtx"))
    assert fdb.job_tx == (CASE["case_id"], "0xtx")


def test_transient_submit_failure_stays_open_and_retries(fdb):
    out = vc.submit(fdb.case, FakeClient(submit=RuntimeError("RPC timeout")))
    assert out["status"] == "submitted" and out["submit_attempts"] == 1
    assert fdb.refunds == []


def test_third_failure_is_terminal_and_refunds(fdb):
    fdb.case["submit_attempts"] = 2
    out = vc.submit(fdb.case, FakeClient(submit=RuntimeError("RPC timeout")))
    assert out["status"] == "error"
    assert fdb.refunds == [(7, Decimal("1.00"))]


def test_permanent_contract_error_is_terminal_at_once(fdb):
    out = vc.submit(fdb.case, FakeClient(submit=RuntimeError("UserError: submitter_not_allowed")))
    assert out["status"] == "error" and out["submit_attempts"] == 1
    assert fdb.refunds == [(7, Decimal("1.00"))]


def _open(fdb, status="adjudicating"):
    fdb.case.update(status=status, genlayer_tx="0xtx")
    return dict(fdb.case)


V = {"pass": True, "score": 88, "reasons": ["complete"]}


def test_accepted_becomes_decided_from_nonfinal_state(fdb):
    c = FakeClient(state=TxState("ACCEPTED", "MAJORITY_AGREE", None, "0xVJ"), verdict=V)
    out = vc.refresh(_open(fdb), c)
    assert out["status"] == "decided" and out["pass"] is True and out["score"] == 88
    assert c.reads == [False]
    assert vc.view(out)["appeal_window_ends"] is not None


def test_finalized_reads_final_state_only(fdb):
    c = FakeClient(state=TxState("FINALIZED", "MAJORITY_AGREE", None, "0xVJ"), verdict=V)
    out = vc.refresh(_open(fdb, "decided"), c)
    assert out["status"] == "final" and c.reads == [True]
    assert vc.view(out)["final"] is True and vc.view(out)["appeal_window_ends"] is None


@pytest.mark.parametrize(
    "state,status",
    [
        (TxState("UNDETERMINED", None, None, "0xVJ"), "undetermined"),
        (TxState("FINALIZED", "MAJORITY_DISAGREE", None, "0xVJ"), "undetermined"),
        (TxState("ACCEPTED", "MAJORITY_AGREE", "FINISHED_WITH_ERROR", "0xVJ"), "error"),
        (TxState("ACCEPTED", "MAJORITY_AGREE", None, "0xSomeoneElse"), "error"),
    ],
)
def test_failures_are_terminal_never_open(fdb, state, status):
    out = vc.refresh(_open(fdb), FakeClient(state=state, verdict=V))
    assert out["status"] == status
    assert out["status"] not in vc.db.OPEN_STATUSES


def test_pending_stays_adjudicating(fdb):
    out = vc.refresh(_open(fdb), FakeClient(state=TxState("PROPOSING", None, None, "0xVJ")))
    assert out["status"] == "adjudicating" and out["last_checked_at"]


def test_appealed_case_waits_for_finality(fdb):
    c = FakeClient(state=TxState("ACCEPTED", "MAJORITY_AGREE", None, "0xVJ"), verdict=V)
    out = vc.refresh(_open(fdb, "appealed"), c)
    assert out["status"] == "appealed" and c.reads == []


def test_view_is_schema_v1():
    v = vc.view(
        {
            **CASE,
            "status": "final",
            "pass": False,
            "score": 12,
            "reasons": ["off-topic"],
            "genlayer_tx": "0xtx",
            "finalized_at": "2026-10-05T00:00:00+00:00",
        }
    )
    for k in (
        "case_id",
        "status",
        "pass",
        "score",
        "reasons",
        "genlayer_tx",
        "finalized_at",
        "appeal_window_ends",
    ):
        assert k in v
    assert v["schema_version"] == 1


# ------------------------------------------------------------------ webhooks


def test_signature_round_trip_and_tamper():
    body = wh.build_event("verify.case.updated", {"case_id": "0x1"})
    import time

    header = wh.sign("whsec_x", int(time.time()), body)
    assert wh.verify_signature("whsec_x", header, body)
    assert not wh.verify_signature("whsec_x", header, body + b" ")
    assert not wh.verify_signature("whsec_other", header, body)
    stale = wh.sign("whsec_x", int(time.time()) - 3600, body)
    assert not wh.verify_signature("whsec_x", stale, body)


def test_emit_delivers_and_records(monkeypatch):
    sent, recorded = [], []
    hook = {
        "id": "h1",
        "url": "https://example.org/hook",
        "secret_enc": "enc",
        "key_version": 1,
        "failure_count": 0,
    }
    monkeypatch.setattr("src.db.outbound_webhooks.hooks_for", lambda uid, ev: [hook])
    monkeypatch.setattr(
        "src.db.outbound_webhooks.record_attempt", lambda h, ok, st, mx: recorded.append((ok, st))
    )
    monkeypatch.setattr("src.utils.crypto.decrypt_api_key", lambda tok, v=None: "whsec_x")
    monkeypatch.setattr(
        wh, "_deliver_one", lambda h, s, e, b, sleep=None: sent.append((s, e, b)) or 200
    )
    assert wh.emit(42, "job.closed", {"job_id": "0x1"}) == 1
    assert sent[0][0] == "whsec_x" and sent[0][1] == "job.closed"
    assert recorded == [(True, 200)]


def test_emit_refuses_unknown_events():
    with pytest.raises(ValueError):
        wh.emit(42, "everything", {})


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _FakeHttp:
    calls = []

    def __init__(self, codes):
        self.codes = list(codes)

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content, headers):
        _FakeHttp.calls.append(headers)
        return _Resp(self.codes.pop(0))


@pytest.mark.parametrize(
    "codes,expected_status,attempts",
    [
        ([200], 200, 1),
        ([500, 503, 200], 200, 3),
        ([500, 500, 500], 500, 3),
        ([410], 410, 1),
        ([429, 200], 200, 2),
    ],
)
def test_delivery_retries_only_what_retrying_can_fix(monkeypatch, codes, expected_status, attempts):
    _FakeHttp.calls = []
    monkeypatch.setattr(wh.httpx, "Client", _FakeHttp(codes))
    monkeypatch.setattr(wh, "PinnedPublicIPTransport", lambda: None)
    status = wh._deliver_one(
        {"id": "h", "url": "https://e.org"}, "whsec_x", "job.closed", b"{}", sleep=lambda s: None
    )
    assert status == expected_status and len(_FakeHttp.calls) == attempts
    assert _FakeHttp.calls[0]["X-Gatewayz-Signature"].startswith("t=")
