-- Per-key ARRIVALS and FAILURES, separately from the cap counter.
--
-- Why this exists
-- ---------------
-- `api_keys_new.requests_used` is a *cap* counter: it moves only when a call
-- consumes the key's request allowance, which a rejected call does not. It is
-- correct for what it is for -- and it is the wrong number to answer "did
-- anything reach this key?".
--
-- On 2026-09-21 that gap cost a day. A partner's key showed requests_used = 1
-- while every call to the vendor it pointed at was failing, and 1 is equally
-- consistent with "one call ever" and with "six days of 503s". The question was
-- only settled by testing which field moves on a failed call, against
-- production, by hand.
--
-- The tag rollup (gatewayz_usage_by_tag, 2026-09-14) already reports `failed`
-- beside `calls` for exactly this reason. This is the same discipline applied
-- to the per-key view, over the same ledger, so the two cannot disagree.
--
-- Failures are counted SEPARATELY and never folded into the totals: a failed
-- call cost real compute, and averaging it away flatters the caller that spent
-- it. Token and cost sums therefore cover completed calls only, while
-- first/last arrival cover EVERY call -- because "when did something last
-- reach this key" is the question a failing integration needs answered.

CREATE OR REPLACE FUNCTION public.gatewayz_arrivals_by_key(
    p_user_id  BIGINT,
    p_since    TIMESTAMPTZ DEFAULT NULL
)
RETURNS TABLE (
    api_key_id        BIGINT,
    arrivals          BIGINT,
    completed         BIGINT,
    failed            BIGINT,
    input_tokens      BIGINT,
    output_tokens     BIGINT,
    cost_usd          NUMERIC,
    first_arrival_at  TIMESTAMPTZ,
    last_arrival_at   TIMESTAMPTZ,
    last_failure_at   TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        r.api_key_id,
        COUNT(*)                                                       AS arrivals,
        COUNT(*) FILTER (WHERE r.status = 'completed')                 AS completed,
        COUNT(*) FILTER (WHERE r.status IS DISTINCT FROM 'completed')  AS failed,
        COALESCE(SUM(r.input_tokens)  FILTER (WHERE r.status = 'completed'), 0) AS input_tokens,
        COALESCE(SUM(r.output_tokens) FILTER (WHERE r.status = 'completed'), 0) AS output_tokens,
        COALESCE(SUM(r.cost_usd)      FILTER (WHERE r.status = 'completed'), 0) AS cost_usd,
        MIN(r.created_at)                                              AS first_arrival_at,
        MAX(r.created_at)                                              AS last_arrival_at,
        MAX(r.created_at) FILTER (WHERE r.status IS DISTINCT FROM 'completed')
                                                                       AS last_failure_at
    FROM public.chat_completion_requests r
    WHERE r.user_id = p_user_id
      AND r.api_key_id IS NOT NULL
      AND (p_since IS NULL OR r.created_at >= p_since)
    GROUP BY r.api_key_id;
$$;

-- service_role only: the FastAPI app is the sole caller, and the function is
-- SECURITY DEFINER so it must not be reachable by anon/authenticated.
-- `REVOKE ... FROM PUBLIC` alone does NOT close this -- Supabase grants EXECUTE
-- to anon/authenticated by default, so they are revoked by name as well.
REVOKE ALL ON FUNCTION public.gatewayz_arrivals_by_key(BIGINT, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.gatewayz_arrivals_by_key(BIGINT, TIMESTAMPTZ) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.gatewayz_arrivals_by_key(BIGINT, TIMESTAMPTZ) TO service_role;

-- The lookup this function makes on every call. Deliberately NOT partial: unlike
-- the tag rollup, which only ever reads tagged rows, this one must see every
-- arrival -- a predicate here would reintroduce the blind spot it exists to close.
CREATE INDEX IF NOT EXISTS idx_ccr_user_key_created
    ON public.chat_completion_requests (user_id, api_key_id, created_at DESC);
