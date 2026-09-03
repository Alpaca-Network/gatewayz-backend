-- Add 'settling' as an intermediate provider_earnings status between
-- 'accrued' and 'settled' (gatewayz-backend#2266; PR #2288 review fix
-- round 1, I4).
--
-- The settlement job now atomically flips a provider's accrued earnings
-- to 'settling' (tagged with the settlement row's id) via a single
-- UPDATE ... WHERE status='accrued' BEFORE transferring, so a concurrent
-- spot-check failure's void (which only ever matches status='accrued')
-- can no longer race a transfer that has already summed and sent those
-- same earnings. See src/services/gpu/settlement.py and
-- docs/gpu/VERIFICATION_AND_PAYOUTS.md.
--
-- This is a small follow-up on 20260903200000_gpu_marketplace.sql (W-A1)
-- rather than an edit to it, because W-A1 hadn't merged when this was
-- written (parallel workstreams -- see m4/_standing.md). The constraint
-- name below matches Postgres's default `<table>_<column>_check` naming
-- for that migration's unnamed CHECK on provider_earnings.status.
--
-- Idempotent: safe to reapply (drops the constraint only if present,
-- then always (re)adds the 4-state version).

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'provider_earnings_status_check'
    ) THEN
        ALTER TABLE public.provider_earnings
            DROP CONSTRAINT provider_earnings_status_check;
    END IF;

    ALTER TABLE public.provider_earnings
        ADD CONSTRAINT provider_earnings_status_check
        CHECK (status IN ('accrued', 'settling', 'settled', 'void'));
END $$;
