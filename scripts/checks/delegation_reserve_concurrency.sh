#!/usr/bin/env bash
# Concurrency check for the delegated-staking cap/budget SQL
# (supabase/migrations/20261008230000_delegated_staking.sql, section 6).
#
# Spins up a throwaway local Postgres, applies user_wallets + the delegation
# migration (twice, to prove idempotency), then fires 40 parallel sessions at
# delegation_reserve_accrual and delegation_claim_accrual and asserts the
# per-account cap (5) and global budget (50) held. Needs initdb/pg_ctl/psql on
# PATH. Never touches a real database.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="$(mktemp -d)"
PORT="${PORT:-55439}"
trap 'pg_ctl -D "$DIR/data" stop -m fast >/dev/null 2>&1 || true; rm -rf "$DIR"' EXIT

initdb -D "$DIR/data" -U postgres -A trust >/dev/null
pg_ctl -D "$DIR/data" -o "-p $PORT -k '' -c listen_addresses=127.0.0.1" -l "$DIR/log" start >/dev/null
until pg_isready -h 127.0.0.1 -p "$PORT" >/dev/null; do sleep 0.3; done
run() { psql -h 127.0.0.1 -p "$PORT" -U postgres -v ON_ERROR_STOP=1 -q -At "$@"; }

run -c "create role anon; create role authenticated; create role service_role;
        create table public.users(id bigserial primary key);"
run -f "$ROOT/supabase/migrations/20260903000000_user_wallets.sql"
run -f "$ROOT/supabase/migrations/20261008230000_delegated_staking.sql" >/dev/null
run -f "$ROOT/supabase/migrations/20261008230000_delegated_staking.sql" >/dev/null 2>&1
run -c "insert into users default values;
        insert into user_wallets(user_id, wallet_address, source)
        select 1, 'w' || g, 'siwe' from generate_series(1, 40) g;"

# 1. 40 wallets of ONE account reserve concurrently: cap 5 must hold.
for g in $(seq 1 40); do
  run -c "select public.delegation_reserve_accrual('w$g','eth','2026-10-07',1000,1,1,1,5,50);" >/dev/null &
done
wait
spent=$(run -c "select sum(credits) from delegation_accruals where reward_date='2026-10-07';")
echo "account cap: reserved $spent (cap 5)"
[ "$(run -c "select ($spent)::numeric <= 5")" = "t" ]

# 2. 40 unlinked wallets (own cap each) reserve concurrently: budget 50 must hold.
for g in $(seq 1 40); do
  run -c "select public.delegation_reserve_accrual('u$g','ada','2026-10-08',1000,3,1,null,5,50);" >/dev/null &
done
wait
committed=$(run -c "select sum(credits) from delegation_accruals where reward_date='2026-10-08';")
echo "global budget: reserved $committed (budget 50)"
[ "$(run -c "select ($committed)::numeric <= 50")" = "t" ]

# 3. Link them all to account 1 and claim concurrently: cap 5 must hold at pay time.
run -c "insert into user_wallets(user_id, wallet_address, source)
        select 1, 'u' || g, 'siwe' from generate_series(1, 40) g;"
for id in $(run -c "select id from delegation_accruals where reward_date='2026-10-08';"); do
  run -c "select public.delegation_claim_accrual($id, 1, 5);" >/dev/null &
done
wait
claimed=$(run -c "select coalesce(sum(credits),0) from delegation_accruals
                  where reward_date='2026-10-08' and status='claimed';")
echo "pay-time cap: claimed $claimed (cap 5)"
[ "$(run -c "select ($claimed)::numeric <= 5")" = "t" ]
echo "OK"
