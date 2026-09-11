-- Migration: staking_reward_rates, staking_reward_accruals
-- (gatewayz-backend staking rewards -- boss's rule: WAYZ stakers are paid
-- in inference credits; providers who offer inference are paid in WAYZ
-- (already built: M4 provider earnings + settlement). See
-- docs/staking/REWARDS.md and the design spec this was built from.)
-- Created: 2026-09-11
--
-- Two tables, both service-role-only (RLS enabled, no anon/authenticated
-- policy, table AND owned sequence explicitly revoked -- same posture as
-- audit_log/admin_invites in 20260911000001_audit_log_and_staff.sql, see
-- tests/security/test_rls_policies_static.py):
--
--   1. staking_reward_rates -- the tiered credits-per-1000-WAYZ-per-day
--      rate table, seeded with tiny placeholder values (real numbers are a
--      product decision the boss confirms before STAKING_REWARDS_ENABLED
--      flips on in prod). Superadmin-editable via PUT /admin/staking/reward-rates
--      (src/routes/admin_staking.py), which deactivates the old set and
--      inserts a new one rather than updating rows in place -- so a rate
--      already used by a paid accrual (rate_id FK below) is never mutated
--      out from under it.
--   2. staking_reward_accruals -- one row per (wallet_address, reward_date),
--      enforced by the unique index below, which is what makes the daily
--      job (src/services/staking_rewards.py::run_staking_rewards_once)
--      idempotent. user_id is null until the wallet is linked to a
--      Gatewayz account -- see src/db/user_wallets.py::link_wallet's
--      pay_pending_for_wallet hook.

create table if not exists public.staking_reward_rates (
  id serial primary key,
  min_stake_wayz numeric(38,0) not null,             -- tier floor in whole WAYZ
  credits_per_1k_wayz_per_day numeric(18,6) not null, -- USD credits
  active boolean not null default true,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.staking_reward_rates (min_stake_wayz, credits_per_1k_wayz_per_day, note) values
  (0,       0.010000, 'placeholder — set by ops'),
  (10000,   0.012000, 'placeholder'),
  (100000,  0.015000, 'placeholder')
on conflict do nothing;

create table if not exists public.staking_reward_accruals (
  id bigserial primary key,
  wallet_address text not null,
  user_id bigint null,                 -- null = pending (wallet not linked yet)
  reward_date date not null,           -- UTC day the stake was held
  staked_amount_wei numeric(78,0) not null,
  rate_id int not null references public.staking_reward_rates(id),
  credits numeric(18,6) not null,
  status text not null check (status in ('paid','pending','skipped')),
  credit_transaction_id bigint null,
  skip_reason text null,
  created_at timestamptz not null default now(),
  paid_at timestamptz null,
  unique (wallet_address, reward_date)
);

create index if not exists idx_sra_user_date on public.staking_reward_accruals(user_id, reward_date desc);
create index if not exists idx_sra_pending on public.staking_reward_accruals(wallet_address) where status = 'pending';

comment on table public.staking_reward_rates is
  'Tiered credits-per-1000-WAYZ-per-day rate table for staking rewards. Superadmin-editable via PUT /admin/staking/reward-rates.';
comment on table public.staking_reward_accruals is
  'One row per (wallet_address, reward_date) -- the unique index makes the daily staking-rewards job idempotent. Written by src/services/staking_rewards.py.';

alter table public.staking_reward_rates enable row level security;
alter table public.staking_reward_accruals enable row level security;

revoke all on public.staking_reward_rates from anon, authenticated;
revoke all on public.staking_reward_accruals from anon, authenticated;
revoke all on sequence public.staking_reward_rates_id_seq from anon, authenticated;
revoke all on sequence public.staking_reward_accruals_id_seq from anon, authenticated;

grant all on public.staking_reward_rates to service_role;
grant all on public.staking_reward_accruals to service_role;
grant all on sequence public.staking_reward_rates_id_seq to service_role;
grant all on sequence public.staking_reward_accruals_id_seq to service_role;
