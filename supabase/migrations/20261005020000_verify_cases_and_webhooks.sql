-- Gatewayz Verify (GenLayer) + outbound webhooks — PRD 2026-10-01, Feature 2.
--
-- verify_cases: one row per case submitted to the VerifyJob Intelligent Contract.
--   case_id is the contract's job_id (bytes32 hex). For a case opened from a
--   Gatewayz job it IS the job id, so the escrow, the job and the verdict share
--   one key. Private cases store ONLY the redacted URI: Gatewayz never forwards,
--   or keeps a pointer to, content the caller marked private.
--   status: submitted -> adjudicating -> decided -> (appealed ->) final, or one of
--   the TERMINAL failures undetermined / error. A terminal failure is never left
--   in a retryable status (see memory: "swallowed failure + retryable status").
--
-- verify_key_caps: the per-key verify budget, separate from the inference cap.
--   charge_verify_cap() checks and increments in ONE statement, so concurrent
--   submissions cannot both slip under the cap.
--
-- outbound_webhooks: customer endpoints for job/case state changes. The signing
--   secret is stored Fernet-encrypted (same keyring as BYOK keys) and shown once.

CREATE TABLE IF NOT EXISTS public.verify_cases (
    case_id           text PRIMARY KEY CHECK (case_id ~ '^0x[0-9a-f]{64}$'),
    user_id           bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    api_key_id        bigint REFERENCES public.api_keys_new(id) ON DELETE SET NULL,
    job_id            text REFERENCES public.inference_jobs(job_id) ON DELETE SET NULL,
    spec_uri          text NOT NULL,
    spec_hash         text NOT NULL CHECK (spec_hash ~ '^0x[0-9a-f]{64}$'),
    deliverable_uri   text NOT NULL,
    deliverable_hash  text NOT NULL CHECK (deliverable_hash ~ '^0x[0-9a-f]{64}$'),
    private           boolean NOT NULL DEFAULT false,
    usage_root        text NOT NULL CHECK (usage_root ~ '^0x[0-9a-f]{64}$'),
    rubric            jsonb NOT NULL,
    rubric_hash       text NOT NULL,
    network           text NOT NULL,
    contract_address  text NOT NULL,
    status            text NOT NULL DEFAULT 'submitted' CHECK (status IN
                        ('submitted', 'adjudicating', 'decided', 'appealed', 'final',
                         'undetermined', 'error')),
    pass              boolean,
    score             integer,
    reasons           jsonb,
    genlayer_tx       text,
    appeal_tx         text,
    charged_usd       numeric(20, 10) NOT NULL DEFAULT 0,
    submit_attempts   integer NOT NULL DEFAULT 0,
    error             text,
    decided_at        timestamptz,
    finalized_at      timestamptz,
    last_checked_at   timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS verify_cases_user_idx ON public.verify_cases (user_id);
CREATE INDEX IF NOT EXISTS verify_cases_open_idx ON public.verify_cases (status)
    WHERE status IN ('submitted', 'adjudicating', 'decided', 'appealed');

CREATE TABLE IF NOT EXISTS public.verify_key_caps (
    api_key_id  bigint PRIMARY KEY REFERENCES public.api_keys_new(id) ON DELETE CASCADE,
    cap_usd     numeric(20, 10) NOT NULL CHECK (cap_usd >= 0),
    spent_usd   numeric(20, 10) NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Returns true and books the charge iff it fits under the key's cap. A key with
-- no row yet gets p_default_cap. One statement: no read-then-write race.
CREATE OR REPLACE FUNCTION public.charge_verify_cap(
    p_api_key_id bigint, p_amount numeric, p_default_cap numeric)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_ok boolean;
BEGIN
    INSERT INTO verify_key_caps (api_key_id, cap_usd)
    VALUES (p_api_key_id, p_default_cap)
    ON CONFLICT (api_key_id) DO NOTHING;

    UPDATE verify_key_caps
       SET spent_usd = spent_usd + p_amount, updated_at = now()
     WHERE api_key_id = p_api_key_id
       AND spent_usd + p_amount <= cap_usd
    RETURNING true INTO v_ok;
    RETURN COALESCE(v_ok, false);
END;
$$;

-- Give a charge back when a case could not be submitted at all.
CREATE OR REPLACE FUNCTION public.refund_verify_cap(p_api_key_id bigint, p_amount numeric)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    UPDATE verify_key_caps
       SET spent_usd = GREATEST(spent_usd - p_amount, 0), updated_at = now()
     WHERE api_key_id = p_api_key_id;
$$;

CREATE TABLE IF NOT EXISTS public.outbound_webhooks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    url             text NOT NULL,
    events          text[] NOT NULL,
    secret_enc      text NOT NULL,
    key_version     integer,
    active          boolean NOT NULL DEFAULT true,
    failure_count   integer NOT NULL DEFAULT 0,
    last_status     integer,
    last_attempt_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outbound_webhooks_user_idx ON public.outbound_webhooks (user_id) WHERE active;

ALTER TABLE public.verify_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verify_key_caps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbound_webhooks ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.verify_cases, public.verify_key_caps, public.outbound_webhooks FROM PUBLIC;
REVOKE ALL ON public.verify_cases, public.verify_key_caps, public.outbound_webhooks FROM anon, authenticated;
GRANT ALL ON public.verify_cases, public.verify_key_caps, public.outbound_webhooks TO service_role;

REVOKE ALL ON FUNCTION public.charge_verify_cap(bigint, numeric, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.charge_verify_cap(bigint, numeric, numeric) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.charge_verify_cap(bigint, numeric, numeric) TO service_role;
REVOKE ALL ON FUNCTION public.refund_verify_cap(bigint, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.refund_verify_cap(bigint, numeric) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refund_verify_cap(bigint, numeric) TO service_role;
