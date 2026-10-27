"""Static posture checks for 20261011120000_delegation_reward_share_discount.sql
(delegated staking v3): additive, service-role only, every balance function
SECURITY DEFINER with a pinned search_path, executable by service_role only,
and every balance change under a row lock / advisory lock and idempotent on a
UUID request_id.

The SQL behaviour itself is exercised against a real Postgres by
tests/security/test_reward_share_sql_live.py when GATEWAYZ_TEST_PG_DSN is set."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20261011120000_delegation_reward_share_discount.sql"
)
SQL = MIGRATION.read_text()
LOWER = SQL.lower()
TABLES = (
    "delegation_reward_attributions",
    "inference_discount_balances",
    "inference_discount_ledger",
    "inference_discount_holds",
)
SEQUENCES = ("delegation_reward_attributions_id_seq", "inference_discount_ledger_id_seq")
FUNCTIONS = {
    "discount_grant": "bigint, numeric, uuid, text, jsonb",
    "discount_apply": "bigint, numeric, numeric, uuid, jsonb",
    "discount_hold": "bigint, numeric, numeric, uuid, integer",
    "discount_reverse": "uuid",
    "discount_adjust": "bigint, numeric, uuid, text, bigint",
    "delegation_attribution_spent": "bigint, date, text[], bigint",
    "delegation_reserve_attribution": (
        "text, text, text, date, bigint, numeric, numeric, numeric, numeric, uuid, numeric, numeric"
    ),
    "delegation_grant_attribution": "bigint, bigint, numeric",
}


def _function_body(name: str) -> str:
    match = re.search(rf"create or replace function public\.{name}\((.*?)\$\$;", LOWER, re.S)
    assert match, f"{name} not defined"
    return match.group(0)


def _norm(sql: str) -> str:
    flat = re.sub(r"\s+", " ", sql).strip()
    return re.sub(r"\( ", "(", re.sub(r" \)", ")", flat))


def test_is_purely_additive():
    # Holds are deleted when settled or expired -- inside function bodies,
    # never as a top-level statement.
    top_level = re.sub(r"\$\$.*?\$\$", "", LOWER, flags=re.S)
    for statement in (
        r"drop table",
        r"drop column",
        r"\btruncate\b",
        r"delete from",
        r"alter column",
    ):
        assert not re.search(statement, top_level), statement
    assert "add column if not exists stake_total_native" in LOWER
    assert LOWER.count("create table if not exists") == 4


@pytest.mark.parametrize("table", TABLES)
def test_tables_rls_on_and_revoked(table):
    assert f"alter table public.{table} enable row level security" in LOWER
    assert f"revoke all on public.{table} from anon, authenticated" in LOWER
    assert f"grant all on public.{table} to service_role" in LOWER
    assert not re.search(rf"create policy[^;]*on public\.{table}", LOWER)


@pytest.mark.parametrize("sequence", SEQUENCES)
def test_sequences_revoked(sequence):
    assert f"revoke all on sequence public.{sequence} from anon, authenticated" in LOWER
    assert f"grant all on sequence public.{sequence} to service_role" in LOWER


@pytest.mark.parametrize("name,signature", FUNCTIONS.items())
def test_functions_definer_pinned_and_service_role_only(name, signature):
    body = _function_body(name)
    assert "security definer" in body
    assert "set search_path = public, pg_temp" in body
    flat = _norm(LOWER)
    assert (
        f"revoke all on function public.{name}({signature}) from public, anon, authenticated"
        in flat
    )
    assert f"grant execute on function public.{name}({signature}) to service_role" in flat
    assert not re.search(
        rf"grant execute on function public\.{name}\([^)]*\) to (anon|authenticated|public)", flat
    )


@pytest.mark.parametrize(
    "name", ["discount_grant", "discount_apply", "discount_adjust", "discount_hold"]
)
def test_balance_functions_lock_the_row_before_checking_idempotency(name):
    body = _function_body(name)
    check = "inference_discount_ledger where request_id = p_request_id"
    assert check in body
    assert body.index("for update") < body.index(check)


def test_reverse_locks_then_checks_its_own_key():
    body = _function_body("discount_reverse")
    assert body.index("for update") < body.index("where kind = 'reverse' and ref = v_ref")


@pytest.mark.parametrize("name", ["delegation_reserve_attribution", "delegation_grant_attribution"])
def test_attribution_functions_take_the_per_date_advisory_lock(name):
    body = _function_body(name)
    assert "pg_advisory_xact_lock(hashtext('delegation_attributions:'" in body
    # Fail closed: anything but an explicit "not paused" row is paused.
    assert "c.accruals_paused = false" in body


def test_request_ids_are_uuid_and_unique():
    assert "request_id uuid null unique" in LOWER
    assert "request_id uuid not null" in LOWER
    assert "uq_dra_request_id" in LOWER
    assert "uq_idl_reverse_ref" in LOWER


def test_balance_never_negative_and_money_has_6dp():
    assert "balance_usd numeric(38,6) not null default 0 check (balance_usd >= 0)" in LOWER
    assert "balance_after numeric(38,6) not null check (balance_after >= 0)" in LOWER
    assert "trunc(p_cost * v_percent, 6)" in LOWER


def test_holds_cap_concurrent_prechecks_and_apply_respects_them():
    hold = _function_body("discount_hold")
    assert "v_balance.balance_usd - v_held" in hold
    assert "expires_at <= now()" in hold
    apply = _function_body("discount_apply")
    assert "v_balance.balance_usd - v_others" in apply
    assert "where request_id = p_request_id and user_id = p_user_id" in apply
    assert "request_id = p_request_id and user_id <> p_user_id" in apply
    adjust = _function_body("discount_adjust")
    assert "from public.inference_discount_holds h" in adjust


def test_apply_caps_d_and_binds_idempotency_to_account_and_cost():
    body = _function_body("discount_apply")
    assert "least(greatest(coalesce(p_discount_percent, 0), 0), 0.99)" in body
    assert "v_existing.user_id = p_user_id" in body
    assert "(v_existing.metadata ->> 'cost')::numeric = p_cost" in body
    assert "jsonb_build_object('cost', p_cost" in body


def test_reserve_caps_at_revenue_and_grant_rechecks_link_and_account():
    reserve = _function_body("delegation_reserve_attribution")
    assert "v_revenue.revenue_usd - v_already" in reserve
    grant = _function_body("delegation_grant_attribution")
    assert "from public.user_wallets w" in grant
    assert "'inactive_user'" in grant
    assert "public.discount_grant(" in grant


def test_version_stamp_is_newer_than_the_migrations_it_builds_on():
    # It alters the delegated-staking tables and functions, so it must sort
    # after the migrations that create them. Later, unrelated migrations
    # (e.g. 20261020000000_usage_anchors.sql) are free to come after it.
    for base in (
        "20261008230000_delegated_staking.sql",
        "20261009010000_delegation_claim_inactive_user.sql",
    ):
        assert (MIGRATION.parent / base).exists()
        assert MIGRATION.name > base
