"""Migration 20261126140000 against the real migrated schema (CI-only): the
retired delegation programme's last SQL functions are gone, and the tables
are kept."""

from __future__ import annotations

DROPPED = (
    "public.delegation_account_spent(bigint, date, text[], bigint)",
    "public.delegation_reserve_accrual(text, text, date, numeric, numeric, numeric, bigint,"
    " numeric, numeric)",
    "public.delegation_claim_accrual(bigint, bigint, numeric)",
    "public.delegation_release_claim(bigint, bigint)",
)
NAMES = (
    "delegation_account_spent",
    "delegation_reserve_accrual",
    "delegation_claim_accrual",
    "delegation_release_claim",
)


def test_the_functions_are_gone(db):
    with db.cursor() as cur:
        for fn in DROPPED:
            cur.execute("SELECT to_regprocedure(%s)", (fn,))
            assert cur.fetchone()[0] is None, fn


def test_no_overload_remains(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
            (list(NAMES),),
        )
        assert cur.fetchall() == []


def test_the_eligibility_table_is_kept(db):
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('public.delegation_eligibility')")
        assert cur.fetchone()[0] is not None
