-- Migration: Gatewayz One Phase 1 (correction) — canonical-registry columns on `models`
-- Created: 2026-06-17
-- Why this exists:
--   The Phase 1 registry migration (20260617000000) added the canonical-registry
--   columns to `public.models_catalog`. But the catalog table in THIS codebase is
--   `public.models` (the app targets `.table("models")` everywhere; `models_catalog`
--   is just the conceptual name — `models_catalog_db.py` operates on `models`).
--   `public.models_catalog` does not exist, so Part 2 of that migration was a no-op.
--   This forward-fix adds the same columns to the real `public.models` table.
--   (Forward-fix rather than editing the already-applied migration.)
-- Safety: additive, idempotent, guarded — skips cleanly if `public.models` is absent.
--   `models` already has some of these (e.g. modality); ADD COLUMN IF NOT EXISTS skips
--   them. `capabilities` is NOT NULL DEFAULT '{}' — metadata-only add on PG 11+ (no rewrite).

DO $$
BEGIN
    IF to_regclass('public.models') IS NULL THEN
        RAISE NOTICE 'public.models not found — skipping canonical registry columns';
        RETURN;
    END IF;

    ALTER TABLE public.models ADD COLUMN IF NOT EXISTS canonical_id text;
    ALTER TABLE public.models ADD COLUMN IF NOT EXISTS capabilities jsonb NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE public.models ADD COLUMN IF NOT EXISTS modality text;
    ALTER TABLE public.models ADD COLUMN IF NOT EXISTS context_length integer;
    ALTER TABLE public.models ADD COLUMN IF NOT EXISTS deprecated_at timestamptz;

    CREATE INDEX IF NOT EXISTS idx_models_canonical_id
        ON public.models (canonical_id)
        WHERE deprecated_at IS NULL;
END$$;
