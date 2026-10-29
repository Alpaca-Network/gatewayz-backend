-- Inference jobs: job-scoped API keys + sealed usage records
-- (Gatewayz x GenLayer inference escrow, PRD 2026-10-01 Feature 3).
--
-- A job is one unit of cross-org agent work that a buyer pays for through an
-- on-chain escrow (Alpaca-Network/gatewayz-genlayer). Gatewayz's part:
--
--   * POST /v1/jobs issues ONE api_keys_new row per job (key_name 'job:<job_id>')
--     with a USD spend cap. The cap is enforced on every request with that key
--     (402 job_cap_exhausted), alongside the account's normal credit checks.
--   * Every completed request on that key appends one line of BILLING METADATA
--     to inference_job_usage — {ts, model, provider, tokens_in, tokens_out,
--     cost_usd, commit}. Never prompt or completion content.
--   * POST /v1/jobs/{id}/close seals the log into a Merkle root (the root the
--     GenLayer verdict carries and the escrow stores) and deactivates the key.
--
-- append_job_usage is the only writer of inference_job_usage: it locks the job
-- row, so seq numbers are gapless and spent_usd can never lose an update under
-- concurrent requests. Lines are append-only; a sealed job takes no more lines.

CREATE TABLE IF NOT EXISTS public.inference_jobs (
    job_id           text PRIMARY KEY CHECK (job_id ~ '^0x[0-9a-f]{64}$'),
    user_id          bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    api_key_id       bigint UNIQUE REFERENCES public.api_keys_new(id) ON DELETE SET NULL,
    buyer            text,
    seller           text,
    spec_hash        text NOT NULL CHECK (spec_hash ~ '^0x[0-9a-f]{64}$'),
    cap_usd          numeric(20, 10) NOT NULL CHECK (cap_usd > 0),
    spent_usd        numeric(20, 10) NOT NULL DEFAULT 0,
    deadline         timestamptz NOT NULL,
    status           text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'closed')),
    usage_root       text CHECK (usage_root IS NULL OR usage_root ~ '^0x[0-9a-f]{64}$'),
    usage_requests   integer,
    usage_tokens_in  bigint,
    usage_tokens_out bigint,
    usage_cost_usd   text,
    escrow_tx        text,
    genlayer_tx      text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz
);

CREATE INDEX IF NOT EXISTS inference_jobs_user_id_idx ON public.inference_jobs (user_id);

CREATE TABLE IF NOT EXISTS public.inference_job_usage (
    job_id     text NOT NULL REFERENCES public.inference_jobs(job_id) ON DELETE CASCADE,
    seq        integer NOT NULL CHECK (seq >= 0),
    entry      jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, seq)
);

CREATE OR REPLACE FUNCTION public.append_job_usage(p_job_id text, p_entry jsonb, p_cost numeric)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_status text;
    v_seq    integer;
BEGIN
    SELECT status INTO v_status FROM inference_jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'job_not_found';
    END IF;
    IF v_status <> 'running' THEN
        -- A request that was admitted before close and finished after it. Its
        -- cost is real (the account was charged) but the record is sealed; it
        -- is reported, not silently folded into a root that is already public.
        RAISE EXCEPTION 'job_not_running';
    END IF;
    SELECT COALESCE(MAX(seq) + 1, 0) INTO v_seq FROM inference_job_usage WHERE job_id = p_job_id;
    INSERT INTO inference_job_usage (job_id, seq, entry) VALUES (p_job_id, v_seq, p_entry);
    UPDATE inference_jobs SET spent_usd = spent_usd + GREATEST(p_cost, 0) WHERE job_id = p_job_id;
    RETURN v_seq;
END;
$$;

ALTER TABLE public.inference_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inference_job_usage ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.inference_jobs, public.inference_job_usage FROM PUBLIC;
REVOKE ALL ON public.inference_jobs, public.inference_job_usage FROM anon, authenticated;
GRANT ALL ON public.inference_jobs, public.inference_job_usage TO service_role;

REVOKE ALL ON FUNCTION public.append_job_usage(text, jsonb, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_job_usage(text, jsonb, numeric) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.append_job_usage(text, jsonb, numeric) TO service_role;
