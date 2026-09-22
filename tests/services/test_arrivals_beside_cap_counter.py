"""A key's cap counter is not an answer to "did anything reach this key?".

On 2026-09-21 a partner key showed `requests_used: 1` while every call to the
vendor it pointed at was failing. 1 is equally consistent with "one call ever"
and with "six days of 503s", because a REJECTED call does not consume the cap.
Settling it took a hand-run experiment against production: sending a failing
call and checking which field moved.

`requests_used` is correct for what it meters. It is simply the wrong number
for this question, and nothing in the per-key view answered the right one.

Two properties are pinned here:

1. Arrivals and failures appear BESIDE the cap counter, never replacing it --
   the cap counter still has a job.
2. When the rollup cannot be read, the arrival fields are ABSENT and
   `arrivals_measured` is false. Never 0. A confident zero from an unread
   source is the same defect this feature exists to remove, relocated.

Filed under tests/services/, NOT tests/db/, on purpose: conftest skips anything
whose path contains "db" when no database is reachable. These tests need none --
they mock the client entirely -- and under tests/db/ all seven would have been
skipped in CI. Green, and proving nothing.
"""

from __future__ import annotations

from unittest.mock import patch

import src.db.api_keys as api_keys_db

KEY_ROWS = [
    {
        "id": 11,
        "api_key": "gw_live_aaaaaaaaaaaa",
        "key_name": "ops",
        "is_active": True,
        "requests_used": 1,
        "max_requests": 50000,
        "last_used_at": "2026-09-09T13:38:21Z",
    },
    {
        "id": 12,
        "api_key": "gw_live_bbbbbbbbbbbb",
        "key_name": "staging",
        "is_active": True,
        "requests_used": 0,
        "max_requests": 20000,
        "last_used_at": None,
    },
]

# Key 11 is the real shape of the incident: one completed call, many failures,
# and a cap counter that never noticed.
ARRIVALS = [
    {
        "api_key_id": 11,
        "arrivals": 41,
        "completed": 1,
        "failed": 40,
        "input_tokens": 191,
        "output_tokens": 3,
        "cost_usd": 0.00103,
        "first_arrival_at": "2026-09-09T13:38:21Z",
        "last_arrival_at": "2026-09-21T23:28:19Z",
        "last_failure_at": "2026-09-21T23:28:19Z",
    },
]


def _usage(arrivals_rows=ARRIVALS, rollup_raises=False, keys=KEY_ROWS):
    class _Result:
        def __init__(self, data):
            self.data = data

    class _Table:
        def select(self, *_):
            return self

        def eq(self, *_):
            return self

        def execute(self):
            return _Result(keys)

    class _Rpc:
        def execute(self):
            if rollup_raises:
                raise RuntimeError("rollup unavailable")
            return _Result(arrivals_rows)

    class _Client:
        def table(self, *_):
            return _Table()

        def rpc(self, *_a, **_k):
            return _Rpc()

    with patch.object(api_keys_db, "get_supabase_client", lambda: _Client()):
        return api_keys_db.get_user_all_api_keys_usage(1)


def _key(out, key_id):
    return next(k for k in out["keys"] if k["key_id"] == key_id)


def test_failures_are_visible_without_a_second_request():
    k = _key(_usage(), 11)
    assert k["arrivals"] == 41
    assert k["failed"] == 40


def test_the_cap_counter_is_not_replaced():
    # It still meters the cap, and the two numbers disagreeing IS the signal --
    # 1 consumed against 41 arrived is exactly the state that was unreadable.
    k = _key(_usage(), 11)
    assert k["requests_used"] == 1
    assert k["arrivals"] > k["requests_used"]


def test_failures_are_never_folded_into_the_totals():
    # A failed call cost real compute; averaging it into spend flatters the
    # caller that spent it. Same rule as the tag rollup.
    k = _key(_usage(), 11)
    assert k["arrivals"] == k["failed"] + 41 - 40
    assert k["failed"] == 40


def test_a_key_nothing_reached_reports_zero_not_absent():
    # Genuinely nothing arrived. That IS knowable, and must read as 0.
    k = _key(_usage(), 12)
    assert k["arrivals"] == 0
    assert k["failed"] == 0


def test_an_unreadable_rollup_reports_absent_not_zero():
    # THE point. A rollup that did not run must not look like a quiet key.
    out = _usage(rollup_raises=True)
    assert out["arrivals_measured"] is False
    for k in out["keys"]:
        assert "arrivals" not in k, "an unread source reported a confident zero"
        assert "failed" not in k
    # The cap counters still come back -- one read failing must not blank the view.
    assert _key(out, 11)["requests_used"] == 1


def test_a_successful_read_says_so():
    assert _usage()["arrivals_measured"] is True


def test_the_last_failure_timestamp_survives():
    # "When did something last reach this key, and when did it last break"
    # is the pair a failing integration actually needs.
    k = _key(_usage(), 11)
    assert k["last_arrival_at"] == "2026-09-21T23:28:19Z"
    assert k["last_failure_at"] == "2026-09-21T23:28:19Z"
