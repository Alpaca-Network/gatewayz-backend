-- Migration: log sliding-scale payout tiers by trailing-7d verified volume
-- (gatewayz-backend Milestone 4 follow-up to #2266; m4/spec.md §5).
-- Created: 2026-09-09
-- Description:
--   Adds provider_payout_tiers, a small, hand-tunable table mapping a
--   provider's trailing-7-day VERIFIED token volume to a payout
--   multiplier (in basis points, so all downstream math stays integer --
--   see src/services/gpu/earnings.py's compute_amount_wei). Product intent
--   (radarmine1@gmail.com, 2026-09-09): reward large, sustained community
--   providers heavily and pay small/one-off "bot" nodes almost nothing,
--   without giving a sybil any incentive to split volume across many
--   small registrations -- see the per-PROVIDER (not per-node) volume
--   query in src/db/gpu_payouts.py's get_provider_verified_volume_7d.
--
--   Also adds two nullable audit columns to provider_earnings so every
--   accrual records the multiplier and the volume snapshot that produced
--   it (multiplier_bps, volume_7d_at_accrual) -- existing rows are
--   unaffected (both default NULL).
--
--   Seeded values below are TESTNET PLACEHOLDERS -- tunable by editing
--   this table directly (no deploy needed); provider_payout_tiers has no
--   code-level cache invalidation trigger, but src/db/gpu_payouts.py's
--   get_payout_tiers() only caches for ~60s.
--
--   RLS is enabled, service-role only (no policy), matching every other
--   table in 20260903200000_gpu_marketplace.sql.

CREATE TABLE IF NOT EXISTS public.provider_payout_tiers (
    min_tokens_7d       bigint PRIMARY KEY,
    multiplier_bps      int NOT NULL CHECK (multiplier_bps >= 0),
    label               text,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.provider_payout_tiers ENABLE ROW LEVEL SECURITY;

-- Testnet placeholder tiers -- a log sliding scale over trailing-7d
-- VERIFIED prompt+completion tokens, scoped per provider (payout wallet):
--   0           tokens/7d -> 0.05x ("bot" / negligible one-off activity)
--   100,000     tokens/7d -> 0.25x
--   1,000,000   tokens/7d -> 0.60x
--   10,000,000  tokens/7d -> 1.00x (baseline full rate)
--   100,000,000 tokens/7d -> 1.50x (large, sustained provider bonus)
INSERT INTO public.provider_payout_tiers (min_tokens_7d, multiplier_bps, label)
VALUES
    (0,           500,   'bot'),
    (100000,      2500,  'small'),
    (1000000,     6000,  'medium'),
    (10000000,    10000, 'large'),
    (100000000,   15000, 'whale')
ON CONFLICT (min_tokens_7d) DO NOTHING;


-- Audit columns on provider_earnings: what multiplier applied, and the
-- 7d-volume snapshot (including the work item just accrued) that decided
-- it. Nullable so pre-existing rows (accrued before this migration) are
-- left alone rather than backfilled with a guessed value.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'provider_earnings'
          AND column_name = 'multiplier_bps'
    ) THEN
        ALTER TABLE public.provider_earnings ADD COLUMN multiplier_bps int;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'provider_earnings'
          AND column_name = 'volume_7d_at_accrual'
    ) THEN
        ALTER TABLE public.provider_earnings ADD COLUMN volume_7d_at_accrual bigint;
    END IF;
END $$;


-- Index-friendly support for get_provider_verified_volume_7d's per-provider,
-- verified-only, trailing-7d sum -- the existing idx_provider_work_node_created
-- is keyed by node_id, not provider_id, and idx_provider_work_verification
-- alone doesn't cover the created_at range scan.
CREATE INDEX IF NOT EXISTS idx_provider_work_provider_verification_created
    ON public.provider_work (provider_id, verification, created_at);
