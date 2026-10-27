-- Migration: usage_anchors -- daily Merkle-root anchoring on Avalanche C-Chain
--
-- One row per (chain, contract, UTC day). The daily job
-- (src/services/chain/usage_anchor.py) builds an RFC 6962 SHA-256 Merkle
-- tree over that day's api_usage rows in credit_transactions and writes only
-- the root to the UsageAnchor contract. See docs/usage-anchor/README.md.
--
-- Lifecycle: unanchored -> pending (tx hash recorded BEFORE broadcast) ->
-- confirmed; pending -> reorged (receipt vanished) -> pending (one resubmit);
-- failed = needs a human (root mismatch, resubmit exhausted, reverted).
-- skipped_empty = the day had no usage rows; nothing is sent.
--
-- Keyed by chain + contract so a Fuji run and the mainnet run never collide.
-- Additive only; service-role only -- RLS on, no anon/authenticated policy.
-- The public read API goes through the backend, never PostgREST.

create table if not exists public.usage_anchors (
  chain_id bigint not null,
  contract_address text not null,
  day date not null,
  day_index bigint not null,              -- UTC days since 1970-01-01 (the on-chain key)
  root text,                              -- 0x-prefixed 32-byte hex
  leaf_count bigint,
  status text not null default 'unanchored'
    check (status in ('unanchored', 'pending', 'confirmed', 'reorged', 'failed', 'skipped_empty')),
  tx_hash text,
  tx_nonce bigint,
  block_number bigint,
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (chain_id, contract_address, day)
);

create index if not exists idx_usage_anchors_status
  on public.usage_anchors (status) where status in ('pending', 'reorged');
create index if not exists idx_usage_anchors_day
  on public.usage_anchors (day desc);

comment on table public.usage_anchors is
  'Daily Merkle roots of api_usage ledger rows anchored on Avalanche C-Chain (UsageAnchor contract). Only the root is public; leaves stay in the database.';

alter table public.usage_anchors enable row level security;
revoke all on public.usage_anchors from anon, authenticated;
grant all on public.usage_anchors to service_role;
