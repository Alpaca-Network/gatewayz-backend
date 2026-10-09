-- Migration: delegated staking (inference-as-yield v2)
--   delegation_measurements, delegation_allowance_rates, delegation_accruals,
--   delegation_revenue, delegation_controls; user_wallets accepts Cardano
--   (CIP-30) links.
-- Created: 2026-10-08
--
-- Users stake ETH from their own wallet into a Gatewayz-run StakeWise V3
-- vault, or delegate ADA to a Gatewayz Cardano pool. The stake never leaves
-- the user's control; the staking rewards accrue to us through the vault fee
-- and the pool margin, and we grant inference credits at an admin-set rate
-- that can change at any time. See docs/delegation/README.md.
--
-- Same posture as 20260915000000_holdings_rewards.sql:
--   * service-role only -- RLS on, no anon/authenticated policy, table AND
--     owned sequence explicitly revoked.
--   * delegation_accruals is UNIQUE (wallet_address, asset, reward_date) and is
--     written 'pending' BEFORE any credit moves; the credit ledger's partial
--     unique index on credit_transactions.request_id is the second guard.
--   * Ships inert: every allowance rate is seeded at 0 and inactive.
--
-- Idempotent throughout (IF NOT EXISTS, guarded seeds, drop-then-add for the
-- one constraint it replaces), so a re-run is a no-op.
--
-- Version stamp: 20261008230000 is newer than every file in
-- supabase/migrations, supabase/staged-migrations and the open PRs' migrations
-- (latest on main: 20261008120000), and unused.

-- 0. user_wallets: Cardano stake addresses are linked with a CIP-30 signData
--    proof. source gains 'cip30'; chain_namespace (no CHECK today) carries
--    'cip34' -- the CAIP-2 namespace for Cardano -- next to the existing
--    'eip155'. Every existing row is eip155, so the wider CHECK accepts them.
--    The original inline CHECK is auto-named user_wallets_source_check by
--    Postgres; any CHECK over `source` is dropped by lookup rather than by that
--    assumed name, so a differently named one cannot survive and keep
--    rejecting 'cip30'.
do $$
declare
  c record;
begin
  for c in
    select con.conname
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    join pg_namespace nsp on nsp.oid = rel.relnamespace
    where nsp.nspname = 'public'
      and rel.relname = 'user_wallets'
      and con.contype = 'c'
      and pg_get_constraintdef(con.oid) ilike '%source%'
  loop
    execute format('alter table public.user_wallets drop constraint %I', c.conname);
  end loop;
end;
$$;
alter table public.user_wallets
  add constraint user_wallets_source_check check (source in ('privy', 'siwe', 'cip30'));

create index if not exists idx_user_wallets_chain_namespace
  on public.user_wallets (chain_namespace);

-- 1. One row per (wallet, asset, sweep) -- WRITTEN EVEN WHEN THE AMOUNT IS
--    ZERO. A wallet we measured and found nothing delegated is a zero row; a
--    wallet we could not measure writes nothing. The daily accrual pays on the
--    day's LOWEST usd_value and refuses a day with too few rows, so an absent
--    zero would let a wallet stake for two sweeps and be paid for the day.
create table if not exists public.delegation_measurements (
  id bigserial primary key,
  wallet_address text not null,
  asset text not null check (asset in ('eth', 'ada')),
  amount_raw numeric(78,0) not null,      -- wei (ETH vault assets) / lovelace (ADA)
  usd_value numeric(38,18) not null,
  taken_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (wallet_address, asset, taken_at)
);

create index if not exists idx_dm_wallet_asset_taken_at
  on public.delegation_measurements (wallet_address, asset, taken_at);
create index if not exists idx_dm_taken_at
  on public.delegation_measurements (taken_at);

-- 2. The per-asset allowance rate. At most one ACTIVE row per asset; a rate
--    change deactivates the old row and inserts a new one, so an accrual's
--    rate can always be traced. Seeded 0 and inactive: a dark feature that
--    accidentally runs pays nothing.
create table if not exists public.delegation_allowance_rates (
  id bigserial primary key,
  asset text not null check (asset in ('eth', 'ada')),
  credits_per_1k_usd_per_day numeric(18,6) not null check (credits_per_1k_usd_per_day >= 0),
  is_active boolean not null default false,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_delegation_allowance_rates_active_asset
  on public.delegation_allowance_rates (asset) where is_active;

insert into public.delegation_allowance_rates (asset, credits_per_1k_usd_per_day, is_active, note)
select a.asset, 0.000000, false, 'placeholder -- set by an admin before DELEGATED_STAKING_ENABLED'
from (values ('eth'), ('ada')) as a(asset)
where not exists (
  select 1 from public.delegation_allowance_rates r where r.asset = a.asset
);

-- 3. One accrual per (wallet, asset, UTC day). Written pending before any
--    credit moves; request_id 'delegation:{asset}:{wallet}:{date}'.
create table if not exists public.delegation_accruals (
  id bigserial primary key,
  wallet_address text not null,
  asset text not null check (asset in ('eth', 'ada')),
  reward_date date not null,
  usd_basis numeric(38,18) not null,      -- the day's MINIMUM measured USD value
  credits numeric(18,6) not null check (credits >= 0),
  rate_credits_per_1k_usd numeric(18,6) not null,
  user_id bigint null,                    -- the account at decision time, if linked
  status text not null default 'pending' check (status in ('pending', 'paid', 'void')),
  ledger_request_id text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (wallet_address, asset, reward_date)
);

create index if not exists idx_da_wallet_date
  on public.delegation_accruals (wallet_address, reward_date desc);
create index if not exists idx_da_reward_date
  on public.delegation_accruals (reward_date);
create index if not exists idx_da_pending
  on public.delegation_accruals (wallet_address) where status = 'pending';

-- 4. Revenue that actually reached us, for reconciliation. period_key makes a
--    period recorded once: 'YYYY-MM-DD' for the ETH fee-recipient share delta,
--    'epoch:N' for Cardano pool_fees. raw_amount is the ETH fee recipient's
--    share balance at that reading (the next day's delta is measured from it).
create table if not exists public.delegation_revenue (
  id bigserial primary key,
  revenue_date date not null,
  asset text not null check (asset in ('eth', 'ada')),
  period_key text not null,
  revenue_native numeric(38,18) not null,
  revenue_usd numeric(38,18) not null,
  raw_amount numeric(78,0) null,
  source text not null,
  created_at timestamptz not null default now(),
  unique (asset, period_key)
);

create index if not exists idx_dr_asset_date
  on public.delegation_revenue (asset, revenue_date desc);

-- 5. Fail-closed switch. Reconciliation pauses an asset's NEW accruals when
--    the cost of credits granted outruns revenue; only an admin resumes it.
create table if not exists public.delegation_controls (
  asset text primary key check (asset in ('eth', 'ada')),
  accruals_paused boolean not null default false,
  paused_reason text null,
  paused_at timestamptz null,
  resumed_at timestamptz null,
  resumed_by text null,
  updated_at timestamptz not null default now()
);

insert into public.delegation_controls (asset)
select a.asset from (values ('eth'), ('ada')) as a(asset)
where not exists (select 1 from public.delegation_controls c where c.asset = a.asset);

comment on table public.delegation_measurements is
  'One row per (wallet, asset, measurement sweep) of stake delegated to the Gatewayz StakeWise vault / Cardano pool, zero included. The daily accrual pays on the day''s MINIMUM.';
comment on table public.delegation_allowance_rates is
  'Admin-set credits per 1000 USD delegated per day, per asset. At most one active row per asset. Not a guaranteed return.';
comment on table public.delegation_accruals is
  'One row per (wallet_address, asset, reward_date); written pending before any credit is granted.';
comment on table public.delegation_revenue is
  'Staking revenue that reached Gatewayz (vault fee shares, pool operator fees), for reconciliation against credits granted.';
comment on table public.delegation_controls is
  'Per-asset fail-closed switch. Reconciliation pauses new accruals; an admin resumes.';

alter table public.delegation_measurements enable row level security;
alter table public.delegation_allowance_rates enable row level security;
alter table public.delegation_accruals enable row level security;
alter table public.delegation_revenue enable row level security;
alter table public.delegation_controls enable row level security;

revoke all on public.delegation_measurements from anon, authenticated;
revoke all on public.delegation_allowance_rates from anon, authenticated;
revoke all on public.delegation_accruals from anon, authenticated;
revoke all on public.delegation_revenue from anon, authenticated;
revoke all on public.delegation_controls from anon, authenticated;
revoke all on sequence public.delegation_measurements_id_seq from anon, authenticated;
revoke all on sequence public.delegation_allowance_rates_id_seq from anon, authenticated;
revoke all on sequence public.delegation_accruals_id_seq from anon, authenticated;
revoke all on sequence public.delegation_revenue_id_seq from anon, authenticated;

grant all on public.delegation_measurements to service_role;
grant all on public.delegation_allowance_rates to service_role;
grant all on public.delegation_accruals to service_role;
grant all on public.delegation_revenue to service_role;
grant all on public.delegation_controls to service_role;
grant all on sequence public.delegation_measurements_id_seq to service_role;
grant all on sequence public.delegation_allowance_rates_id_seq to service_role;
grant all on sequence public.delegation_accruals_id_seq to service_role;
grant all on sequence public.delegation_revenue_id_seq to service_role;
