-- Migration: Model categorization — derived multi-label tags + tunable rules
-- Created: 2026-07-04
-- Spec:    docs/superpowers/specs/2026-07-04-model-categorization-design.md
--
-- What this adds:
--   1. public.models.categories text[]  — derived tags per model (cheapest,
--      fastest, largest, smartest, reasoning, vision, free, coding,
--      long-context, balanced, flagship|mid|budget). GIN-indexed so routing
--      can filter with `categories @> '{fastest}'`.
--   2. public.category_rules            — absolute-threshold config so ops can
--      tune cutoffs WITHOUT a code deploy (same rationale as the capability
--      columns migration 20260401000005). Seeded with the spec defaults.
--
-- Safety: additive, idempotent, guarded. `categories NOT NULL DEFAULT '{}'` is a
--   metadata-only add on PG 11+ (no table rewrite).

-- ============================================================================
-- Part 1: models.categories column + GIN index
-- ============================================================================
DO $$
BEGIN
    IF to_regclass('public.models') IS NULL THEN
        RAISE NOTICE 'public.models not found — skipping categories column';
        RETURN;
    END IF;

    ALTER TABLE public.models
        ADD COLUMN IF NOT EXISTS categories text[] NOT NULL DEFAULT '{}'::text[];

    CREATE INDEX IF NOT EXISTS idx_models_categories
        ON public.models USING GIN (categories);

    COMMENT ON COLUMN public.models.categories IS
        'Derived category tags (cheapest/fastest/largest/smartest/reasoning/vision/free/coding/long-context/balanced/flagship|mid|budget). Computed by src/services/model_categorizer.py on sync; do not hand-edit.';
END$$;

-- ============================================================================
-- Part 2: category_rules — tunable absolute thresholds
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.category_rules (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category     text NOT NULL,
    dimension    text NOT NULL,   -- blended_price | latency_tier | context_length | quality | quality_code | is_reasoning | modality | is_free | value_ratio | quality_band
    operator     text NOT NULL,   -- lte | gte | eq | contains | band
    threshold    numeric,         -- null for boolean/contains rules
    threshold2   numeric,         -- upper bound for 'band' operator
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- e.g. blended-price weights
    enabled      boolean NOT NULL DEFAULT true,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT category_rules_unique UNIQUE (category),
    CONSTRAINT category_rules_operator_valid
        CHECK (operator IN ('lte', 'gte', 'eq', 'contains', 'band'))
);

COMMENT ON TABLE public.category_rules IS
    'Absolute-threshold rules for model categorization, tunable without code deploy. One row per category. Consumed by src/services/model_categorizer.py.';

-- Sensitive/config: RLS on with no permissive policy → backend service_role only
-- (matches the locked-down posture of the Phase 1 registry tables).
ALTER TABLE public.category_rules ENABLE ROW LEVEL SECURITY;

-- Auto-touch updated_at
CREATE OR REPLACE FUNCTION public.update_category_rules_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_category_rules_updated_at_trigger ON public.category_rules;
CREATE TRIGGER update_category_rules_updated_at_trigger
    BEFORE UPDATE ON public.category_rules
    FOR EACH ROW
    EXECUTE FUNCTION public.update_category_rules_updated_at();

-- ============================================================================
-- Part 3: seed default rules (spec §3). Idempotent via ON CONFLICT.
-- ============================================================================
INSERT INTO public.category_rules (category, dimension, operator, threshold, threshold2, params) VALUES
    -- Relative-feeling dims, absolute cutoffs:
    ('cheapest',     'blended_price',  'lte',  0.50,     NULL, '{"weight_input": 0.25, "weight_output": 0.75}'::jsonb),
    ('fastest',      'latency_tier',   'lte',  2,        NULL, '{}'::jsonb),
    ('largest',      'context_length', 'gte',  200000,   NULL, '{}'::jsonb),
    ('smartest',     'quality',        'gte',  85,       NULL, '{}'::jsonb),
    -- Capability dims:
    ('long-context', 'context_length', 'gte',  128000,   NULL, '{}'::jsonb),
    ('coding',       'quality_code',   'gte',  85,       NULL, '{}'::jsonb),
    ('reasoning',    'is_reasoning',   'eq',   NULL,     NULL, '{}'::jsonb),
    ('vision',       'modality',       'contains', NULL, NULL, '{"needles": ["image", "vision", "multimodal"]}'::jsonb),
    ('free',         'is_free',        'eq',   NULL,     NULL, '{}'::jsonb),
    -- Composite: quality per $/1M tokens (higher = better value):
    ('balanced',     'value_ratio',    'gte',  200,      NULL, '{"note": "quality divided by blended $/1M; retune from live distribution at rollout"}'::jsonb),
    -- Coarse quality tiers (mutually exclusive; engine assigns exactly one):
    ('flagship',     'quality_band',   'band', 90,       NULL, '{}'::jsonb),
    ('mid',          'quality_band',   'band', 70,       90,   '{}'::jsonb),
    ('budget',       'quality_band',   'band', 0,        70,   '{}'::jsonb)
ON CONFLICT (category) DO NOTHING;
