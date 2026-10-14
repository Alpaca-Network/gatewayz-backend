-- Migration: delegated staking eligibility (US / Canada block)
-- Created: 2026-10-14
--
-- Counsel cleared delegated staking everywhere except the US and Canada. One
-- row per account that has attested "I am not a resident or citizen of the
-- United States or Canada" (status 'eligible'), or that was seen in a
-- restricted country (status 'restricted', sticky: a later attestation never
-- clears it -- only a manual update does).
--
-- No row = not eligible. Rewards (v2 accruals and v3 discount attributions)
-- are granted only to an eligible account (src/services/delegation/
-- eligibility.py, checked in rewards._payee_verified); anything not granted
-- stays pending. Exits, reads and spending an existing discount balance never
-- look at this table.
--
-- Version stamp 20261014120000 is newer than every file in supabase/migrations
-- and unused.

create table if not exists public.delegation_eligibility (
  user_id bigint primary key,
  status text not null check (status in ('eligible', 'restricted')),
  attested_at timestamptz null,          -- when the account last attested
  attestation_version text null,         -- which wording it attested to
  country text null,                     -- ISO alpha-2 from the trusted header, if any
  restricted_reason text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Service role only, same posture as the other delegation tables.
alter table public.delegation_eligibility enable row level security;
revoke all on public.delegation_eligibility from anon, authenticated;
grant all on public.delegation_eligibility to service_role;
