"""Tests for src/routes/coupons.py (POST /coupons/redeem).

Three things these care about more than coverage:

1. **Exactly one grant.** A coupon is money; a redemption that can happen
   twice is the only bug in this feature that cannot be fixed after the fact.
2. **Every refusal keeps its own reason.** Expired, exhausted, wrong account
   and already-redeemed are four distinct answers. Collapsing them into
   "invalid coupon" is the masking this codebase spent the week removing --
   and it turns a self-explanatory failure into a support ticket.
3. **An outage never reads as a refusal.** Telling a user their good code is
   invalid is worse than telling them to retry, because they stop trying.

On the concurrency tests below
==============================
No Postgres runs in CI (tests/conftest.py skips anything needing one), so
TestConcurrentRedemption drives the real endpoint against `FakeCouponDatabase`
-- a model of the three Postgres mechanisms the RPC relies on: `FOR UPDATE`
row locks, `UNIQUE(coupon_id, user_id)`, and `CHECK(times_used <= max_uses)`,
with whole-transaction rollback. What it proves is that the Python half
introduces no double-grant path of its own and that the layered guards behave
as claimed when they fire. What it cannot prove is that the shipped SQL still
contains those constructs -- that is pinned separately and statically by
tests/schema/test_redeem_coupon_rpc.py, which fails if `FOR UPDATE` or the
in-statement `times_used + 1` is ever removed. The two tests are complementary
and neither is sufficient alone.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.db.coupon_redemptions import RedemptionUnavailable
from src.main import app
from src.routes.coupons import coupon_redeem_rl
from src.security.deps import get_current_user

client = TestClient(app)

USER = {"id": 34, "email": "user@example.com", "role": "user"}
OTHER_USER = {"id": 99, "email": "other@example.com", "role": "user"}

NOW = datetime(2026, 9, 16, tzinfo=UTC)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=30)


def make_coupon(**overrides):
    """A stored `coupons` row. Only columns verified to exist in prod
    (ynleroehyrmaafkgjgmr, 2026-09-16)."""
    row = {
        "id": 19,
        "code": "GATEWAYZ",
        "description": "Gatewayz Coupon",
        "coupon_type": "referral",
        "coupon_scope": "global",
        "value_usd": 20.00,
        "assigned_to_user_id": None,
        "max_uses": 1,
        "times_used": 0,
        "valid_from": PAST,
        "valid_until": FUTURE,
        "is_active": True,
    }
    row.update(overrides)
    return row


def verdict(**overrides):
    row = {
        "success": True,
        "error_code": None,
        "error_message": None,
        "debug": None,
        "coupon_id": 19,
        "code": "GATEWAYZ",
        "value_applied": 20.0,
        "balance_before": 5.0,
        "balance_after": 25.0,
        "redemption_id": 7,
        "transaction_id": 4242,
    }
    row.update(overrides)
    return row


def refusal(error_code, message="nope", **overrides):
    row = {
        "success": False,
        "error_code": error_code,
        "error_message": message,
        "debug": None,
        "coupon_id": 19,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around every
    test -- same reason as tests/routes/test_admin_coupons.py: another module
    can leave an override set, and only restoring the exact prior dict makes
    this file independent of run order."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """The endpoint is deliberately throttled to 5/min; every test but
    TestRateLimit would otherwise be testing the throttle."""
    app.dependency_overrides[coupon_redeem_rl] = lambda: None
    yield


@pytest.fixture(autouse=True)
def _mute_audit():
    """record_audit writes to Supabase; asserted on explicitly where it
    matters and must never attempt a network call anywhere else."""
    with patch("src.routes.coupons.record_audit") as mock:
        yield mock


@pytest.fixture
def as_user():
    def _set(user=None):
        app.dependency_overrides[get_current_user] = lambda: user or USER

    _set()
    return _set


def post(code="GATEWAYZ", **kwargs):
    return client.post("/coupons/redeem", json={"code": code}, **kwargs)


class TestAuth:
    def test_redeem_requires_authentication(self):
        assert post().status_code in (401, 403)

    def test_unauthenticated_request_never_reaches_the_database(self):
        with patch("src.routes.coupons.redeem_coupon") as rpc:
            post()
        rpc.assert_not_called()


class TestSuccess:
    def test_grants_and_returns_the_new_balance(self, as_user):
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            response = post()

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "coupon_id": 19,
            "code": "GATEWAYZ",
            "value_applied": 20.0,
            "balance_before": 5.0,
            "balance_after": 25.0,
            "redemption_id": 7,
            "transaction_id": 4242,
        }
        assert rpc.call_args.kwargs["user_id"] == 34

    def test_redeems_for_the_authenticated_user_only(self, as_user):
        """The account credited comes from the auth dependency, never the body.

        A user_id accepted from the request would let any authenticated caller
        credit somebody else's account -- or their own, repeatedly, under
        another id.
        """
        as_user(OTHER_USER)
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            client.post("/coupons/redeem", json={"code": "GATEWAYZ", "user_id": 1})
        # The body was rejected outright rather than partly honoured.
        rpc.assert_not_called()

        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post()
        assert rpc.call_args.kwargs["user_id"] == 99

    def test_code_is_normalised_before_the_lookup(self, as_user):
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post("  gatewayz  ")
        assert rpc.call_args.kwargs["code"] == "GATEWAYZ"

    def test_records_the_client_fingerprint_for_fraud_review(self, as_user):
        """coupon_redemptions has ip_address/user_agent columns for exactly
        this; leaving them NULL wastes the only abuse signal the table has."""
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post(headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1", "User-Agent": "curl/8"})
        assert rpc.call_args.kwargs["ip_address"] == "203.0.113.9"
        assert rpc.call_args.kwargs["user_agent"] == "curl/8"

    def test_user_agent_is_bounded(self, as_user):
        """coupon_redemptions.user_agent is unbounded TEXT; the column is a
        fraud signal, not a log, so an 8KB header must not land in it whole."""
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post(headers={"User-Agent": "A" * 4000})
        assert len(rpc.call_args.kwargs["user_agent"]) == 512

    def test_absent_user_agent_is_none_not_empty_string(self, as_user):
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post(headers={"User-Agent": ""})
        assert rpc.call_args.kwargs["user_agent"] is None

    def test_writes_an_audit_entry(self, as_user, _mute_audit):
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()):
            post()
        _mute_audit.assert_called_once()
        kwargs = _mute_audit.call_args.kwargs
        assert kwargs["action"] == "coupon.redeemed"
        assert kwargs["target_type"] == "coupon"
        assert kwargs["target_id"] == 19
        assert kwargs["metadata"]["value_applied"] == 20.0

    def test_calls_the_rpc_exactly_once(self, as_user):
        """One request, one redemption attempt. A retry in the route is how
        one click becomes two ledger rows."""
        with patch("src.routes.coupons.redeem_coupon", return_value=verdict()) as rpc:
            post()
        assert rpc.call_count == 1


class TestRefusalsStayDistinguishable:
    """Every invariant gets its own status AND its own error.code."""

    CASES = [
        ("COUPON_NOT_FOUND", 404, "coupon_not_found"),
        ("COUPON_NOT_ASSIGNED", 403, "coupon_not_assigned"),
        ("COUPON_INACTIVE", 409, "coupon_inactive"),
        ("COUPON_NOT_YET_ACTIVE", 409, "coupon_not_yet_active"),
        ("ALREADY_REDEEMED", 409, "coupon_already_redeemed"),
        ("MAX_USES_EXCEEDED", 409, "coupon_max_uses_exceeded"),
        ("COUPON_EXPIRED", 410, "coupon_expired"),
    ]

    @pytest.mark.parametrize("error_code,status,api_code", CASES)
    def test_each_reason_maps_to_its_own_response(self, as_user, error_code, status, api_code):
        with patch("src.routes.coupons.redeem_coupon", return_value=refusal(error_code)):
            response = post()
        assert response.status_code == status
        assert response_error(response)["code"] == api_code

    def test_every_reason_the_db_layer_can_return_has_a_mapping(self):
        """The route indexes _REFUSAL_RESPONSES directly, so a reason with no
        entry would KeyError into a 500 -- turning a clean refusal into an
        outage. Pinning the two sets equal is what keeps adding a reason to
        the RPC from doing that silently."""
        from src.db.coupon_redemptions import REDEMPTION_ERROR_CODES
        from src.routes.coupons import _REFUSAL_RESPONSES

        assert set(_REFUSAL_RESPONSES) == set(REDEMPTION_ERROR_CODES)

    def test_no_two_reasons_share_an_error_code(self):
        codes = [api_code for _, _, api_code in self.CASES]
        assert len(set(codes)) == len(codes), "a shared error.code is indistinguishable to a client"

    def test_the_four_named_reasons_are_all_different(self, as_user):
        """expired / exhausted / wrong-user / already-redeemed -- the four the
        feature was specified to keep apart."""
        seen = set()
        for error_code in (
            "COUPON_EXPIRED",
            "MAX_USES_EXCEEDED",
            "COUPON_NOT_ASSIGNED",
            "ALREADY_REDEEMED",
        ):
            with patch("src.routes.coupons.redeem_coupon", return_value=refusal(error_code)):
                response = post()
            seen.add((response.status_code, response_error(response)["code"]))
        assert len(seen) == 4

    def test_refusal_message_reaches_the_user(self, as_user):
        with patch(
            "src.routes.coupons.redeem_coupon",
            return_value=refusal("COUPON_EXPIRED", "This coupon expired on 2026-01-01."),
        ):
            response = post()
        assert "2026-01-01" in response_error(response)["message"]

    def test_refusal_uses_the_repo_error_envelope(self, as_user):
        with patch("src.routes.coupons.redeem_coupon", return_value=refusal("COUPON_EXPIRED")):
            body = response_error(post())
        assert set(body) == {"message", "type", "code"}

    def test_refusal_is_audited_with_its_reason(self, as_user, _mute_audit):
        """The fraud trail needs the failures, not only the successes --
        a run of COUPON_NOT_FOUND from one account is code guessing."""
        with patch("src.routes.coupons.redeem_coupon", return_value=refusal("COUPON_NOT_FOUND")):
            post()
        kwargs = _mute_audit.call_args.kwargs
        assert kwargs["action"] == "coupon.redemption_refused"
        assert kwargs["metadata"]["reason"] == "COUPON_NOT_FOUND"


def response_error(response):
    """The error body.

    src/main.py installs detailed_http_exception_handler, which returns an
    already-structured `detail` as the response body verbatim -- so the
    envelope is top-level {"error": {...}}, not nested under "detail".
    Matches how tests/routes/test_admin_coupons.py reads it.
    """
    return response.json()["error"]


class TestOutageIsNeverARefusal:
    def test_rpc_unavailable_is_503_not_invalid_coupon(self, as_user):
        """Answering 404/409 here would tell a user their good code is dead."""
        with patch(
            "src.routes.coupons.redeem_coupon",
            side_effect=RedemptionUnavailable("PGRST202"),
        ):
            response = post()
        assert response.status_code == 503
        assert response_error(response)["code"] == "coupon_redemption_failed"

    def test_internal_failure_is_503(self, as_user):
        with patch(
            "src.routes.coupons.redeem_coupon",
            return_value=refusal("REDEMPTION_FAILED", debug="22003: numeric field overflow"),
        ):
            response = post()
        assert response.status_code == 503

    def test_internal_failure_never_leaks_the_database_message(self, as_user):
        """`debug` carries SQLSTATE/SQLERRM. It is for the server log."""
        with patch(
            "src.routes.coupons.redeem_coupon",
            return_value=refusal(
                "REDEMPTION_FAILED",
                message="relation coupons_v2 does not exist",
                debug="42P01: relation coupons_v2 does not exist",
            ),
        ):
            response = post()
        body = response.text
        assert "42P01" not in body
        assert "coupons_v2" not in body

    def test_unknown_principal_is_503_not_a_coupon_error(self, as_user):
        as_user({"email": "ghost@example.com"})  # no "id"
        with patch("src.routes.coupons.redeem_coupon") as rpc:
            response = post()
        assert response.status_code == 503
        rpc.assert_not_called()


class TestInputValidation:
    @pytest.mark.parametrize("code", ["has space", "semi;colon", "aa'bb", "x" * 51])
    def test_malformed_code_is_422_and_never_reaches_the_database(self, as_user, code):
        with patch("src.routes.coupons.redeem_coupon") as rpc:
            response = client.post("/coupons/redeem", json={"code": code})
        assert response.status_code == 422
        rpc.assert_not_called()

    def test_empty_code_is_rejected(self, as_user):
        assert client.post("/coupons/redeem", json={"code": ""}).status_code == 422

    def test_missing_code_is_rejected(self, as_user):
        assert client.post("/coupons/redeem", json={}).status_code == 422

    def test_caller_cannot_supply_the_value(self, as_user):
        """The amount granted comes from the coupon row and nothing else."""
        with patch("src.routes.coupons.redeem_coupon") as rpc:
            response = client.post("/coupons/redeem", json={"code": "GATEWAYZ", "value_usd": 1000})
        assert response.status_code == 422
        rpc.assert_not_called()


class TestRateLimit:
    """A coupon code is a bearer secret in a small alphabet, so an unthrottled
    redeem endpoint is a code-guessing oracle that pays out.

    Exercised against the dependency directly rather than by firing requests
    through the whole middleware stack: each full request costs ~1.5s of
    ip_whitelist lookups, and eight of those put a single test within a few
    seconds of pytest.ini's 30s per-test timeout -- a test that fails on a slow
    CI runner teaches people to rerun rather than to read.
    """

    def test_the_endpoint_declares_the_rate_limit_dependency(self):
        """Wiring, checked separately from behaviour: a throttle that is
        implemented but not attached is the failure mode worth catching."""
        route = next(
            r
            for r in app.routes
            if getattr(r, "path", None) == "/coupons/redeem" and "POST" in getattr(r, "methods", ())
        )
        calls = {d.call for d in route.dependant.dependencies}
        assert coupon_redeem_rl in calls

    async def test_sixth_attempt_in_a_window_is_refused(self):
        from fastapi import HTTPException

        from src.services import endpoint_rate_limiter

        endpoint_rate_limiter._buckets["coupon_redeem"].clear()

        class _Req:
            headers = {"Authorization": "Bearer gw_live_ratelimit_probe"}

        for _ in range(5):
            assert await coupon_redeem_rl(_Req()) is None

        with pytest.raises(HTTPException) as excinfo:
            await coupon_redeem_rl(_Req())
        assert excinfo.value.status_code == 429

    def test_limit_is_tighter_than_the_general_user_surface(self):
        """5/60 -- the same budget as the faucet claim, and a twelfth of the
        60/60 used for ordinary reads. A person types one code, once."""
        from src.routes.coupons import coupon_redeem_rl as dep

        closure = {
            name: cell.cell_contents
            for name, cell in zip(dep.__code__.co_freevars, dep.__closure__ or (), strict=True)
        }
        assert closure["max_requests"] <= 5
        assert closure["window_seconds"] >= 60


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class FakeCouponDatabase:
    """A model of the Postgres mechanisms redeem_coupon() depends on.

    Models exactly three things, and nothing else:
      * `SELECT ... FOR UPDATE`  -- a per-coupon lock held to end of transaction
      * `UNIQUE (coupon_id, user_id)` on coupon_redemptions
      * `CHECK (times_used <= max_uses)` on coupons
    plus all-or-nothing rollback of a failed transaction.

    `row_lock` can be switched off to demonstrate that the lock is what makes
    the ceiling correct, and that the unique/check backstops still hold when it
    is gone -- i.e. that the layering is real and not decorative.
    """

    def __init__(self, coupon, *, row_lock=True, balance=5.0, parties=1):
        self.coupon = dict(coupon)
        self.redemptions: list[dict] = []
        self.credit_transactions: list[dict] = []
        self.balance = balance
        self.row_lock = row_lock
        self._lock = threading.Lock()
        # Rendezvous point: no caller starts its transaction until every caller
        # has arrived, so the race is forced rather than timing-dependent.
        # It sits OUTSIDE the row lock deliberately -- a barrier inside the
        # critical section cannot be reached by the waiter (the lock holder is
        # holding it), so it deadlocks and only "passes" on its timeout.
        # `parties=1` (the default, for single-request tests) releases at once.
        self._entry_barrier = threading.Barrier(parties, timeout=5)
        # Second rendezvous, used ONLY when the row lock is switched off: it
        # pins both callers to the same pre-write read of times_used, which is
        # the TOCTOU the lock exists to prevent. With the lock on, reaching this
        # point concurrently is impossible by construction.
        self._read_barrier = threading.Barrier(parties, timeout=5)

    @staticmethod
    def _rendezvous(barrier):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    def redeem(self, *, code, user_id, ip_address=None, user_agent=None):
        self._rendezvous(self._entry_barrier)
        if not self.row_lock:
            return self._redeem_txn(code, user_id, ip_address, user_agent)
        with self._lock:
            return self._redeem_txn(code, user_id, ip_address, user_agent)

    def _redeem_txn(self, code, user_id, ip_address, user_agent):
        coupon = self.coupon
        if coupon["code"].upper() != code.upper():
            return {"success": False, "error_code": "COUPON_NOT_FOUND", "error_message": "no"}

        # Eligibility, in redeem_coupon()'s order.
        if not coupon["is_active"]:
            return {"success": False, "error_code": "COUPON_INACTIVE", "error_message": "no"}
        if NOW < coupon["valid_from"]:
            return {"success": False, "error_code": "COUPON_NOT_YET_ACTIVE", "error_message": "no"}
        if NOW > coupon["valid_until"]:
            return {"success": False, "error_code": "COUPON_EXPIRED", "error_message": "no"}
        if coupon["coupon_scope"] == "user_specific" and coupon["assigned_to_user_id"] != user_id:
            return {"success": False, "error_code": "COUPON_NOT_ASSIGNED", "error_message": "no"}

        # The read side of the race. With the lock off, both callers arrive
        # here together and both see the same times_used -- so both believe
        # they may proceed, and only the database-side ceiling stops the
        # second payout.
        if not self.row_lock:
            self._rendezvous(self._read_barrier)

        if coupon["times_used"] >= coupon["max_uses"]:
            return {"success": False, "error_code": "MAX_USES_EXCEEDED", "error_message": "no"}
        if any(r["user_id"] == user_id for r in self.redemptions):
            return {"success": False, "error_code": "ALREADY_REDEEMED", "error_message": "no"}

        # --- writes; all-or-nothing from here -------------------------------
        value = coupon["value_usd"]
        snapshot = (list(self.redemptions), list(self.credit_transactions), self.balance)
        try:
            balance_before = self.balance
            self.balance += value
            self.credit_transactions.append(
                {"user_id": user_id, "amount": value, "type": "coupon_redemption"}
            )

            # UNIQUE (coupon_id, user_id)
            if any(r["user_id"] == user_id for r in self.redemptions):
                raise AssertionError("unique_violation: uq_coupon_user")
            self.redemptions.append(
                {
                    "coupon_id": coupon["id"],
                    "user_id": user_id,
                    "value_applied": value,
                    "user_balance_before": balance_before,
                    "user_balance_after": self.balance,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                }
            )

            # times_used = times_used + 1, then CHECK (times_used <= max_uses)
            coupon["times_used"] += 1
            if coupon["times_used"] > coupon["max_uses"]:
                raise AssertionError("check_violation: times_used_within_limit")
        except AssertionError as e:
            self.redemptions, self.credit_transactions, self.balance = snapshot
            coupon["times_used"] = min(coupon["times_used"], coupon["max_uses"])
            code_ = "ALREADY_REDEEMED" if "unique_violation" in str(e) else "MAX_USES_EXCEEDED"
            return {"success": False, "error_code": code_, "error_message": "no"}

        return {
            "success": True,
            "error_code": None,
            "error_message": None,
            "debug": None,
            "coupon_id": coupon["id"],
            "code": coupon["code"],
            "value_applied": value,
            "balance_before": balance_before,
            "balance_after": self.balance,
            "redemption_id": len(self.redemptions),
            "transaction_id": len(self.credit_transactions),
        }


_USERS_BY_ID = {USER["id"]: USER, OTHER_USER["id"]: OTHER_USER}


def _user_from_header(request: Request):
    """Pick the authenticated principal from a test-only header.

    TestClient dispatches the ASGI app on its own portal thread, so a
    thread-local set in the submitting thread is invisible inside the handler
    -- which silently made both concurrent requests authenticate as the SAME
    user, turning a two-user race into a one-user one. Carrying the id on the
    request is the only thing that survives the thread hop.
    """
    return _USERS_BY_ID[int(request.headers["X-Test-User-Id"])]


def _redeem_concurrently(fake, users):
    """Fire two real requests at the endpoint at the same instant."""
    assert fake._entry_barrier.parties == len(users), (
        "FakeCouponDatabase(parties=...) must match the number of concurrent "
        "callers, or the race never actually overlaps"
    )
    app.dependency_overrides[get_current_user] = _user_from_header

    def run(user):
        return TestClient(app).post(
            "/coupons/redeem",
            json={"code": "GATEWAYZ"},
            headers={"X-Test-User-Id": str(user["id"])},
        )

    with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, u) for u in users]
            return [f.result(timeout=15) for f in futures]


class TestConcurrentRedemption:
    def test_two_simultaneous_redemptions_of_a_single_use_coupon_grant_once(self):
        """The headline invariant: max_uses=1, two callers, exactly one grant."""
        fake = FakeCouponDatabase(make_coupon(max_uses=1), parties=2)
        responses = _redeem_concurrently(fake, [USER, OTHER_USER])

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 409], [r.json() for r in responses]
        assert len(fake.redemptions) == 1
        assert len(fake.credit_transactions) == 1
        assert fake.coupon["times_used"] == 1
        assert fake.balance == 25.0

    def test_the_loser_is_told_the_coupon_is_exhausted(self):
        fake = FakeCouponDatabase(make_coupon(max_uses=1), parties=2)
        responses = _redeem_concurrently(fake, [USER, OTHER_USER])
        loser = next(r for r in responses if r.status_code != 200)
        assert response_error(loser)["code"] == "coupon_max_uses_exceeded"

    def test_same_user_racing_itself_redeems_once(self):
        """uq_coupon_user, not the counter, is what catches this one."""
        fake = FakeCouponDatabase(make_coupon(max_uses=5), parties=2)
        responses = _redeem_concurrently(fake, [USER, USER])

        assert sorted(r.status_code for r in responses) == [200, 409]
        assert len(fake.redemptions) == 1
        assert len(fake.credit_transactions) == 1
        assert fake.coupon["times_used"] == 1

    def test_two_users_on_a_two_use_coupon_both_succeed(self):
        """The guard must not be a blanket lockout: a coupon with room for two
        redemptions grants two."""
        fake = FakeCouponDatabase(make_coupon(max_uses=2), parties=2)
        responses = _redeem_concurrently(fake, [USER, OTHER_USER])

        assert [r.status_code for r in responses] == [200, 200]
        assert len(fake.redemptions) == 2
        assert len(fake.credit_transactions) == 2
        assert fake.coupon["times_used"] == 2

    def test_without_the_row_lock_the_check_constraint_still_holds(self):
        """The layering is real, not decorative.

        With `FOR UPDATE` removed, both callers read times_used=0 and both
        believe they may proceed -- the exact TOCTOU that a Python-side
        check-then-write would have. The database-side ceiling
        (`times_used = times_used + 1` under CHECK times_used <= max_uses) is
        what still limits the payout to one. This is why the increment must
        never be computed in Python: if it were, this test would show two
        grants.
        """
        fake = FakeCouponDatabase(make_coupon(max_uses=1), row_lock=False, parties=2)
        responses = _redeem_concurrently(fake, [USER, OTHER_USER])

        assert sorted(r.status_code for r in responses) == [200, 409]
        assert len(fake.redemptions) == 1
        assert len(fake.credit_transactions) == 1
        assert fake.balance == 25.0

    def test_ledger_and_credit_transaction_are_written_together_or_not_at_all(self):
        """A granted credit with no redemption row (or the reverse) is a
        reconciliation problem nobody can settle later."""
        for max_uses, users in (
            (1, [USER, OTHER_USER]),
            (2, [USER, OTHER_USER]),
            (5, [USER, USER]),
        ):
            fake = FakeCouponDatabase(make_coupon(max_uses=max_uses), parties=len(users))
            _redeem_concurrently(fake, users)
            assert len(fake.redemptions) == len(fake.credit_transactions)
            assert fake.coupon["times_used"] == len(fake.redemptions)
            assert fake.coupon["times_used"] <= fake.coupon["max_uses"]

    def test_balance_math_on_the_ledger_row_is_self_consistent(self):
        """coupon_redemptions.balance_change_matches_value:
        user_balance_after = user_balance_before + value_applied."""
        fake = FakeCouponDatabase(make_coupon(max_uses=2), parties=2)
        _redeem_concurrently(fake, [USER, OTHER_USER])
        for row in fake.redemptions:
            assert row["user_balance_after"] == pytest.approx(
                row["user_balance_before"] + row["value_applied"]
            )


class TestScopeInvariants:
    """user_specific vs global, driven through the same model."""

    def test_user_specific_coupon_is_refused_for_anyone_else(self):
        fake = FakeCouponDatabase(
            make_coupon(coupon_scope="user_specific", assigned_to_user_id=USER["id"], max_uses=1)
        )
        app.dependency_overrides[get_current_user] = lambda: OTHER_USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            response = post()

        assert response.status_code == 403
        assert response_error(response)["code"] == "coupon_not_assigned"
        assert fake.redemptions == []
        assert fake.credit_transactions == []

    def test_user_specific_coupon_works_for_its_assignee(self):
        fake = FakeCouponDatabase(
            make_coupon(coupon_scope="user_specific", assigned_to_user_id=USER["id"], max_uses=1)
        )
        app.dependency_overrides[get_current_user] = lambda: USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            assert post().status_code == 200
        assert len(fake.redemptions) == 1

    def test_global_coupon_works_for_anyone(self):
        fake = FakeCouponDatabase(make_coupon(coupon_scope="global", max_uses=5))
        app.dependency_overrides[get_current_user] = lambda: OTHER_USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            assert post().status_code == 200

    def test_inactive_coupon_is_refused(self):
        fake = FakeCouponDatabase(make_coupon(is_active=False))
        app.dependency_overrides[get_current_user] = lambda: USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            response = post()
        assert response.status_code == 409
        assert response_error(response)["code"] == "coupon_inactive"
        assert fake.credit_transactions == []

    def test_expired_coupon_is_refused(self):
        fake = FakeCouponDatabase(make_coupon(valid_until=PAST))
        app.dependency_overrides[get_current_user] = lambda: USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            response = post()
        assert response.status_code == 410
        assert response_error(response)["code"] == "coupon_expired"
        assert fake.credit_transactions == []

    def test_not_yet_active_coupon_is_refused(self):
        fake = FakeCouponDatabase(make_coupon(valid_from=FUTURE, valid_until=FUTURE))
        app.dependency_overrides[get_current_user] = lambda: USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            response = post()
        assert response.status_code == 409
        assert response_error(response)["code"] == "coupon_not_yet_active"
        assert fake.credit_transactions == []

    def test_sequential_second_redemption_by_the_same_user_is_refused(self):
        """Not a race -- the plain "I clicked it twice, an hour apart" case."""
        fake = FakeCouponDatabase(make_coupon(max_uses=5))
        app.dependency_overrides[get_current_user] = lambda: USER
        with patch("src.routes.coupons.redeem_coupon", side_effect=fake.redeem):
            first = post()
            second = post()

        assert first.status_code == 200
        assert second.status_code == 409
        assert response_error(second)["code"] == "coupon_already_redeemed"
        assert len(fake.redemptions) == 1
        assert len(fake.credit_transactions) == 1
        assert fake.balance == 25.0
