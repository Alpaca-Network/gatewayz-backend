"""Static posture checks for 20261126120000_discount_credits_hardening.sql.

The behaviour is tested against a real database in
tests/sql/test_discount_credits_hardening_sql.py (CI only); these checks run
everywhere: every function stays SECURITY DEFINER with a pinned search_path,
the new one is service_role only, the retired functions are dropped by exact
signature, no table is dropped, and the settlement rule reads the request's
own hold before expired holds are deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20261126120000_discount_credits_hardening.sql"
LOWER = MIGRATION.read_text().lower()
FUNCTIONS = ("discount_roll_day", "discount_apply", "discount_set_daily_cap", "discount_release")


def _body(name: str) -> str:
    match = re.search(rf"create or replace function public\.{name}\((.*?)\$\$;", LOWER, re.S)
    assert match, f"{name} not defined"
    return match.group(0)


def _flat(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


def test_sorts_after_the_migrations_it_replaces():
    for base in (
        "20261011120000_delegation_reward_share_discount.sql",
        "20261125120000_discount_daily_cap.sql",
    ):
        assert (MIGRATIONS / base).exists()
        assert MIGRATION.name > base


def test_every_function_is_definer_with_a_pinned_search_path():
    for name in FUNCTIONS:
        body = _body(name)
        assert "security definer" in body, name
        assert "set search_path = public, pg_temp" in body, name


def test_the_new_function_is_service_role_only():
    flat = _flat(LOWER)
    assert (
        "revoke all on function public.discount_release(uuid, bigint) "
        "from public, anon, authenticated"
    ) in flat
    assert "grant execute on function public.discount_release(uuid, bigint) to service_role" in flat
    assert not re.search(r"grant execute[^;]*to (anon|authenticated|public)", flat)


def test_retired_functions_are_dropped_by_exact_signature_and_no_table_is():
    flat = _flat(LOWER)
    for signature in (
        "delegation_grant_attribution(bigint, bigint, numeric)",
        "delegation_reserve_attribution( text, text, text, date, bigint, numeric, numeric, "
        "numeric, numeric, uuid, numeric, numeric)",
        "delegation_attribution_spent(bigint, date, text[], bigint)",
    ):
        assert f"drop function if exists public.{signature}" in flat, signature
    top_level = re.sub(r"\$\$.*?\$\$", "", LOWER, flags=re.S)
    for statement in ("drop table", "drop column", r"\btruncate\b", "alter column"):
        assert not re.search(statement, top_level), statement


def test_apply_reads_its_own_live_hold_before_deleting_expired_holds():
    body = _flat(_body("discount_apply"))
    own = body.index(
        "where request_id = p_request_id and user_id = p_user_id and expires_at > now()"
    )
    expired = body.index("where user_id = p_user_id and expires_at <= now()")
    assert own < expired
    assert "greatest(v_own, v_balance.daily_cap_usd - v_balance.cap_used_usd - v_others)" in body
    # The balance term is unchanged: the balance never goes negative.
    assert "v_balance.balance_usd - v_others" in body


def test_a_raise_waits_24_hours_and_old_rows_keep_the_date_rule():
    set_cap = _flat(_body("discount_set_daily_cap"))
    assert "v_at := now() + interval '24 hours'" in set_cap
    assert "next_daily_cap_from = (v_at at time zone 'utc')::date" in set_cap
    roll = _flat(_body("discount_roll_day"))
    assert "v_row.next_daily_cap_at <= now()" in roll
    assert "v_row.next_daily_cap_at is null and v_row.next_daily_cap_from is not null" in roll


def test_release_never_touches_a_balance():
    body = _body("discount_release")
    assert "update public.inference_discount_balances" not in body
    assert "insert into public.inference_discount_ledger" not in body
    assert "where request_id = p_request_id and user_id = p_user_id" in _flat(body)
