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
--   * The per-account cap and global budget are enforced atomically in SQL
--     (delegation_reserve_accrual / delegation_claim_accrual, section 6).
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
  -- pending -> claimed (reserved for paid_user_id under the account cap) -> paid.
  -- void = decided, never to be paid (did not fit the account cap at pay time).
  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'paid', 'void')),
  paid_user_id bigint null,               -- the account the credits went / are going to
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

-- 6. Atomic check-and-reserve. The per-account daily cap and the global
--    daily budget are enforced HERE, inside one transaction holding a per-date
--    advisory lock, never by read-then-write in Python: two accrual runs, or a
--    run and a pay-on-link, would otherwise both read "under cap" and both pay.
--    Every reservation and every claim for a reward date takes the same lock,
--    so they serialize; the lock is released at commit.
--
--    An account's spend for a date is every live accrual (pending, claimed,
--    paid) attributed to it: by the account at decision time (user_id), the
--    account paid (paid_user_id), or a wallet it has linked now. A wallet that
--    moved between accounts therefore counts against both -- conservative.

create or replace function public.delegation_account_spent(
  p_user_id bigint, p_reward_date date, p_statuses text[],
  p_exclude_id bigint default null
) returns numeric
language sql
stable
set search_path = public, pg_temp
as $$
  select coalesce(sum(a.credits), 0)
  from public.delegation_accruals a
  where a.reward_date = p_reward_date
    and a.status = any(p_statuses)
    and (p_exclude_id is null or a.id <> p_exclude_id)
    and (
      a.user_id = p_user_id
      or a.paid_user_id = p_user_id
      or a.wallet_address in (
        select w.wallet_address from public.user_wallets w where w.user_id = p_user_id
      )
    );
$$;

-- Reserve (insert pending) one wallet-asset-day accrual, capped by the
-- account's remaining headroom and refused when the global budget would be
-- exceeded or the asset is paused. Returns {status, accrual?, capped?} with
-- status in created | exists | over_cap | budget_exhausted | paused.
create or replace function public.delegation_reserve_accrual(
  p_wallet_address text, p_asset text, p_reward_date date,
  p_usd_basis numeric, p_credits numeric, p_rate numeric, p_user_id bigint,
  p_account_cap numeric, p_global_budget numeric
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_existing public.delegation_accruals;
  v_row public.delegation_accruals;
  v_committed numeric;
  v_spent numeric := 0;
  v_grant numeric;
begin
  perform pg_advisory_xact_lock(hashtext('delegation_accruals:' || p_reward_date::text));

  select * into v_existing from public.delegation_accruals
  where wallet_address = lower(p_wallet_address) and asset = p_asset
    and reward_date = p_reward_date;
  if found then
    return jsonb_build_object('status', 'exists', 'accrual', to_jsonb(v_existing));
  end if;

  if exists (select 1 from public.delegation_controls c
             where c.asset = p_asset and c.accruals_paused) then
    return jsonb_build_object('status', 'paused');
  end if;

  if p_user_id is not null then
    v_spent := public.delegation_account_spent(
      p_user_id, p_reward_date, array['pending', 'claimed', 'paid']);
  end if;
  v_grant := least(p_credits, p_account_cap - v_spent);
  if v_grant <= 0 then
    return jsonb_build_object('status', 'over_cap');
  end if;

  select coalesce(sum(credits), 0) into v_committed from public.delegation_accruals
  where reward_date = p_reward_date and status in ('pending', 'claimed', 'paid');
  if v_committed + v_grant > p_global_budget then
    return jsonb_build_object('status', 'budget_exhausted');
  end if;

  insert into public.delegation_accruals (
    wallet_address, asset, reward_date, usd_basis, credits,
    rate_credits_per_1k_usd, user_id, status
  ) values (
    lower(p_wallet_address), p_asset, p_reward_date, p_usd_basis, v_grant,
    p_rate, p_user_id, 'pending'
  ) returning * into v_row;

  return jsonb_build_object(
    'status', 'created', 'accrual', to_jsonb(v_row), 'capped', v_grant < p_credits);
end;
$$;

-- Claim a pending accrual for payment to p_user_id: re-checks the pause and
-- the account cap (counting claimed + paid rows only, i.e. money already
-- committed to this account) and flips pending -> claimed. A row that no
-- longer fits is voided. A row already claimed is returned as-is so a crashed
-- payment can be resumed (the ledger's UUID request_id keeps that idempotent).
-- Returns {status, accrual?} with status in claimed | paid | void | paused |
-- not_found.
create or replace function public.delegation_claim_accrual(
  p_accrual_id bigint, p_user_id bigint, p_account_cap numeric
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_row public.delegation_accruals;
  v_spent numeric;
begin
  select * into v_row from public.delegation_accruals where id = p_accrual_id;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  perform pg_advisory_xact_lock(hashtext('delegation_accruals:' || v_row.reward_date::text));
  -- Re-read under the lock: a concurrent claim may have moved it.
  select * into v_row from public.delegation_accruals where id = p_accrual_id for update;

  if v_row.status in ('paid', 'void', 'claimed') then
    return jsonb_build_object('status', v_row.status, 'accrual', to_jsonb(v_row));
  end if;

  if exists (select 1 from public.delegation_controls c
             where c.asset = v_row.asset and c.accruals_paused) then
    return jsonb_build_object('status', 'paused');
  end if;

  v_spent := public.delegation_account_spent(
    p_user_id, v_row.reward_date, array['claimed', 'paid'], v_row.id);
  if v_row.credits > p_account_cap - v_spent then
    update public.delegation_accruals set status = 'void', updated_at = now()
    where id = v_row.id returning * into v_row;
    return jsonb_build_object('status', 'void', 'accrual', to_jsonb(v_row));
  end if;

  update public.delegation_accruals
  set status = 'claimed', paid_user_id = p_user_id, updated_at = now()
  where id = v_row.id returning * into v_row;
  return jsonb_build_object('status', 'claimed', 'accrual', to_jsonb(v_row));
end;
$$;

revoke all on function public.delegation_account_spent(bigint, date, text[], bigint)
  from public, anon, authenticated;
revoke all on function public.delegation_reserve_accrual(
  text, text, date, numeric, numeric, numeric, bigint, numeric, numeric)
  from public, anon, authenticated;
revoke all on function public.delegation_claim_accrual(bigint, bigint, numeric)
  from public, anon, authenticated;
grant execute on function public.delegation_account_spent(bigint, date, text[], bigint)
  to service_role;
grant execute on function public.delegation_reserve_accrual(
  text, text, date, numeric, numeric, numeric, bigint, numeric, numeric) to service_role;
grant execute on function public.delegation_claim_accrual(bigint, bigint, numeric)
  to service_role;

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
