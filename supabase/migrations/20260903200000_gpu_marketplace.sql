-- Migration: GPU marketplace -- provider/node registry, verified work,
-- earnings/settlements, public utilization rollup (Milestone 4,
-- gatewayz-backend#2261 epic, #2262-#2267).
-- Created: 2026-09-03
-- Description:
--   Single migration for ALL Milestone 4 tables (spec section 2). Every
--   other M4 workstream (community routing, spot-check verification,
--   payouts, public transparency) builds on these tables and adds no
--   migrations of its own. Owned by W-A1 (#2262).
--
--   RLS is enabled on every table, service-role only (no policy), matching
--   user_wallets/wallet_stakes/faucet_claims -- including
--   gpu_utilization_hourly. It's an aggregate-only rollup (spec section 6),
--   but public reads go through the rate-limited, cached W-C API
--   (/gpu/public/*), not a direct anon PostgREST policy -- a policy here
--   would let any holder of the public anon key bypass that endpoint's
--   60/min/IP limit and 30s cache entirely (W-C review finding).
--
--   provider_work intentionally stores NO prompt/response content -- only
--   hashes (threat model G3, spec section 3 / 4). Community GPU operators
--   see prompt content by construction (spec section 1's trust-boundary
--   decision); this table must never become a second copy of it.
--
--   See spec: .../scratchpad/m4/spec.md sections 2-3.

CREATE TABLE IF NOT EXISTS public.gpu_providers (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    payout_wallet_address text NOT NULL,
    display_name        text NOT NULL,
    contact_email       text,
    status              text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'suspended')),
    region_default       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    approved_at          timestamptz,
    approved_by          bigint REFERENCES public.users(id),
    -- One provider registration per user.
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_gpu_providers_status ON public.gpu_providers (status);

ALTER TABLE public.gpu_providers ENABLE ROW LEVEL SECURITY;


CREATE TABLE IF NOT EXISTS public.gpu_nodes (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id         bigint NOT NULL REFERENCES public.gpu_providers(id) ON DELETE CASCADE,
    name                text NOT NULL,
    region              text NOT NULL,
    gpu_model           text NOT NULL,
    vram_gb             int NOT NULL,
    bandwidth_mbps      int,
    endpoint_url        text NOT NULL,
    endpoint_api_key_encrypted text,
    models              jsonb NOT NULL DEFAULT '[]'::jsonb,
    node_token_hash     text NOT NULL UNIQUE,
    status              text NOT NULL DEFAULT 'registered'
                             CHECK (status IN ('registered', 'active', 'degraded', 'offline', 'disabled')),
    last_heartbeat_at   timestamptz,
    health_score        numeric NOT NULL DEFAULT 100,
    outstanding_requests int NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gpu_nodes_provider_id ON public.gpu_nodes (provider_id);
CREATE INDEX IF NOT EXISTS idx_gpu_nodes_status ON public.gpu_nodes (status);

ALTER TABLE public.gpu_nodes ENABLE ROW LEVEL SECURITY;


CREATE TABLE IF NOT EXISTS public.provider_work (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    billing_ref         text NOT NULL UNIQUE,
    node_id             bigint NOT NULL REFERENCES public.gpu_nodes(id) ON DELETE CASCADE,
    provider_id         bigint NOT NULL REFERENCES public.gpu_providers(id) ON DELETE CASCADE,
    model               text NOT NULL,
    prompt_hash         text NOT NULL,
    response_hash       text,
    prompt_tokens       int NOT NULL DEFAULT 0,
    completion_tokens   int NOT NULL DEFAULT 0,
    latency_ms          int,
    status              text NOT NULL CHECK (status IN ('completed', 'failed')),
    attested            boolean NOT NULL DEFAULT false,
    attestation_sig      text,
    verification        text NOT NULL DEFAULT 'pending'
                             CHECK (verification IN ('pending', 'sampled', 'verified', 'failed', 'skipped')),
    created_at          timestamptz NOT NULL DEFAULT now()
    -- No prompt/response content is ever stored here -- threat model G3.
);

CREATE INDEX IF NOT EXISTS idx_provider_work_node_created
    ON public.provider_work (node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_work_verification
    ON public.provider_work (verification);

ALTER TABLE public.provider_work ENABLE ROW LEVEL SECURITY;


CREATE TABLE IF NOT EXISTS public.provider_payout_rates (
    model_class         text PRIMARY KEY,
    wayz_per_1k_tokens   numeric(78, 0) NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.provider_payout_rates ENABLE ROW LEVEL SECURITY;

-- Testnet placeholder rates, in wei (1 WAYZ = 10^18 wei):
--   small (<=13B params)  -- 0.05 WAYZ / 1k tokens
--   medium (<=34B params) -- 0.10 WAYZ / 1k tokens
--   large (>34B params)   -- 0.25 WAYZ / 1k tokens
INSERT INTO public.provider_payout_rates (model_class, wayz_per_1k_tokens)
VALUES
    ('small', 50000000000000000),
    ('medium', 100000000000000000),
    ('large', 250000000000000000)
ON CONFLICT (model_class) DO NOTHING;


CREATE TABLE IF NOT EXISTS public.provider_earnings (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id         bigint NOT NULL REFERENCES public.gpu_providers(id) ON DELETE CASCADE,
    work_id             bigint NOT NULL REFERENCES public.provider_work(id) ON DELETE CASCADE,
    amount_wei          numeric(78, 0) NOT NULL,
    status              text NOT NULL DEFAULT 'accrued'
                             CHECK (status IN ('accrued', 'settled', 'void')),
    settlement_id        bigint,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_earnings_provider_status
    ON public.provider_earnings (provider_id, status);

ALTER TABLE public.provider_earnings ENABLE ROW LEVEL SECURITY;


CREATE TABLE IF NOT EXISTS public.provider_settlements (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id         bigint NOT NULL REFERENCES public.gpu_providers(id) ON DELETE CASCADE,
    period_start        timestamptz NOT NULL,
    period_end          timestamptz NOT NULL,
    amount_wei          numeric(78, 0) NOT NULL,
    tx_hash             text,
    status              text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'sent', 'failed')),
    error               text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_provider_settlements_provider_id
    ON public.provider_settlements (provider_id);

ALTER TABLE public.provider_settlements ENABLE ROW LEVEL SECURITY;

-- provider_earnings.settlement_id references provider_settlements -- added
-- after both tables exist (created_by-order FK). Guarded so reapplying
-- this migration is idempotent, same as every CREATE/POLICY above.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_provider_earnings_settlement'
    ) THEN
        ALTER TABLE public.provider_earnings
            ADD CONSTRAINT fk_provider_earnings_settlement
            FOREIGN KEY (settlement_id) REFERENCES public.provider_settlements(id);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS public.gpu_utilization_hourly (
    hour                timestamptz NOT NULL,
    region              text NOT NULL,
    model               text NOT NULL,
    requests            int NOT NULL DEFAULT 0,
    completion_tokens   bigint NOT NULL DEFAULT 0,
    prompt_tokens       bigint NOT NULL DEFAULT 0,
    avg_latency_ms      int,
    error_rate          numeric,
    active_nodes        int NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, region, model)
);

-- Public transparency rollup (spec section 6) -- aggregate only, no
-- wallet/endpoint/provider/user identity is ever stored in this table.
-- No anon policy: public access is served exclusively through W-C's
-- rate-limited, cached /gpu/public/* API (server-side reads use the
-- service-role client, which RLS never restricts) -- see migration header.
ALTER TABLE public.gpu_utilization_hourly ENABLE ROW LEVEL SECURITY;
