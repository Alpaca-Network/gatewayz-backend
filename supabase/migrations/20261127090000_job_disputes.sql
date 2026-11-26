-- Customer cost disputes on job usage lines (Gatewayz x GenLayer, shadow mode).
--
-- job_disputes: one row per dispute a key owner opens on lines of a sealed job.
--   dispute_id is the CostDispute contract's dispute id (bytes32 hex). The judge is
--   the CostDispute Intelligent Contract on GenLayer; Gatewayz only submits and
--   records its verdict. Shadow mode: no money moves on a verdict.
--   status: submitted -> adjudicating -> decided -> final, or one of the TERMINAL
--   failures undetermined / error. A terminal failure is never left in a
--   retryable status, and it releases its lines (job_dispute_lines) so the owner
--   can dispute them again: a line is only marked on chain when the dispute
--   transaction succeeds.
--
-- job_dispute_lines: (job_id, line_index) claimed by at most one live dispute.
--   The primary key makes "a line is disputed once" hold under concurrent requests.

CREATE TABLE IF NOT EXISTS public.job_disputes (
    dispute_id        text PRIMARY KEY CHECK (dispute_id ~ '^0x[0-9a-f]{64}$'),
    job_id            text NOT NULL REFERENCES public.inference_jobs(job_id) ON DELETE CASCADE,
    user_id           bigint NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    usage_root        text NOT NULL CHECK (usage_root ~ '^0x[0-9a-f]{64}$'),
    line_indexes      integer[] NOT NULL CHECK (cardinality(line_indexes) BETWEEN 1 AND 10),
    claims            jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_uris      jsonb NOT NULL DEFAULT '{}'::jsonb,
    network           text NOT NULL,
    contract_address  text NOT NULL,
    policy_uri        text NOT NULL,
    status            text NOT NULL DEFAULT 'submitted' CHECK (status IN
                        ('submitted', 'adjudicating', 'decided', 'final',
                         'undetermined', 'error')),
    verdict           jsonb,
    billed_usd        numeric(20, 9),
    refund_usd        numeric(20, 9),
    genlayer_tx       text,
    submit_attempts   integer NOT NULL DEFAULT 0,
    error             text,
    decided_at        timestamptz,
    finalized_at      timestamptz,
    last_checked_at   timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_disputes_job_idx ON public.job_disputes (job_id);
CREATE INDEX IF NOT EXISTS job_disputes_user_idx ON public.job_disputes (user_id);
CREATE INDEX IF NOT EXISTS job_disputes_open_idx ON public.job_disputes (status)
    WHERE status IN ('submitted', 'adjudicating', 'decided');

CREATE TABLE IF NOT EXISTS public.job_dispute_lines (
    job_id      text NOT NULL REFERENCES public.inference_jobs(job_id) ON DELETE CASCADE,
    line_index  integer NOT NULL CHECK (line_index >= 0),
    dispute_id  text NOT NULL REFERENCES public.job_disputes(dispute_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, line_index)
);

CREATE INDEX IF NOT EXISTS job_dispute_lines_dispute_idx ON public.job_dispute_lines (dispute_id);

ALTER TABLE public.job_disputes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.job_dispute_lines ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.job_disputes, public.job_dispute_lines FROM PUBLIC;
REVOKE ALL ON public.job_disputes, public.job_dispute_lines FROM anon, authenticated;
GRANT ALL ON public.job_disputes, public.job_dispute_lines TO service_role;
