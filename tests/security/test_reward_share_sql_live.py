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


def psql(sql: str) -> str:
    out = subprocess.run(
        ["psql", DSN, "-v", "ON_ERROR_STOP=1", "-qtAX", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def schema():
    psql("""
        drop schema if exists public cascade; create schema public;
        do $$ begin
          if not exists (select 1 from pg_roles where rolname='anon') then create role anon; end if;
          if not exists (select 1 from pg_roles where rolname='authenticated') then
            create role authenticated; end if;
          if not exists (select 1 from pg_roles where rolname='service_role') then
            create role service_role; end if;
        end $$;
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
            ["psql", DSN, "-v", "ON_ERROR_STOP=1", "-q", "-f", str(MIGRATIONS / name)],
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
