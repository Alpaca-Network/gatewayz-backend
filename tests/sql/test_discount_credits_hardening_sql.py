"""Discount credits settlement, cap timing and hold release, against the real
migrated schema (CI-only).

Migration 20261126120000. A request's own live hold is honoured at
settlement even when the cap was lowered after the hold was placed; a raised
cap takes effect 24 hours after it was requested; discount_release frees one
account's hold for one request and never changes a balance; the retired
delegation attribution functions are gone.

Every test runs in one transaction, so now() is the same instant throughout
a test: time is moved by editing the stored timestamps and dates.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from decimal import Decimal as D

from tests.sql.test_discount_daily_cap_sql import _account, _apply, _hold, _set_cap
from tests.sql.test_job_usage_v2_sql import _insert_user

ROLES = (("service_role", True), ("anon", False), ("authenticated", False))
DROPPED = (
    "public.delegation_grant_attribution(bigint, bigint, numeric)",
    "public.delegation_reserve_attribution(text, text, text, date, bigint, numeric, numeric,"
    " numeric, numeric, uuid, numeric, numeric)",
    "public.delegation_attribution_spent(bigint, date, text[], bigint)",
)


def _cap_row(cur, user_id) -> dict:
    cur.execute(
        """
        SELECT balance_usd, daily_cap_usd, cap_used_usd, cap_day,
               next_daily_cap_usd, next_daily_cap_from, next_daily_cap_at,
               now() AS now, (now() at time zone 'utc')::date AS today
        FROM public.inference_discount_balances WHERE user_id = %s
        """,
        (user_id,),
    )
    return dict(zip([c.name for c in cur.description], cur.fetchone(), strict=True))


def _release(cur, request_id, user_id) -> D:
    cur.execute("SELECT public.discount_release(%s, %s)", (request_id, user_id))
    return cur.fetchone()[0]


def _holds(cur, request_id) -> list[tuple]:
    cur.execute(
        "SELECT user_id, amount_usd FROM public.inference_discount_holds WHERE request_id = %s",
        (request_id,),
    )
    return cur.fetchall()


def _expire_hold(cur, request_id):
    cur.execute(
        "UPDATE public.inference_discount_holds"
        " SET expires_at = now() - interval '1 second' WHERE request_id = %s",
        (request_id,),
    )


# --- 1. A request's own live hold is honoured at settlement ----------------


def test_a_lowered_cap_never_shrinks_a_hold_already_placed(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        assert _apply(cur, user_id, D("2")) == D("1.98")
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("9.9")
        # Lowered below today's use plus the hold.
        assert _set_cap(cur, user_id, D("5"))["status"] == "set"
        assert _apply(cur, user_id, D("10"), request_id) == D("9.9")
        row = _cap_row(cur, user_id)
        # Today's use may end above the cap; it is recorded as it is.
        assert row["cap_used_usd"] == D("11.88") and row["daily_cap_usd"] == D("5")
        assert row["balance_usd"] == D("988.12")
        assert _holds(cur, request_id) == []
        # Any other request now draws nothing until the cap has room again.
        assert _apply(cur, user_id, D("10")) == 0


def test_the_own_hold_does_not_lift_the_cost_or_balance_bounds(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("9.9")
        _set_cap(cur, user_id, D("1"))
        # The request cost less than estimated: it draws d x cost, not the hold.
        assert _apply(cur, user_id, D("4"), request_id) == D("3.96")


def test_an_expired_hold_gives_no_protection(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("9.9")
        _set_cap(cur, user_id, D("1"))
        _expire_hold(cur, request_id)
        assert _apply(cur, user_id, D("10"), request_id) == D("1")
        assert _holds(cur, request_id) == []


def test_another_accounts_hold_on_the_same_request_gives_no_protection(db):
    with db.cursor() as cur:
        owner = _account(cur)
        other = _account(cur)
        request_id = uuid.uuid4()
        assert _hold(cur, owner, D("10"), request_id) == D("9.9")
        assert _apply(cur, other, D("10"), request_id) == 0
        assert _holds(cur, request_id) == [(owner, D("9.9"))]


# --- 2. A raised cap takes effect 24 hours after it was requested ----------


def test_a_raise_records_the_time_it_takes_effect(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        result = _set_cap(cur, user_id, D("20"))
        row = _cap_row(cur, user_id)
    assert result["status"] == "scheduled"
    assert result["next_daily_cap_at"] is not None
    assert (row["next_daily_cap_at"] - row["now"]).total_seconds() == 24 * 3600
    # The date column is still written, as the UTC date of that time.
    assert row["next_daily_cap_from"] == row["next_daily_cap_at"].astimezone(UTC).date()
    assert str(result["next_daily_cap_from"]) == row["next_daily_cap_from"].isoformat()


def test_a_raise_is_not_in_force_on_the_next_utc_date_before_24_hours(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        _set_cap(cur, user_id, D("20"))
        # The UTC date moves on (and the old date rule would apply the raise),
        # but 24 hours have not passed.
        cur.execute(
            "UPDATE public.inference_discount_balances"
            " SET cap_day = cap_day - 1, next_daily_cap_from = next_daily_cap_from - 1"
            " WHERE user_id = %s",
            (user_id,),
        )
        assert _apply(cur, user_id, D("100")) == D("5")
        row = _cap_row(cur, user_id)
        assert row["daily_cap_usd"] == D("5") and row["next_daily_cap_usd"] == D("20")
        assert row["next_daily_cap_at"] is not None


def test_a_raise_is_in_force_once_its_time_has_passed(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        _set_cap(cur, user_id, D("20"))
        assert _apply(cur, user_id, D("100")) == D("5")
        cur.execute(
            "UPDATE public.inference_discount_balances SET next_daily_cap_at = now()"
            " WHERE user_id = %s",
            (user_id,),
        )
        # Same UTC day: the 5 already used still counts against the new cap.
        assert _apply(cur, user_id, D("100")) == D("15")
        row = _cap_row(cur, user_id)
    assert row["daily_cap_usd"] == D("20")
    assert row["next_daily_cap_usd"] is None
    assert row["next_daily_cap_from"] is None and row["next_daily_cap_at"] is None


def test_rows_written_before_the_timestamp_keep_the_date_rule(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        cur.execute(
            "UPDATE public.inference_discount_balances"
            " SET next_daily_cap_usd = 20, next_daily_cap_at = null,"
            " next_daily_cap_from = (now() at time zone 'utc')::date + 1"
            " WHERE user_id = %s",
            (user_id,),
        )
        assert _apply(cur, user_id, D("100")) == D("5")  # its date has not come
        cur.execute(
            "UPDATE public.inference_discount_balances"
            " SET cap_day = cap_day - 1, next_daily_cap_from = next_daily_cap_from - 1"
            " WHERE user_id = %s",
            (user_id,),
        )
        assert _apply(cur, user_id, D("100")) == D("20")  # a new day, under the new cap
        row = _cap_row(cur, user_id)
    assert row["daily_cap_usd"] == D("20") and row["next_daily_cap_from"] is None


def test_lowering_clears_the_pending_time_too(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        _set_cap(cur, user_id, D("20"))
        result = _set_cap(cur, user_id, D("4"))
        row = _cap_row(cur, user_id)
    assert result["status"] == "set" and result["next_daily_cap_at"] is None
    assert row["next_daily_cap_at"] is None and row["next_daily_cap_usd"] is None


# --- 3. discount_release ----------------------------------------------------


def test_release_frees_only_that_accounts_hold_for_that_request(db):
    with db.cursor() as cur:
        a = _account(cur)
        b = _account(cur)
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        assert _hold(cur, a, D("10"), mine) == D("9.9")
        assert _hold(cur, b, D("10"), theirs) == D("9.9")

        # Another account's id, or another request: nothing is released.
        assert _release(cur, mine, b) == 0
        assert _release(cur, theirs, a) == 0
        assert _holds(cur, mine) == [(a, D("9.9"))]
        assert _holds(cur, theirs) == [(b, D("9.9"))]

        assert _release(cur, mine, a) == D("9.9")
        assert _holds(cur, mine) == []
        assert _release(cur, mine, a) == 0  # a repeat releases nothing
        assert _holds(cur, theirs) == [(b, D("9.9"))]


def test_release_never_changes_a_balance(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        before = _cap_row(cur, user_id)
        request_id = uuid.uuid4()
        _hold(cur, user_id, D("10"), request_id)
        _release(cur, request_id, user_id)
        after = _cap_row(cur, user_id)
        assert after["balance_usd"] == before["balance_usd"] == D("1000")
        assert after["cap_used_usd"] == before["cap_used_usd"]
        cur.execute(
            "SELECT count(*) FROM public.inference_discount_ledger"
            " WHERE user_id = %s AND kind <> 'grant'",
            (user_id,),
        )
        assert cur.fetchone()[0] == 0


def test_a_released_hold_frees_its_part_of_the_cap(db):
    with db.cursor() as cur:
        user_id = _account(cur, cap="5")
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("5")
        assert _hold(cur, user_id, D("10")) == 0
        _release(cur, request_id, user_id)
        assert _hold(cur, user_id, D("10")) == D("5")


def test_release_after_settlement_or_expiry_returns_zero(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        settled, expired = uuid.uuid4(), uuid.uuid4()
        _hold(cur, user_id, D("10"), settled)
        assert _apply(cur, user_id, D("10"), settled) == D("9.9")
        assert _release(cur, settled, user_id) == 0
        _hold(cur, user_id, D("10"), expired)
        _expire_hold(cur, expired)
        assert _release(cur, expired, user_id) == 0
        assert _holds(cur, expired) == []
        assert _cap_row(cur, user_id)["balance_usd"] == D("990.1")


def test_release_with_missing_arguments_is_a_no_op(db):
    with db.cursor() as cur:
        user_id = _insert_user(cur)
        assert _release(cur, None, user_id) == 0
        assert _release(cur, uuid.uuid4(), None) == 0
        assert _release(cur, uuid.uuid4(), user_id) == 0  # no balance row at all


# --- 4. Retired functions and grants ---------------------------------------


def test_the_retired_delegation_functions_are_gone(db):
    with db.cursor() as cur:
        for fn in DROPPED:
            cur.execute("SELECT to_regprocedure(%s)", (fn,))
            assert cur.fetchone()[0] is None, fn


def test_only_the_service_role_may_release_a_hold(db):
    with db.cursor() as cur:
        for role, allowed in ROLES:
            cur.execute(
                "SELECT has_function_privilege(%s, 'public.discount_release(uuid, bigint)',"
                " 'EXECUTE')",
                (role,),
            )
            assert cur.fetchone()[0] is allowed, role


def test_replaced_functions_keep_service_role_only_grants(db):
    with db.cursor() as cur:
        for fn in (
            "public.discount_apply(bigint, numeric, numeric, uuid, jsonb)",
            "public.discount_roll_day(bigint)",
            "public.discount_set_daily_cap(bigint, numeric)",
        ):
            for role, allowed in ROLES:
                cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
                assert cur.fetchone()[0] is allowed, (fn, role)
