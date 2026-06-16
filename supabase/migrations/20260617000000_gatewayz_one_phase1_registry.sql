-- Migration: Gatewayz One Phase 1 — registry & projection schema
-- Created: 2026-06-17
-- Status:  STAGED / NOT YET APPLIED — review before running. No application
--          code reads these columns/tables yet; this only lays the schema for
--          the control-plane registry (spec §7).
-- Description:
--   * providers      — tiering + routing metadata (retires the ENABLED_PROVIDERS
--                      env var in favour of providers.is_active + tier).
--   * models_catalog — canonical-registry fields (canonical_id, capabilities…).
--   * model_provider_offers (NEW) — the (model × provider) join the smart router
--                      scores over (see src/services/smart_router.py).
--   * routing_policies (NEW)       — per-key/system default policy + weights.
--   * user_memory (NEW)            — portable, model-agnostic per-user memory
--                      (see src/services/context_assembly.py).
--   * chat threading — conversation_id + rolling summary on existing chat tables.
--   All statements are idempotent. New tables have RLS ENABLED with no permissive
--   policies, so only the backend's service_role (which bypasses RLS) can touch
--   them — matching the repo's locked-down posture (2026-05-27 hardening).
-- REVIEW NOTES:
--   * user_id is typed bigint to match the integer user ids used across the
--     codebase; CONFIRM against public.users.id before adding the FK (left off
--     deliberately to avoid a type-mismatch failure on apply).

-- ============================================================================
-- Part 1: providers — tiering + routing metadata
-- ============================================================================
ALTER TABLE public.providers
    ADD COLUMN IF NOT EXISTS tier text NOT NULL DEFAULT 'niche';
ALTER TABLE public.providers
    ADD COLUMN IF NOT EXISTS region_affinity text;
ALTER TABLE public.providers
    ADD COLUMN IF NOT EXISTS async_streaming boolean NOT NULL DEFAULT false;
ALTER TABLE public.providers
    ADD COLUMN IF NOT EXISTS auth_type text;
ALTER TABLE public.providers
    ADD COLUMN IF NOT EXISTS base_url text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'providers_tier_valid'
    ) THEN
        ALTER TABLE public.providers
            ADD CONSTRAINT providers_tier_valid
            CHECK (tier IN ('core', 'aggregator', 'niche'));
    END IF;
END$$;

-- ============================================================================
-- Part 2: models_catalog — canonical registry fields
-- ============================================================================
ALTER TABLE public.models_catalog
    ADD COLUMN IF NOT EXISTS canonical_id text;
ALTER TABLE public.models_catalog
    ADD COLUMN IF NOT EXISTS capabilities jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.models_catalog
    ADD COLUMN IF NOT EXISTS modality text;
ALTER TABLE public.models_catalog
    ADD COLUMN IF NOT EXISTS context_length integer;
ALTER TABLE public.models_catalog
    ADD COLUMN IF NOT EXISTS deprecated_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_models_catalog_canonical
    ON public.models_catalog (canonical_id)
    WHERE deprecated_at IS NULL;

-- ============================================================================
-- Part 3: model_provider_offers (NEW) — the router's scoring join
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.model_provider_offers (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_id    text NOT NULL,
    provider_slug   text NOT NULL,
    native_id       text NOT NULL,
    upstream_cost   numeric(14, 10) NOT NULL DEFAULT 0,  -- per 1k tokens, provider→us
    quality_prior   real NOT NULL DEFAULT 0.5,           -- 0..1
    p50_ms          integer,
    p95_ms          integer,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_provider_offers_unique UNIQUE (canonical_id, provider_slug)
);

CREATE INDEX IF NOT EXISTS idx_mpo_canonical_active
    ON public.model_provider_offers (canonical_id)
    WHERE is_active;

ALTER TABLE public.model_provider_offers ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 4: routing_policies (NEW) — per-key / system default routing policy
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.routing_policies (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    api_key_id      bigint,  -- NULL = system default
    policy          text NOT NULL DEFAULT 'balanced',
    weight_cost     real NOT NULL DEFAULT 0.4,
    weight_latency  real NOT NULL DEFAULT 0.3,
    weight_quality  real NOT NULL DEFAULT 0.3,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT routing_policies_policy_valid
        CHECK (policy IN ('cost', 'latency', 'quality', 'balanced'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_policies_key
    ON public.routing_policies (api_key_id)
    WHERE api_key_id IS NOT NULL;

ALTER TABLE public.routing_policies ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 5: user_memory (NEW) — portable, model-agnostic per-user memory
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_memory (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint NOT NULL,           -- CONFIRM type vs public.users.id, then add FK
    kind        text NOT NULL DEFAULT 'fact',
    content     text NOT NULL,
    salience    real NOT NULL DEFAULT 0.5,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_user_salience
    ON public.user_memory (user_id, salience DESC);

-- Sensitive (per-user). RLS on, no permissive policy → backend service_role only.
ALTER TABLE public.user_memory ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Part 6: chat threading — conversation id + rolling summary
-- ============================================================================
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS conversation_id uuid;
ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS rolling_summary text;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_conversation
    ON public.chat_sessions (conversation_id)
    WHERE conversation_id IS NOT NULL;
