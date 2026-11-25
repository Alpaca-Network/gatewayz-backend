"""The daily cap on discount credits, against the real migrated schema (CI-only).

Migration 20261125120000. An account draws at most its cap per UTC day; when
the cap is reached a request draws less or nothing (it is never refused);
lowering the cap applies at once and raising it applies from the next day.
"""

from __future__ import annotations

import uuid
from decimal import Decimal as D

from tests.sql.test_job_usage_v2_sql import _insert_user

PCT = D("0.99")
NEW_FUNCTIONS = (
    "public.discount_roll_day(bigint)",
    "public.discount_set_daily_cap(bigint, numeric)",
)
ROLES = (("service_role", True), ("anon", False), ("authenticated", False))


def _account(cur, balance="1000", cap=None) -> int:
    user_id = _insert_user(cur)
    cur.execute(
        "SELECT public.discount_grant(%s, %s, %s, 'test', '{}'::jsonb)",
        (user_id, balance, uuid.uuid4()),
    )
    if cap is not None:
        cur.execute(
            "UPDATE public.inference_discount_balances SET daily_cap_usd = %s WHERE user_id = %s",
            (cap, user_id),
        )
    return user_id


def _apply(cur, user_id, cost, request_id=None) -> D:
    cur.execute(
        "SELECT public.discount_apply(%s, %s, %s, %s, '{}'::jsonb)",
        (user_id, cost, PCT, request_id or uuid.uuid4()),
    )
    return cur.fetchone()[0]


def _hold(cur, user_id, estimate, request_id=None) -> D:
    cur.execute(
        "SELECT public.discount_hold(%s, %s, %s, %s, 900)",
        (user_id, estimate, PCT, request_id or uuid.uuid4()),
    )
    return cur.fetchone()[0]


def _row(cur, user_id) -> dict:
    cur.execute(
        """
        SELECT balance_usd, daily_cap_usd, cap_used_usd, cap_day,
               next_daily_cap_usd, next_daily_cap_from,
               (now() at time zone 'utc')::date AS today
        FROM public.inference_discount_balances WHERE user_id = %s
        """,
        (user_id,),
    )
    return dict(zip([c.name for c in cur.description], cur.fetchone(), strict=True))


def _set_cap(cur, user_id, cap) -> dict:
    cur.execute("SELECT public.discount_set_daily_cap(%s, %s)", (user_id, cap))
    return cur.fetchone()[0]


def _yesterday(cur, user_id):
    """Make the row look as if it was last touched yesterday (UTC)."""
    cur.execute(
        "UPDATE public.inference_discount_balances SET cap_day = cap_day - 1 WHERE user_id = %s",
        (user_id,),
    )


def test_new_accounts_get_the_default_cap(db):
    with db.cursor() as cur:
        row = _row(cur, _account(cur))
    assert row["daily_cap_usd"] == D("50")
    assert row["cap_used_usd"] == 0 and row["cap_day"] == row["today"]


def test_under_the_cap_nothing_changes(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        assert _apply(cur, user_id, D("10")) == D("9.9")
        row = _row(cur, user_id)
    assert row["cap_used_usd"] == D("9.9") and row["balance_usd"] == D("990.1")


def test_a_request_that_crosses_the_cap_draws_only_what_is_left(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        assert _apply(cur, user_id, D("4")) == D("3.96")
        assert _apply(cur, user_id, D("4")) == D("1.04")  # 5 - 3.96
        assert _apply(cur, user_id, D("4")) == 0  # cap reached: full price, not refused
        row = _row(cur, user_id)
    assert row["cap_used_usd"] == D("5") and row["balance_usd"] == D("995")


def test_a_cap_of_zero_stops_discount_use(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="0")
        assert _apply(cur, user_id, D("4")) == 0
        assert _hold(cur, user_id, D("4")) == 0


def test_the_cap_starts_again_on_a_new_utc_day(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        assert _apply(cur, user_id, D("10")) == D("5")
        assert _apply(cur, user_id, D("10")) == 0
        _yesterday(cur, user_id)
        assert _apply(cur, user_id, D("10")) == D("5")
        row = _row(cur, user_id)
    assert row["cap_day"] == row["today"] and row["cap_used_usd"] == D("5")


def test_a_hold_reserves_no_more_than_what_is_left_of_the_cap(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        assert _apply(cur, user_id, D("2")) == D("1.98")
        assert _hold(cur, user_id, D("10")) == D("3.02")
        # A second request cannot count the part of the cap the first one holds.
        assert _hold(cur, user_id, D("10")) == 0
        assert _apply(cur, user_id, D("10")) == 0


def test_a_request_settles_against_its_own_hold_within_the_cap(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("5")
        assert _apply(cur, user_id, D("3"), request_id) == D("2.97")
        # The unused part of the hold is released: the rest of the cap is free.
        assert _apply(cur, user_id, D("10")) == D("2.03")


def test_a_repeat_of_the_same_request_draws_once(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        request_id = uuid.uuid4()
        assert _apply(cur, user_id, D("2"), request_id) == D("1.98")
        assert _apply(cur, user_id, D("2"), request_id) == D("1.98")
        assert _row(cur, user_id)["cap_used_usd"] == D("1.98")


def test_reversing_todays_use_frees_todays_cap(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        request_id = uuid.uuid4()
        assert _apply(cur, user_id, D("10"), request_id) == D("5")
        cur.execute("SELECT public.discount_reverse(%s)", (request_id,))
        assert cur.fetchone()[0] == D("5")
        row = _row(cur, user_id)
        assert row["cap_used_usd"] == 0 and row["balance_usd"] == D("1000")
        assert _apply(cur, user_id, D("10")) == D("5")


def test_reversing_an_older_use_does_not_raise_todays_allowance(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        old = uuid.uuid4()
        assert _apply(cur, user_id, D("10"), old) == D("5")
        cur.execute(
            "UPDATE public.inference_discount_ledger"
            " SET created_at = created_at - interval '1 day' WHERE request_id = %s",
            (old,),
        )
        _yesterday(cur, user_id)
        assert _apply(cur, user_id, D("2")) == D("1.98")  # today
        cur.execute("SELECT public.discount_reverse(%s)", (old,))
        assert cur.fetchone()[0] == D("5")
        row = _row(cur, user_id)
    assert row["cap_used_usd"] == D("1.98")  # unchanged by yesterday's refund
    assert row["balance_usd"] == D("998.02")


def test_lowering_the_cap_applies_at_once(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        result = _set_cap(cur, user_id, D("3"))
        assert result["status"] == "set" and D(str(result["daily_cap_usd"])) == D("3")
        assert _apply(cur, user_id, D("10")) == D("3")


def test_raising_the_cap_waits_for_the_next_day(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        result = _set_cap(cur, user_id, D("20"))
        assert result["status"] == "scheduled"
        assert D(str(result["daily_cap_usd"])) == D("5")
        assert D(str(result["next_daily_cap_usd"])) == D("20")
        row = _row(cur, user_id)
        assert (row["next_daily_cap_from"] - row["today"]).days == 1
        assert row["daily_cap_usd"] == D("5")
        # Today is still bounded by the old cap.
        assert _apply(cur, user_id, D("100")) == D("5")
        assert _apply(cur, user_id, D("100")) == 0
        # The next day: move the row and the pending date back by one day.
        cur.execute(
            "UPDATE public.inference_discount_balances"
            " SET cap_day = cap_day - 1, next_daily_cap_from = next_daily_cap_from - 1"
            " WHERE user_id = %s",
            (user_id,),
        )
        assert _apply(cur, user_id, D("100")) == D("20")
        row = _row(cur, user_id)
    assert row["daily_cap_usd"] == D("20") and row["next_daily_cap_usd"] is None


def test_lowering_cancels_a_pending_raise(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        _set_cap(cur, user_id, D("20"))
        assert _set_cap(cur, user_id, D("4"))["status"] == "set"
        row = _row(cur, user_id)
    assert row["daily_cap_usd"] == D("4")
    assert row["next_daily_cap_usd"] is None and row["next_daily_cap_from"] is None


def test_bad_caps_are_refused(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        for bad in (D("-1"), D("1000001"), None):
            assert _set_cap(cur, user_id, bad)["status"] == "invalid"
        assert _row(cur, user_id)["daily_cap_usd"] == D("50")


def test_setting_a_cap_before_any_grant_creates_the_row_and_no_credits(db):
    with db.cursor() as cur:
        user_id = _insert_user(cur)
        assert _set_cap(cur, user_id, D("10"))["status"] == "set"
        row = _row(cur, user_id)
    assert row["balance_usd"] == 0 and row["daily_cap_usd"] == D("10")


def test_only_the_service_role_may_call_the_new_functions(db):
    with db.cursor() as cur:
        for fn in NEW_FUNCTIONS:
            for role, allowed in ROLES:
                cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
                assert cur.fetchone()[0] is allowed, (fn, role)
