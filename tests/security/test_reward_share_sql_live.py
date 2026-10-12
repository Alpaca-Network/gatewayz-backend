"""Behaviour of the v3 SQL functions against a REAL Postgres -- opt-in.

Set GATEWAYZ_TEST_PG_DSN to a throwaway local database URL (never a shared or
remote one: this creates and drops a schema's worth of objects) with `psql`
on PATH. The test builds minimal users / user_wallets stubs, applies the v2
and v3 delegation migrations, and checks the money invariants that only the
database can enforce: idempotency, row locks under concurrency, the revenue
ceiling, fail-closed pause and the never-negative balance.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import pytest

DSN = os.environ.get("GATEWAYZ_TEST_PG_DSN")
# This drops and recreates the `public` schema: refuse anything but localhost.
_LOCAL = bool(DSN) and urlparse(DSN).hostname in ("127.0.0.1", "localhost", "::1")
pytestmark = pytest.mark.skipif(
    not _LOCAL or not shutil.which("psql"),
    reason="set GATEWAYZ_TEST_PG_DSN to a LOCAL throwaway Postgres",
)
MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
FILES = (
    "20261008230000_delegated_staking.sql",
    "20261009010000_delegation_claim_inactive_user.sql",
    "20261011120000_delegation_reward_share_discount.sql",
)


def _worker_dsn() -> str | None:
    """Each xdist worker gets its own throwaway database next to DSN's, so
    parallel workers never drop each other's schema."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not DSN or not worker:
        return DSN
    parts = urlparse(DSN)
    name = f"{(parts.path or '/postgres').lstrip('/') or 'postgres'}_{worker}"
    for sql in (f"drop database if exists {name}", f"create database {name}"):
        subprocess.run(["psql", DSN, "-qtAX", "-c", sql], capture_output=True, check=True)
    return parts._replace(path=f"/{name}").geturl()


TARGET = DSN


def psql(sql: str) -> str:
    out = subprocess.run(
        ["psql", TARGET, "-v", "ON_ERROR_STOP=1", "-qtAX", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def schema():
    global TARGET
    TARGET = _worker_dsn()
    psql("""
        drop schema if exists public cascade; create schema public;
        do $$ begin
          if not exists (select 1 from pg_roles where rolname='anon') then create role anon; end if;
          if not exists (select 1 from pg_roles where rolname='authenticated') then
            create role authenticated; end if;
          if not exists (select 1 from pg_roles where rolname='service_role') then
            create role service_role; end if;
        end $$;
        -- Supabase's default privileges: every new function, table and
        -- sequence in public is granted to anon / authenticated / service_role
        -- unless a migration revokes it.
        alter default privileges in schema public
          grant all on tables to anon, authenticated, service_role;
        alter default privileges in schema public
          grant all on sequences to anon, authenticated, service_role;
        alter default privileges in schema public
          grant all on functions to anon, authenticated, service_role;
        create table public.users (id bigserial primary key, email text, is_active boolean default true);
        create table public.user_wallets (id bigserial primary key, user_id bigint references users(id),
          wallet_address text unique not null, source text check (source in ('privy','siwe')),
          chain_namespace text default 'eip155', created_at timestamptz default now());
        insert into users (id) values (1), (2), (3);
        update users set is_active = false where id = 3;
        insert into user_wallets (user_id, wallet_address, source)
          values (1, '0xaaa', 'siwe'), (2, '0xbbb', 'siwe'), (3, '0xccc', 'siwe');
        """)
    for name in FILES + FILES[-1:]:  # the v3 file twice: re-runs are no-ops
        subprocess.run(
            ["psql", TARGET, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(MIGRATIONS / name)],
            capture_output=True,
            check=True,
        )
    yield


def u() -> str:
    return str(uuid.uuid4())


def balance(user_id: int) -> str:
    return psql(f"select balance_usd from inference_discount_balances where user_id = {user_id}")


def test_grant_is_idempotent_and_truncates():
    rid = u()
    psql(f"select discount_grant(1, 10.1234567, '{rid}', 'r', '{{}}')")
    psql(f"select discount_grant(1, 10.1234567, '{rid}', 'r', '{{}}')")
    assert balance(1) == "10.123456"


def test_apply_min_of_cost_and_balance_idempotent_then_reverse_once():
    psql(f"select discount_grant(2, 3, '{u()}', 'r', '{{}}')")
    rid = u()
    assert psql(f"select discount_apply(2, 2, 0.99, '{rid}', '{{}}')") == "1.980000"
    assert psql(f"select discount_apply(2, 2, 0.99, '{rid}', '{{}}')") == "1.980000"
    assert balance(2) == "1.020000"
    big = u()
    assert psql(f"select discount_apply(2, 100, 0.99, '{big}', '{{}}')") == "1.020000"
    assert balance(2) == "0.000000"
    assert psql(f"select discount_reverse('{big}')") == "1.020000"
    assert psql(f"select discount_reverse('{big}')") == "0"
    # A reversed request never draws again.
    assert psql(f"select discount_apply(2, 100, 0.99, '{big}', '{{}}')") == "0"
    assert balance(2) == "1.020000"


def test_idempotency_is_bound_to_account_and_cost():
    """Security review: one request_id can never fund a second request -- a
    reuse by another account or for another cost gets no discount."""
    psql("insert into users (id) values (13), (14)")
    psql(f"select discount_grant(13, 50, '{u()}', 'r', '{{}}')")
    psql(f"select discount_grant(14, 50, '{u()}', 'r', '{{}}')")
    rid = u()
    assert psql(f"select discount_apply(13, 2, 0.99, '{rid}', '{{}}')") == "1.980000"
    assert psql(f"select discount_apply(13, 2.00000000, 0.99, '{rid}', '{{}}')") == "1.980000"
    assert psql(f"select discount_apply(13, 3, 0.99, '{rid}', '{{}}')") == "0"
    assert psql(f"select discount_apply(14, 2, 0.99, '{rid}', '{{}}')") == "0"
    assert balance(13) == "48.020000" and balance(14) == "50.000000"


def test_discount_percent_is_capped_below_one():
    psql("insert into users (id) values (15)")
    psql(f"select discount_grant(15, 50, '{u()}', 'r', '{{}}')")
    assert psql(f"select discount_apply(15, 2, 1, '{u()}', '{{}}')") == "1.980000"
    assert psql(f"select discount_apply(15, 2, 7, '{u()}', '{{}}')") == "1.980000"


def test_concurrent_prechecks_cannot_amplify_unpaid_inference():
    """Security review ("precheck amplification"): $0.01 credits, $10 of
    discount, 50 concurrent $1 requests. Each pre-check counts only its own
    HOLD, so only the requests the balance can actually cover are admitted,
    and every admitted request charges at most the credits (C) -- the same
    exposure as a request without the discount."""
    psql("insert into users (id) values (20)")
    psql(f"select discount_grant(20, 10, '{u()}', 'r', '{{}}')")
    credits, d, cost = 0.01, 0.99, 1.0

    def request(_):
        rid = u()
        held = float(psql(f"select discount_hold(20, {cost}, {d}, '{rid}', 900)"))
        capacity = min(credits + held, credits / (1 - d))
        if capacity + 1e-12 < cost:
            return None  # 402 at pre-check; nothing served
        applied = float(psql(f"select discount_apply(20, {cost}, {d}, '{rid}', '{{}}')"))
        return cost - applied  # what the credits must cover

    with ThreadPoolExecutor(max_workers=16) as pool:
        charges = [c for c in pool.map(request, range(50)) if c is not None]
    assert len(charges) == 10  # 10 x $0.99 fits in $10; the 11th hold is $0.10
    assert all(c <= credits + 1e-9 for c in charges)
    assert balance(20) == "0.100000"
    # Only the refused 11th request's $0.10 hold is still live (until its TTL).
    assert psql("select count(*) from inference_discount_holds where user_id = 20") == "1"


def test_holds_are_idempotent_settled_expire_and_respected():
    psql("insert into users (id) values (21)")
    psql(f"select discount_grant(21, 5, '{u()}', 'r', '{{}}')")
    a, b = u(), u()
    assert psql(f"select discount_hold(21, 3, 0.99, '{a}', 900)") == "2.970000"
    assert psql(f"select discount_hold(21, 3, 0.99, '{a}', 900)") == "2.970000"
    assert psql(f"select discount_hold(21, 3, 0.99, '{b}', 900)") == "2.030000"
    # Neither a request without a hold nor an admin correction can take held discount.
    assert psql(f"select discount_apply(21, 3, 0.99, '{u()}', '{{}}')") == "0"
    assert '"insufficient_balance"' in psql(f"select discount_adjust(21, -1, '{u()}', 'x', 1)")
    # Settling A uses its hold and releases it.
    assert psql(f"select discount_apply(21, 1, 0.99, '{a}', '{{}}')") == "0.990000"
    assert psql(f"select count(*) from inference_discount_holds where request_id = '{a}'") == "0"
    assert psql(f"select discount_hold(21, 3, 0.99, '{a}', 900)") == "0"  # billed: no new hold
    # B's hold expires -> it stops counting.
    psql(
        "update inference_discount_holds set expires_at = now() - interval '1 second'"
        f" where request_id = '{b}'"
    )
    assert psql(f"select discount_apply(21, 5, 0.99, '{u()}', '{{}}')") == "4.010000"
    assert psql("select count(*) from inference_discount_holds where user_id = 21") == "0"


def test_concurrent_applies_never_overdraw():
    psql("insert into users (id) values (10)")
    psql(f"select discount_grant(10, 5, '{u()}', 'r', '{{}}')")
    with ThreadPoolExecutor(max_workers=8) as pool:
        applied = list(
            pool.map(
                lambda _: float(psql(f"select discount_apply(10, 1, 0.99, '{u()}', '{{}}')")),
                range(12),
            )
        )
    assert abs(sum(applied) - 5.0) < 1e-9
    assert balance(10) == "0.000000"


def test_concurrent_duplicates_apply_once():
    psql("insert into users (id) values (11)")
    psql(f"select discount_grant(11, 50, '{u()}', 'r', '{{}}')")
    rid = u()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: psql(f"select discount_apply(11, 1, 0.99, '{rid}', '{{}}')"), range(6)
            )
        )
    assert set(results) == {"0.990000"}
    assert balance(11) == "49.010000"


def test_adjust_never_below_zero_and_needs_reason():
    psql("insert into users (id) values (12)")
    assert '"insufficient_balance"' in psql(f"select discount_adjust(12, -1, '{u()}', 'x', 1)")
    assert '"invalid"' in psql(f"select discount_adjust(12, 1, '{u()}', ' ', 1)")
    assert '"user_not_found"' in psql(f"select discount_adjust(999, 1, '{u()}', 'x', 1)")
    rid = u()
    assert '"adjusted"' in psql(f"select discount_adjust(12, 2, '{rid}', 'x', 1)")
    assert '"exists"' in psql(f"select discount_adjust(12, 2, '{rid}', 'x', 1)")
    assert balance(12) == "2.000000"


def test_attribution_ceiling_pause_link_and_grant():
    psql(
        "insert into delegation_revenue (revenue_date, asset, period_key, revenue_native,"
        " revenue_usd, source, stake_total_native)"
        " values ('2026-10-10', 'eth', '2026-10-10', 0.01, 30, 't', 100)"
    )

    def reserve(wallet, user_id, attributed, cap=100):
        return psql(
            f"select delegation_reserve_attribution('{wallet}', 'eth', '2026-10-10', '2026-10-10',"
            f" {user_id}, 1, 100, {attributed}, 0.95, '{u()}', {cap}, 1000) ->> 'status'"
        )

    assert reserve("0xaaa", 1, 18) == "created"
    assert reserve("0xbbb", 2, 15) == "created"  # capped to the 12 left of 30
    assert reserve("0xccc", 3, 5) == "revenue_exhausted"
    assert (
        psql(
            "select sum(attributed_usd) from delegation_reward_attributions where period_key = '2026-10-10'"
        )
        == "30.000000000000000000"
    )
    a_id = psql("select id from delegation_reward_attributions where wallet_address = '0xaaa'")
    b_id = psql("select id from delegation_reward_attributions where wallet_address = '0xbbb'")
    # Wrong account, then paused (fail closed), then granted exactly once.
    assert psql(f"select delegation_grant_attribution({a_id}, 2, 100) ->> 'status'") == "not_linked"
    psql("update delegation_controls set accruals_paused = true where asset = 'eth'")
    assert psql(f"select delegation_grant_attribution({a_id}, 1, 100) ->> 'status'") == "paused"
    psql("delete from delegation_controls where asset = 'eth'")
    assert psql(f"select delegation_grant_attribution({a_id}, 1, 100) ->> 'status'") == "paused"
    psql("insert into delegation_controls (asset, accruals_paused) values ('eth', false)")
    before = float(balance(1) or 0)
    assert psql(f"select delegation_grant_attribution({a_id}, 1, 100) ->> 'status'") == "granted"
    assert psql(f"select delegation_grant_attribution({a_id}, 1, 100) ->> 'status'") == "already"
    assert abs(float(balance(1)) - before - 17.1) < 1e-9
    # Deactivated account: never granted.
    psql("update user_wallets set user_id = 3 where wallet_address = '0xbbb'")
    assert psql(f"select delegation_grant_attribution({b_id}, 3, 100) ->> 'status'") == (
        "inactive_user"
    )


V3_FUNCTIONS = (
    "discount_grant",
    "discount_apply",
    "discount_hold",
    "discount_reverse",
    "discount_adjust",
    "delegation_attribution_spent",
    "delegation_reserve_attribution",
    "delegation_grant_attribution",
)
V3_TABLES = (
    "delegation_reward_attributions",
    "inference_discount_balances",
    "inference_discount_ledger",
    "inference_discount_holds",
)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_nothing_in_v3_is_reachable_by_client_roles_even_with_supabase_defaults(role):
    names = ",".join(f"'{n}'" for n in V3_FUNCTIONS)
    leaked = psql(
        "select string_agg(p.proname, ',') from pg_proc p join pg_namespace n"
        " on n.oid = p.pronamespace where n.nspname = 'public'"
        f" and p.proname in ({names}) and has_function_privilege('{role}', p.oid, 'execute')"
    )
    assert leaked == ""
    for table in V3_TABLES:
        for priv in ("select", "insert", "update", "delete"):
            assert psql(f"select has_table_privilege('{role}', 'public.{table}', '{priv}')") == "f"
    for seq in ("delegation_reward_attributions_id_seq", "inference_discount_ledger_id_seq"):
        assert psql(f"select has_sequence_privilege('{role}', 'public.{seq}', 'usage')") == "f"
    # PUBLIC too: no v3 function carries the default PUBLIC execute grant.
    assert (
        psql(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace,"
            " aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a"
            f" where n.nspname = 'public' and p.proname in ({names}) and a.grantee = 0"
        )
        == "0"
    )


def test_a_key_held_by_another_account_is_neither_released_nor_drawn():
    psql("insert into users (id) values (30), (31)")
    psql(f"select discount_grant(30, 10, '{u()}', 'r', '{{}}')")
    psql(f"select discount_grant(31, 10, '{u()}', 'r', '{{}}')")
    rid = u()
    assert psql(f"select discount_hold(30, 5, 0.99, '{rid}', 900)") == "4.950000"
    assert psql(f"select discount_apply(31, 5, 0.99, '{rid}', '{{}}')") == "0"
    assert psql(f"select count(*) from inference_discount_holds where request_id = '{rid}'") == "1"
    assert balance(31) == "10.000000"


def test_nothing_positive_without_a_ledger_row():
    """No path returns a positive discount without writing its 'use' row."""
    psql("insert into users (id) values (32)")
    psql(f"select discount_grant(32, 10, '{u()}', 'r', '{{}}')")
    assert psql("select discount_apply(32, 2, 0.99, null, '{}')") == "0"
    assert psql(f"select discount_apply(32, null, 0.99, '{u()}', '{{}}')") == "0"
    assert psql(f"select discount_apply(null, 2, 0.99, '{u()}', '{{}}')") == "0"
    assert psql(f"select discount_apply(32, -2, 0.99, '{u()}', '{{}}')") == "0"
    rid = u()
    assert psql(f"select discount_apply(32, 2, 0.99, '{rid}', '{{}}')") == "1.980000"
    assert psql(f"select discount_apply(32, 2.5, 0.99, '{rid}', '{{}}')") == "0"
    assert (
        psql("select count(*) from inference_discount_ledger where user_id = 32 and kind = 'use'")
        == "1"
    )
    assert balance(32) == "8.020000"
    # The ledger always explains the balance.
    assert (
        psql(
            "select (select coalesce(sum(amount_usd), 0) from inference_discount_ledger"
            " where user_id = 32) = (select balance_usd from inference_discount_balances"
            " where user_id = 32)"
        )
        == "t"
    )


def test_a_period_is_never_granted_twice_or_beyond_its_revenue():
    psql("insert into users (id) values (33)")
    psql("insert into user_wallets (user_id, wallet_address, source) values (33, '0xddd', 'siwe')")
    psql(
        "insert into delegation_revenue (revenue_date, asset, period_key, revenue_native,"
        " revenue_usd, source, stake_total_native)"
        " values ('2026-10-20', 'eth', '2026-10-20', 0.01, 10, 't', 10)"
    )

    def reserve(wallet, attributed, rid):
        return psql(
            f"select delegation_reserve_attribution('{wallet}', 'eth', '2026-10-20', '2026-10-20',"
            f" 33, 1, 10, {attributed}, 0.95, '{rid}', 1000, 100000) ->> 'status'"
        )

    first = u()
    assert reserve("0xddd", 10, first) == "created"
    # Same wallet+period under another key, case variant included: one row.
    assert reserve("0xDDD", 10, u()) == "exists"
    # Another wallet cannot take more than the period earned.
    assert reserve("0xeee", 1, u()) == "revenue_exhausted"
    row = psql("select id from delegation_reward_attributions where request_id = '" + first + "'")
    assert psql(f"select delegation_grant_attribution({row}, 33, 1000) ->> 'status'") == "granted"
    assert psql(f"select delegation_grant_attribution({row}, 33, 1000) ->> 'status'") == "already"
    assert balance(33) == "9.500000"  # 0.95 x 10, once
    # A second revenue row for the same period is rejected by UNIQUE (asset, period_key).
    with pytest.raises(subprocess.CalledProcessError):
        psql(
            "insert into delegation_revenue (revenue_date, asset, period_key, revenue_native,"
            " revenue_usd, source) values ('2026-10-21', 'eth', '2026-10-20', 1, 999, 't')"
        )


def test_not_executable_by_anon():
    assert (
        psql(
            "select has_function_privilege('anon',"
            " 'public.discount_apply(bigint, numeric, numeric, uuid, jsonb)', 'execute')"
        )
        == "f"
    )
    assert (
        psql(
            "select has_table_privilege('authenticated', 'public.inference_discount_ledger', 'select')"
        )
        == "f"
    )
