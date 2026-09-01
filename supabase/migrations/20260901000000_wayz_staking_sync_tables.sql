-- Migration: WAYZ staking on-chain indexer tables (gatewayz-backend#2244)
-- Created: 2026-09-01
-- Description:
--   Backing store for the poll-based WAYZStaking sync job
--   (src/services/chain/wayz_staking_sync.py). wallet_stakes holds the
--   latest known on-chain stake + computed daily allowance per wallet;
--   chain_sync_cursors tracks the last-synced block per contract so the
--   event-log scan can resume incrementally. Neither table is read by any
--   request-handling code yet (see spec's Non-goals) -- backend-only,
--   service_role access.

CREATE TABLE IF NOT EXISTS public.wallet_stakes (
    wallet_address     text PRIMARY KEY,
    staked_amount      numeric(78, 0) NOT NULL DEFAULT 0,
    daily_allowance    numeric(78, 0) NOT NULL DEFAULT 0,
    last_synced_block  bigint,
    last_synced_at     timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.chain_sync_cursors (
    contract_address   text PRIMARY KEY,
    last_synced_block  bigint NOT NULL,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Backend-only data, no per-user request path reads these yet: RLS on, no
-- permissive policy -> service_role only (mirrors credit_ledger's pattern).
ALTER TABLE public.wallet_stakes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chain_sync_cursors ENABLE ROW LEVEL SECURITY;
