-- Migration: WAYZ testnet faucet claims table (gatewayz-backend#2245)
-- Created: 2026-09-01
-- Description:
--   One row per successful/attempted faucet claim. UNIQUE on both
--   user_id and wallet_address enforces "one claim per account AND per
--   wallet" -- the actual anti-sybil mechanism (rate limiting on the
--   endpoints is a coarse abuse guard on top of this, not the primary
--   defense). No RLS policy -- service-role only, nothing reads this
--   from a per-user request path.

CREATE TABLE IF NOT EXISTS public.faucet_claims (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint NOT NULL UNIQUE,
    wallet_address  text NOT NULL UNIQUE,
    amount          numeric(78, 0) NOT NULL,
    status          text NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    tx_hash         text,
    error           text,
    claimed_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.faucet_claims ENABLE ROW LEVEL SECURITY;
