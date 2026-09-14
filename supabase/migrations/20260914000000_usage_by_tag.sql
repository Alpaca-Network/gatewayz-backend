-- Usage rollups grouped by the caller's attribution tag.
--
-- Aggregated in SQL rather than by fetching rows and summing in Python. This
-- repo has already been bitten by that shape once: get_provider_verified_volume_7d
-- used a PostgREST .limit(2000) plus a client-side sum, which silently
-- undercounted exactly the high-volume providers the figure existed to measure
-- (#2295 review). A capped sum is not a smaller answer, it is a wrong one.
--
-- Failures are counted SEPARATELY, never folded into the totals. A failed call
-- cost real compute, and averaging it away flatters the initiative that spent
-- it -- the partner attribution plan is explicit about this and so are we.

CREATE OR REPLACE FUNCTION public.gatewayz_usage_by_tag(
    p_user_id  BIGINT,
    p_tag      TEXT DEFAULT NULL,
    p_since    TIMESTAMPTZ DEFAULT NULL
)
RETURNS TABLE (
    tag            TEXT,
    calls          BIGINT,
    failed         BIGINT,
    input_tokens   BIGINT,
    output_tokens  BIGINT,
    cost_usd       NUMERIC,
    first_at       TIMESTAMPTZ,
    last_at        TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        r.metadata->>'tag'                                             AS tag,
        COUNT(*) FILTER (WHERE r.status = 'completed')                 AS calls,
        COUNT(*) FILTER (WHERE r.status IS DISTINCT FROM 'completed')  AS failed,
        COALESCE(SUM(r.input_tokens)  FILTER (WHERE r.status = 'completed'), 0) AS input_tokens,
        COALESCE(SUM(r.output_tokens) FILTER (WHERE r.status = 'completed'), 0) AS output_tokens,
        COALESCE(SUM(r.cost_usd)      FILTER (WHERE r.status = 'completed'), 0) AS cost_usd,
        MIN(r.created_at) AS first_at,
        MAX(r.created_at) AS last_at
    FROM public.chat_completion_requests r
    WHERE r.user_id = p_user_id
      AND r.metadata ? 'tag'
      AND (p_tag   IS NULL OR r.metadata->>'tag' = p_tag)
      AND (p_since IS NULL OR r.created_at >= p_since)
    GROUP BY r.metadata->>'tag'
    ORDER BY MAX(r.created_at) DESC;
$$;

-- service_role only: the FastAPI app is the sole caller, and the function is
-- SECURITY DEFINER so it must not be reachable by anon/authenticated.
REVOKE ALL ON FUNCTION public.gatewayz_usage_by_tag(BIGINT, TEXT, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.gatewayz_usage_by_tag(BIGINT, TEXT, TIMESTAMPTZ) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.gatewayz_usage_by_tag(BIGINT, TEXT, TIMESTAMPTZ) TO service_role;

-- The lookup this function makes on every call.
CREATE INDEX IF NOT EXISTS idx_ccr_user_tag_created
    ON public.chat_completion_requests (user_id, created_at DESC)
    WHERE metadata ? 'tag';
