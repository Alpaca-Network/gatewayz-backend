-- Migration: user_wallets -- wallet-to-account linkage (Milestone 2,
-- gatewayz-backend#2249 #2250 #2251 #2252)
-- Created: 2026-09-03
-- Description:
--   One row per wallet linked to a Gatewayz account. A user may have
--   several wallets (Privy embedded + external); a wallet belongs to
--   exactly one user (wallet_address UNIQUE). is_primary marks the
--   wallet a wallet-first account was created/signed-in with; the
--   partial unique index enforces at most one primary per user.
--   No RLS policy -- service-role only, same as wallet_stakes/faucet_claims.
--   See docs/superpowers/specs/2026-09-03-wallet-identity-auth-design.md
--   section 3.

CREATE TABLE IF NOT EXISTS public.user_wallets (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    wallet_address      text NOT NULL UNIQUE,
    chain_namespace     text NOT NULL DEFAULT 'eip155',
    source              text NOT NULL CHECK (source IN ('privy', 'siwe')),
    wallet_client_type  text,
    is_primary          boolean NOT NULL DEFAULT false,
    verified_at         timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, wallet_address)
);

CREATE INDEX IF NOT EXISTS idx_user_wallets_user_id ON public.user_wallets (user_id);

-- At most one primary wallet per user.
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_wallets_primary_per_user
    ON public.user_wallets (user_id)
    WHERE is_primary;

ALTER TABLE public.user_wallets ENABLE ROW LEVEL SECURITY;
