"""Credit holds and unpaid balances against the real migrated schema
(CI-only). Migration 20261126190000.

* credit_hold refuses when the balance less the account's other live holds
  cannot cover the amount; expired holds do not count; a repeat for the same
  request returns the existing hold (0 for another account); credit_release
  drops one account's hold for one request.
* atomic_deduct_credits charges a request despite its own hold (and despite
  other holds) and deletes its own hold.
* charge_with_debt charges min(cost, balance), allowance first, and records
  the rest once, idempotently on request_id.
* atomic_add_credits pays unpaid balances first, oldest first.
* Every new function and table is service_role only, with RLS on.

Every test runs in one transaction, so now() is the same instant throughout a
test: expiry is simulated by editing expires_at.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal as D

from tests.sql.test_job_usage_v2_sql import _insert_user

ROLES = (("service_role", True), ("anon", False), ("authenticated", False))


def _account(cur, allowance="0", purchased="10") -> int:
    user_id = _insert_user(cur)
    cur.execute(
        "UPDATE public.users SET subscription_allowance = %s, purchased_credits = %s"
        " WHERE id = %s",
        (D(allowance), D(purchased), user_id),
    )
    return user_id


def _balances(cur, user_id) -> tuple[D, D]:
    cur.execute(
        "SELECT subscription_allowance, purchased_credits FROM public.users WHERE id = %s",
        (user_id,),
    )
    allowance, purchased = cur.fetchone()
    return D(str(allowance or 0)), D(str(purchased or 0))


def _hold(cur, user_id, amount, request_id=None) -> D:
    cur.execute(
        "SELECT public.credit_hold(%s, %s, %s, 900)",
        (user_id, amount, request_id or uuid.uuid4()),
    )
    return cur.fetchone()[0]


def _release(cur, request_id, user_id) -> D:
    cur.execute("SELECT public.credit_release(%s, %s)", (request_id, user_id))
    return cur.fetchone()[0]


def _holds(cur, request_id) -> list[tuple]:
    cur.execute(
        "SELECT user_id, amount_usd FROM public.credit_holds WHERE request_id = %s",
        (request_id,),
    )
    return cur.fetchall()


def _expire(cur, request_id):
    cur.execute(
        "UPDATE public.credit_holds SET expires_at = now() - interval '1 second'"
        " WHERE request_id = %s",
        (request_id,),
    )


def _deduct(cur, user_id, amount, request_id, from_allowance="0") -> dict:
    from_purchased = D(amount) - D(from_allowance)
    cur.execute(
        "SELECT public.atomic_deduct_credits(%s, %s, %s, %s, 'api_usage', 'test',"
        " '{}'::jsonb, %s)",
        (user_id, D(amount), D(from_allowance), from_purchased, request_id),
    )
    return cur.fetchone()[0]


def _charge_with_debt(cur, user_id, amount, request_id) -> dict:
    cur.execute(
        "SELECT public.charge_with_debt(%s, %s, 'api_usage', 'test', %s::jsonb, %s)",
        (user_id, D(amount), json.dumps({"model": "m"}), request_id),
    )
    return cur.fetchone()[0]


def _add(cur, user_id, credits, request_id=None) -> dict:
    cur.execute(
        "SELECT public.atomic_add_credits(%s, %s, 'purchase', 'test', 'purchased',"
        " NULL, '{}'::jsonb, %s, NULL)",
        (user_id, D(credits), request_id or uuid.uuid4()),
    )
    return cur.fetchone()[0]


def _debts(cur, user_id) -> list[tuple]:
    cur.execute(
        "SELECT amount_usd, settled_usd, settled_at IS NOT NULL FROM public.credit_debts"
        " WHERE user_id = %s ORDER BY created_at, id",
        (user_id,),
    )
    return cur.fetchall()


# --- credit_hold / credit_release ---------------------------------------------


def test_a_hold_is_refused_when_other_live_holds_use_the_balance(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        assert _hold(cur, user_id, D("6")) == D("6")
        assert _hold(cur, user_id, D("5")) == 0  # 10 - 6 < 5
        assert _hold(cur, user_id, D("4")) == D("4")  # exactly what is left
        assert _hold(cur, user_id, D("0.01")) == 0


def test_the_allowance_counts_toward_the_balance(db):
    with db.cursor() as cur:
        user_id = _account(cur, allowance="3", purchased="2")
        assert _hold(cur, user_id, D("5")) == D("5")
        assert _hold(cur, user_id, D("0.01")) == 0


def test_expired_holds_do_not_count_and_are_removed(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        old = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), old) == D("10")
        _expire(cur, old)
        assert _hold(cur, user_id, D("10")) == D("10")
        assert _holds(cur, old) == []


def test_a_repeat_hold_returns_the_existing_hold_for_the_same_account_only(db):
    with db.cursor() as cur:
        owner = _account(cur, purchased="10")
        other = _account(cur, purchased="10")
        request_id = uuid.uuid4()
        assert _hold(cur, owner, D("3"), request_id) == D("3")
        assert _hold(cur, owner, D("9"), request_id) == D("3")
        assert _hold(cur, other, D("3"), request_id) == 0
        assert _holds(cur, request_id) == [(owner, D("3"))]


def test_a_hold_rounds_up_to_the_ledger_unit(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="1")
        assert _hold(cur, user_id, D("0.000000001")) == D("0.00000001")


def test_release_frees_only_that_accounts_hold_for_that_request(db):
    with db.cursor() as cur:
        a = _account(cur)
        b = _account(cur)
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        _hold(cur, a, D("4"), mine)
        _hold(cur, b, D("4"), theirs)
        assert _release(cur, mine, b) == 0
        assert _release(cur, mine, a) == D("4")
        assert _release(cur, mine, a) == 0
        assert _holds(cur, theirs) == [(b, D("4"))]
        # The released amount is available again; the balance never changed.
        assert _hold(cur, a, D("10")) == D("10")
        assert _balances(cur, a) == (D("0"), D("10"))


def test_release_of_an_expired_hold_returns_zero(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        request_id = uuid.uuid4()
        _hold(cur, user_id, D("4"), request_id)
        _expire(cur, request_id)
        assert _release(cur, request_id, user_id) == 0
        assert _holds(cur, request_id) == []


# --- atomic_deduct_credits and holds ------------------------------------------


def test_a_request_is_charged_despite_its_own_hold_and_the_hold_is_deleted(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        request_id = uuid.uuid4()
        assert _hold(cur, user_id, D("10"), request_id) == D("10")
        result = _deduct(cur, user_id, "7", request_id)
        assert result["success"] is True, result
        assert _holds(cur, request_id) == []
        assert _balances(cur, user_id) == (D("0"), D("3"))


def test_other_live_holds_never_block_a_charge(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        _hold(cur, user_id, D("10"))  # another request holds everything
        result = _deduct(cur, user_id, "4", uuid.uuid4())
        assert result["success"] is True, result


def test_a_charged_request_cannot_be_held_again(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        request_id = uuid.uuid4()
        assert _deduct(cur, user_id, "1", request_id)["success"] is True
        assert _hold(cur, user_id, D("1"), request_id) == 0


# --- charge_with_debt -----------------------------------------------------------


def test_charge_with_debt_charges_the_balance_and_records_the_rest_once(db):
    with db.cursor() as cur:
        user_id = _account(cur, allowance="1", purchased="2")
        request_id = uuid.uuid4()
        _hold(cur, user_id, D("3"), request_id)
        result = _charge_with_debt(cur, user_id, "5", request_id)
        assert result["success"] is True, result
        assert D(str(result["charged_usd"])) == D("3")
        assert D(str(result["debt_usd"])) == D("2")
        assert _balances(cur, user_id) == (D("0"), D("0"))
        assert _debts(cur, user_id) == [(D("2"), D("0"), False)]
        assert _holds(cur, request_id) == []
        cur.execute(
            "SELECT amount, metadata->>'from_allowance', metadata->>'from_purchased'"
            " FROM public.credit_transactions WHERE request_id = %s",
            (request_id,),
        )
        amount, from_allowance, from_purchased = cur.fetchone()
        assert D(str(amount)) == D("-3")
        assert D(from_allowance) == D("1") and D(from_purchased) == D("2")

        repeat = _charge_with_debt(cur, user_id, "5", request_id)
        assert repeat["success"] is True and repeat["idempotent"] is True
        assert D(str(repeat["debt_usd"])) == D("2")
        assert _debts(cur, user_id) == [(D("2"), D("0"), False)]
        assert _balances(cur, user_id) == (D("0"), D("0"))


def test_charge_with_debt_on_an_empty_balance_records_only_debt(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="0")
        request_id = uuid.uuid4()
        result = _charge_with_debt(cur, user_id, "1.5", request_id)
        assert result["success"] is True and result["transaction_id"] is None
        assert _debts(cur, user_id) == [(D("1.5"), D("0"), False)]
        assert _charge_with_debt(cur, user_id, "1.5", request_id)["idempotent"] is True
        assert len(_debts(cur, user_id)) == 1


def test_charge_with_debt_within_the_balance_records_no_debt(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="10")
        result = _charge_with_debt(cur, user_id, "4", uuid.uuid4())
        assert D(str(result["debt_usd"])) == 0
        assert _debts(cur, user_id) == []
        assert _balances(cur, user_id) == (D("0"), D("6"))


def test_charge_with_debt_requires_a_request_id(db):
    with db.cursor() as cur:
        user_id = _account(cur)
        result = _charge_with_debt(cur, user_id, "1", None)
        assert result["success"] is False


# --- atomic_add_credits settles unpaid balances ---------------------------------


def test_added_credits_pay_unpaid_balances_oldest_first(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="0")
        first, second = uuid.uuid4(), uuid.uuid4()
        _charge_with_debt(cur, user_id, "2", first)
        _charge_with_debt(cur, user_id, "3", second)
        cur.execute(
            "UPDATE public.credit_debts SET created_at = now() - interval '1 hour'"
            " WHERE request_id = %s",
            (first,),
        )

        result = _add(cur, user_id, "4")
        assert result["success"] is True, result
        assert D(str(result["debt_settled_usd"])) == D("4")
        assert _balances(cur, user_id) == (D("0"), D("0"))
        assert _debts(cur, user_id) == [(D("2"), D("2"), True), (D("3"), D("2"), False)]
        cur.execute(
            "SELECT amount FROM public.credit_transactions"
            " WHERE user_id = %s AND transaction_type = 'debt_settlement'",
            (user_id,),
        )
        assert [D(str(r[0])) for r in cur.fetchall()] == [D("-4")]

        result = _add(cur, user_id, "10")
        assert D(str(result["debt_settled_usd"])) == D("1")
        assert _balances(cur, user_id) == (D("0"), D("9"))
        assert all(settled for *_, settled in _debts(cur, user_id))


def test_adding_credits_without_unpaid_balances_is_unchanged(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="1")
        result = _add(cur, user_id, "2")
        assert result["success"] is True and "debt_settled_usd" not in result
        assert _balances(cur, user_id) == (D("0"), D("3"))
        cur.execute(
            "SELECT count(*) FROM public.credit_transactions"
            " WHERE user_id = %s AND transaction_type = 'debt_settlement'",
            (user_id,),
        )
        assert cur.fetchone()[0] == 0


def test_a_repeated_grant_settles_nothing_more(db):
    with db.cursor() as cur:
        user_id = _account(cur, purchased="0")
        _charge_with_debt(cur, user_id, "2", uuid.uuid4())
        grant = uuid.uuid4()
        _add(cur, user_id, "5", grant)
        repeat = _add(cur, user_id, "5", grant)
        assert repeat["idempotent"] is True
        assert _balances(cur, user_id) == (D("0"), D("3"))


# --- Grants and RLS -------------------------------------------------------------


def test_only_the_service_role_may_call_the_new_and_replaced_functions(db):
    with db.cursor() as cur:
        for fn in (
            "public.credit_hold(bigint, numeric, uuid, integer)",
            "public.credit_release(uuid, bigint)",
            "public.charge_with_debt(bigint, numeric, character varying, text, jsonb, uuid)",
            "public.atomic_deduct_credits(bigint, numeric, numeric, numeric,"
            " character varying, text, jsonb, uuid)",
            "public.atomic_add_credits(bigint, numeric, character varying, text,"
            " character varying, bigint, jsonb, uuid, text)",
        ):
            for role, allowed in ROLES:
                cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
                assert cur.fetchone()[0] is allowed, (fn, role)


def test_the_new_tables_are_service_role_only_with_rls_on(db):
    with db.cursor() as cur:
        for table in ("credit_holds", "credit_debts"):
            cur.execute(
                "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n"
                " ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relname = %s",
                (table,),
            )
            assert cur.fetchone()[0] is True, table
            for role, allowed in ROLES:
                cur.execute(
                    "SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{table}")
                )
                assert cur.fetchone()[0] is allowed, (table, role)
