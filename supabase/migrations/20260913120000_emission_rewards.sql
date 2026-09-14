-- Migration: Chutes-style WAYZ emission rewards (gatewayz-backend
-- tokenomics -- boss asks: split WAYZ rewards between stakers and GPU
-- providers the way Chutes/Bittensor does: 41% providers (ranked by a
-- rolling 7-day score), 41% stakers, 18% treasury). See
-- docs/tokenomics/EMISSION.md for the full design and worked example.
-- Created: 2026-09-13
--
-- Two new tables, both service-role-only (RLS enabled, no anon/
-- authenticated policy, table AND owned sequence explicitly revoked --
-- same posture as staking_reward_rates/staking_reward_accruals in
-- 20260911120000_staking_rewards.sql, see
-- tests/security/test_rls_policies_static.py):
--
--   1. emission_epochs -- one row per calendar day the emission_epoch job
--      ran, recording the split (providers/stakers/treasury, in wei) and a
--      summary. epoch_date is the primary key, which is what makes the
--      daily job idempotent (src/services/emission/epoch.py).
--   2. provider_scores -- one row per (epoch_date, provider_id): the raw
--      metrics, the adjusted score, and the resulting share/allocation.
--      Feeds GET /gpu/providers/me/earnings' `emission` block and
--      GET /admin/emission/epochs/{date}.
--
-- provider_earnings also gains a `source` column ('per_unit' | 'emission')
-- and a nullable `epoch_date` -- an emission allocation is a single daily
-- payout to a provider, not tied to one provider_work row, so work_id must
-- become nullable for these rows (the FK and its UNIQUE(work_id)
-- constraint both already tolerate NULL: a FK with a NULL value trivially
-- satisfies referential integrity, and Postgres's UNIQUE constraint does
-- not treat multiple NULLs as duplicates). settlement.py's read/write
-- paths (list_accrued_earnings, mark_earnings_settling/settled/accrued)
-- never filter or select on work_id, so no code changes were needed there
-- for settlement to pay emission rows exactly like per-unit ones -- see
-- tests/services/gpu/test_settlement.py's null-work_id coverage.

create table if not exists public.emission_epochs (
  epoch_date date primary key,
  emission_wei numeric(78,0) not null,
  providers_wei numeric(78,0) not null,
  stakers_wei numeric(78,0) not null,
  treasury_wei numeric(78,0) not null,
  providers_scored int not null default 0,
  stakers_paid int not null default 0,
  status text not null check (status in ('computed', 'allocated', 'failed')),
  summary jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create table if not exists public.provider_scores (
  id bigserial primary key,
  epoch_date date not null,
  provider_id bigint not null references public.gpu_providers(id),
  compute numeric(18,8) not null,
  speed numeric(18,8) not null,
  availability numeric(18,8) not null,
  unique_models numeric(18,8) not null,
  raw_score numeric(18,8) not null,
  adjusted_score numeric(18,8) not null,
  share numeric(18,10) not null,
  allocation_wei numeric(78,0) not null,
  tier_multiplier_bps int not null default 10000,
  details jsonb not null default '{}',
  created_at timestamptz not null default now(),
  unique (epoch_date, provider_id)
);

create index if not exists idx_provider_scores_provider on public.provider_scores (provider_id, epoch_date desc);

comment on table public.emission_epochs is
  'One row per day the emission_epoch job ran -- the daily WAYZ split (providers/stakers/treasury). Written by src/services/emission/epoch.py.';
comment on table public.provider_scores is
  'One row per (epoch_date, provider_id) -- the 7-day rolling score and resulting emission share/allocation. Written by src/services/emission/epoch.py.';

-- provider_earnings: source + epoch_date for emission allocations, and
-- work_id becomes nullable (see migration header). The status CHECK
-- already includes 'settling' from 20260903200001_provider_earnings_settling.sql
-- and is left untouched here.
alter table public.provider_earnings add column if not exists source text not null default 'per_unit' check (source in ('per_unit', 'emission'));
alter table public.provider_earnings add column if not exists epoch_date date null;
alter table public.provider_earnings alter column work_id drop not null;

-- One emission allocation per (provider, epoch) -- partial unique index so
-- it only constrains 'emission' rows and never interferes with per_unit
-- rows' existing UNIQUE(work_id).
create unique index if not exists idx_provider_earnings_emission on public.provider_earnings (provider_id, epoch_date) where source = 'emission';

-- staking_reward_accruals: source + the WAYZ amount an emission-mode
-- accrual was converted from (STAKER_REWARD_ASSET=credits path -- see
-- docs/tokenomics/EMISSION.md). rate_table rows leave both untouched.
-- rate_id becomes nullable: an emission-mode accrual isn't priced off
-- staking_reward_rates at all (it's a pro-rata share of the day's
-- stakers_wei), so it has no rate row to reference. A CHECK constraint
-- keeps the invariant for 'rate_table' rows (today's per_unit-analogous
-- path): those must still carry a rate_id.
alter table public.staking_reward_accruals add column if not exists source text not null default 'rate_table' check (source in ('rate_table', 'emission'));
alter table public.staking_reward_accruals add column if not exists wayz_amount_wei numeric(78,0) null;
alter table public.staking_reward_accruals alter column rate_id drop not null;
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'staking_reward_accruals_rate_id_required_for_rate_table'
    ) then
        alter table public.staking_reward_accruals
            add constraint staking_reward_accruals_rate_id_required_for_rate_table
            check (source <> 'rate_table' or rate_id is not null);
    end if;
end $$;

alter table public.emission_epochs enable row level security;
alter table public.provider_scores enable row level security;

revoke all on public.emission_epochs from anon, authenticated;
revoke all on public.provider_scores from anon, authenticated;
revoke all on sequence public.provider_scores_id_seq from anon, authenticated;

grant all on public.emission_epochs to service_role;
grant all on public.provider_scores to service_role;
grant all on sequence public.provider_scores_id_seq to service_role;
