-- Migration: holdings_tokens, wallet_holdings_snapshots,
--            holdings_reward_rates, holdings_reward_accruals
-- (gatewayz-backend holdings rewards -- pay inference credits for HOLDING
-- top-20 tokens in a wallet the user has proven they control. This is
-- NON-CUSTODIAL: we never take a deposit, we only read balances, so there
-- is no "staked amount" here -- the input is a USD valuation we observed.)
-- Created: 2026-09-15
--
-- This deliberately reuses the payout half of the (currently dark) WAYZ
-- staking rewards feature in 20260911120000_staking_rewards.sql and
-- replaces only the "how much do you have" input. Same posture, same
-- idempotency story:
--
--   * service-role only -- RLS on, no anon/authenticated policy, table AND
--     owned sequence explicitly revoked (see
--     tests/security/test_rls_policies_static.py).
--   * holdings_reward_accruals is UNIQUE (wallet_address, reward_date) and
--     is written 'pending' BEFORE any credit is granted, exactly like
--     staking_reward_accruals -- that index is what makes the daily job
--     re-runnable without double-paying. ledger_request_id records the
--     credit ledger's request_id (whose own partial unique index on
--     credit_transactions.request_id is the second, independent guard).
--
-- Version stamp: 20260915000000 is new and unused across
-- supabase/migrations, supabase/migrations_backup and
-- supabase/staged-migrations. A reused stamp kills `supabase db push` on
-- schema_migrations_pkey, and the Sync job used to swallow it -- see the
-- 2026-09-11 collision that silently prevented the staking tables from
-- being created (fixed by gatewayz-backend#2313).

-- 1. The token registry. Seeded by ops, NOT by this migration.
create table if not exists public.holdings_tokens (
  id bigserial primary key,
  chain_id int not null,
  contract_address text null,          -- null = the chain's native asset (ETH, etc.)
  symbol text not null,
  decimals int not null,
  price_id text not null,              -- the external price feed's id for this asset
  is_enabled boolean not null default true,
  created_at timestamptz not null default now()
);

-- One row per (chain, contract), with the native asset treated as a single
-- distinct row per chain. A plain UNIQUE (chain_id, contract_address)
-- would let two native rows coexist, because in SQL null <> null -- hence
-- COALESCE to a sentinel that can never collide with a real address.
create unique index if not exists idx_holdings_tokens_chain_contract
  on public.holdings_tokens (chain_id, coalesce(contract_address, 'native'));

create index if not exists idx_holdings_tokens_enabled
  on public.holdings_tokens (chain_id) where is_enabled;

-- 2. Observed balances. One row per (wallet, token, observation); every row
--    written by a single sweep shares one taken_at, which is what makes a
--    "batch" identifiable -- src/db/holdings.py::get_min_usd_for_date sums
--    per batch and then takes the day's MINIMUM, so a wallet is paid on
--    what it held at its thinnest point that day rather than on a
--    flash-funded peak.
create table if not exists public.wallet_holdings_snapshots (
  id bigserial primary key,
  wallet_address text not null,
  token_id bigint not null references public.holdings_tokens(id),
  raw_amount numeric(78,0) not null,   -- base units, pre-decimals (uint256-safe)
  usd_value numeric(38,18) not null,
  taken_at timestamptz not null default now()
);

create index if not exists idx_whs_wallet_taken_at
  on public.wallet_holdings_snapshots (wallet_address, taken_at);
create index if not exists idx_whs_taken_at
  on public.wallet_holdings_snapshots (taken_at);

-- 3. The tiered rate table. Same semantics as staking_reward_rates: the
--    active row with the largest min_usd <= the wallet's USD value wins,
--    so a min_usd = 0 tier must always exist or wallets below every floor
--    fall through and earn nothing. The trigger below enforces that the
--    zero tier can never be deleted or raised off zero.
create table if not exists public.holdings_reward_rates (
  id serial primary key,
  min_usd numeric(38,18) not null,             -- tier floor, USD
  credits_per_1k_usd_per_day numeric(18,6) not null,
  is_active boolean not null default true,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.holdings_reward_rates (min_usd, credits_per_1k_usd_per_day, note)
select 0, 0.000000, 'placeholder — set by ops before HOLDINGS_REWARDS_ENABLED'
where not exists (select 1 from public.holdings_reward_rates where min_usd = 0);

create or replace function public.holdings_reward_rates_require_zero_tier()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if not exists (select 1 from public.holdings_reward_rates where min_usd = 0) then
    raise exception 'holdings_reward_rates must always keep a min_usd = 0 tier';
  end if;
  return null;
end;
$$;

drop trigger if exists trg_holdings_reward_rates_require_zero_tier
  on public.holdings_reward_rates;
create constraint trigger trg_holdings_reward_rates_require_zero_tier
  after update or delete on public.holdings_reward_rates
  deferrable initially deferred
  for each row
  execute function public.holdings_reward_rates_require_zero_tier();

-- 4. One accrual per wallet per UTC day. Mirrors staking_reward_accruals.
create table if not exists public.holdings_reward_accruals (
  id bigserial primary key,
  wallet_address text not null,
  reward_date date not null,           -- UTC day the holdings were observed
  usd_basis numeric(38,18) not null,   -- the day's MINIMUM summed USD value
  credits numeric(18,6) not null,
  status text not null default 'pending' check (status in ('pending','paid','void')),
  ledger_request_id text null,         -- credit_transactions.request_id once paid
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (wallet_address, reward_date)
);

create index if not exists idx_hra_wallet_date
  on public.holdings_reward_accruals (wallet_address, reward_date desc);
create index if not exists idx_hra_pending
  on public.holdings_reward_accruals (wallet_address) where status = 'pending';

comment on table public.holdings_tokens is
  'Registry of top-20 tokens whose balances earn inference credits. contract_address null = the chain native asset. Filled by ops, not by the migration.';
comment on table public.wallet_holdings_snapshots is
  'Observed (non-custodial) wallet balances. Rows sharing one taken_at are one sweep; the daily job pays on the minimum batch total for the day.';
comment on table public.holdings_reward_rates is
  'Tiered credits-per-1000-USD-held-per-day rate table. Largest active min_usd <= the wallet value wins; a min_usd = 0 tier is mandatory.';
comment on table public.holdings_reward_accruals is
  'One row per (wallet_address, reward_date) -- the unique index makes the daily holdings-rewards job idempotent. Written pending before any credit is granted.';

alter table public.holdings_tokens enable row level security;
alter table public.wallet_holdings_snapshots enable row level security;
alter table public.holdings_reward_rates enable row level security;
alter table public.holdings_reward_accruals enable row level security;

revoke all on public.holdings_tokens from anon, authenticated;
revoke all on public.wallet_holdings_snapshots from anon, authenticated;
revoke all on public.holdings_reward_rates from anon, authenticated;
revoke all on public.holdings_reward_accruals from anon, authenticated;
revoke all on sequence public.holdings_tokens_id_seq from anon, authenticated;
revoke all on sequence public.wallet_holdings_snapshots_id_seq from anon, authenticated;
revoke all on sequence public.holdings_reward_rates_id_seq from anon, authenticated;
revoke all on sequence public.holdings_reward_accruals_id_seq from anon, authenticated;

grant all on public.holdings_tokens to service_role;
grant all on public.wallet_holdings_snapshots to service_role;
grant all on public.holdings_reward_rates to service_role;
grant all on public.holdings_reward_accruals to service_role;
grant all on sequence public.holdings_tokens_id_seq to service_role;
grant all on sequence public.wallet_holdings_snapshots_id_seq to service_role;
grant all on sequence public.holdings_reward_rates_id_seq to service_role;
grant all on sequence public.holdings_reward_accruals_id_seq to service_role;
