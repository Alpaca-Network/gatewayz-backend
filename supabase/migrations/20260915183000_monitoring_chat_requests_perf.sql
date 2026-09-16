-- Monitoring chat-request endpoints: indexes + server-side aggregation.
--
-- Context
-- -------
-- /api/monitoring/chat-requests* aggregated chat_completion_requests in Python
-- by asking PostgREST for "all rows". PostgREST caps a response at db-max-rows
-- (1000), so those handlers never saw the whole table - they paid for the
-- attempt and then counted an arbitrary slice. The /models handler was worse:
-- it walked the entire models table (13k+ rows) and issued a stats query per
-- row, one HTTP round trip each. The route handlers are now bounded; this
-- migration gives the database the indexes and the aggregate functions those
-- bounded queries need.
--
-- Two of the existing RPCs were dead on arrival: get_models_with_requests() and
-- get_models_with_requests_by_provider() select m.model_id, a column that does
-- not exist on public.models (the vendor-facing identifier is
-- provider_model_id). Every call returned 42703, so the endpoint always fell
-- through to the 13k-round-trip fallback. Both are recreated correctly below.
--
-- Locking note
-- ------------
-- These are plain CREATE INDEX statements, not CREATE INDEX CONCURRENTLY.
-- CONCURRENTLY cannot run inside a transaction block and `supabase db push`
-- (see .github/workflows/supabase-migrations.yml) wraps the push in one, which
-- is why no migration in this repo uses it. A plain CREATE INDEX takes a SHARE
-- lock that blocks writes to chat_completion_requests for the duration of the
-- build; at the current table size (~80k rows, well under 100 MB) that is
-- sub-second. If this table grows by an order of magnitude, build the same
-- indexes out-of-band first:
--
--   CREATE INDEX CONCURRENTLY idx_ccr_created_at_desc
--       ON public.chat_completion_requests (created_at DESC);
--   CREATE INDEX CONCURRENTLY idx_ccr_model_status_created_covering
--       ON public.chat_completion_requests (model_id, status, created_at DESC)
--       INCLUDE (input_tokens, output_tokens, processing_time_ms);
--
-- and this migration becomes a no-op thanks to IF NOT EXISTS.

-- ============================================================================
-- 1. Indexes
-- ============================================================================

-- Predicate served: `created_at >= $window` plus `ORDER BY created_at DESC
-- LIMIT n` with no other filter. That is the unfiltered
-- /api/monitoring/chat-requests page query and the bounded recent-rows samples
-- that /chat-requests/providers, /chat-requests/counts and
-- /chat-requests/models now take instead of scanning the table. Existing
-- indexes all lead with another column (status, model_id, user_id), so none of
-- them can drive an unfiltered ordered scan.
CREATE INDEX IF NOT EXISTS "idx_ccr_created_at_desc"
    ON "public"."chat_completion_requests" ("created_at" DESC);

-- Predicates served:
--   * model_id = $1 AND status = $2 ORDER BY created_at DESC  (the model detail
--     page and the errors page filtered to one model)
--   * GROUP BY model_id with SUM(input_tokens), SUM(output_tokens),
--     AVG(processing_time_ms) - get_models_with_requests(),
--     get_models_with_requests_by_provider(), get_model_request_stats()
--   * model_id = $1 AND created_at BETWEEN ... returning only the three metric
--     columns - /chat-requests/plot-data
-- The INCLUDE columns let all three aggregate paths run as index-only scans;
-- without them every aggregate has to visit the heap for 80k rows.
-- idx_chat_completion_requests_model_id_status is a strict prefix of this index
-- and is now redundant - dropping it (DROP INDEX CONCURRENTLY, out-of-band) is
-- left as a follow-up so this migration stays additive.
CREATE INDEX IF NOT EXISTS "idx_ccr_model_status_created_covering"
    ON "public"."chat_completion_requests" ("model_id", "status", "created_at" DESC)
    INCLUDE ("input_tokens", "output_tokens", "processing_time_ms");

ANALYZE "public"."chat_completion_requests";

-- ============================================================================
-- 2. Fix get_models_with_requests() - m.model_id does not exist
-- ============================================================================

DROP FUNCTION IF EXISTS get_models_with_requests();

CREATE OR REPLACE FUNCTION get_models_with_requests()
RETURNS TABLE (
    model_id INTEGER,
    model_identifier TEXT,
    model_name TEXT,
    provider_model_id TEXT,
    provider JSONB,
    stats JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id AS model_id,
        -- public.models has no model_id column; provider_model_id is the
        -- vendor-facing identifier the dashboard renders as model_identifier.
        m.provider_model_id::TEXT AS model_identifier,
        m.model_name::TEXT,
        m.provider_model_id::TEXT,
        jsonb_build_object(
            'id', p.id,
            'name', p.name,
            'slug', p.slug
        ) AS provider,
        jsonb_build_object(
            'total_requests', COUNT(ccr.id),
            'total_input_tokens', COALESCE(SUM(ccr.input_tokens), 0),
            'total_output_tokens', COALESCE(SUM(ccr.output_tokens), 0),
            'total_tokens', COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0),
            'avg_processing_time_ms', COALESCE(ROUND(AVG(ccr.processing_time_ms)::numeric, 2), 0)
        ) AS stats
    FROM models m
    INNER JOIN providers p ON p.id = m.provider_id
    INNER JOIN chat_completion_requests ccr ON ccr.model_id = m.id
    GROUP BY m.id, m.model_name, m.provider_model_id, p.id, p.name, p.slug
    HAVING COUNT(ccr.id) > 0
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_models_with_requests() IS
'Aggregates chat completion request statistics for every model with at least one
request, entirely inside the database. Replaces the previous definition, which
selected the non-existent models.model_id and therefore always failed with
42703, forcing /api/monitoring/chat-requests/models into a per-model fallback
that issued one query per row of the 13k-row models table.';

-- ============================================================================
-- 3. Fix get_models_with_requests_by_provider(INTEGER) - same phantom column
-- ============================================================================

DROP FUNCTION IF EXISTS get_models_with_requests_by_provider(INTEGER);

CREATE OR REPLACE FUNCTION get_models_with_requests_by_provider(p_provider_id INTEGER)
RETURNS TABLE (
    model_id INTEGER,
    model_identifier TEXT,
    model_name TEXT,
    provider_model_id TEXT,
    provider JSONB,
    stats JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id AS model_id,
        m.provider_model_id::TEXT AS model_identifier,
        m.model_name::TEXT,
        m.provider_model_id::TEXT,
        jsonb_build_object(
            'id', p.id,
            'name', p.name,
            'slug', p.slug
        ) AS provider,
        jsonb_build_object(
            'total_requests', COUNT(ccr.id),
            'total_input_tokens', COALESCE(SUM(ccr.input_tokens), 0),
            'total_output_tokens', COALESCE(SUM(ccr.output_tokens), 0),
            'total_tokens', COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0),
            'avg_processing_time_ms', COALESCE(ROUND(AVG(ccr.processing_time_ms)::numeric, 2), 0)
        ) AS stats
    FROM models m
    INNER JOIN providers p ON p.id = m.provider_id
    INNER JOIN chat_completion_requests ccr ON ccr.model_id = m.id
    WHERE m.provider_id = p_provider_id
    GROUP BY m.id, m.model_name, m.provider_model_id, p.id, p.name, p.slug
    HAVING COUNT(ccr.id) > 0
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_models_with_requests_by_provider(INTEGER) IS
'Provider-scoped variant of get_models_with_requests(). Recreated to select
models.provider_model_id instead of the non-existent models.model_id.';

-- ============================================================================
-- 4. New: get_model_request_counts() for /chat-requests/counts
-- ============================================================================
-- /api/monitoring/chat-requests/counts had no RPC at all: it fetched every
-- request joined two levels deep and counted them in a Python dict. The row
-- shape returned here is exactly the shape that handler already emitted, so the
-- admin panel sees no difference.

DROP FUNCTION IF EXISTS get_model_request_counts();

CREATE OR REPLACE FUNCTION get_model_request_counts()
RETURNS TABLE (
    model_id INTEGER,
    model_name TEXT,
    model_identifier TEXT,
    provider_name TEXT,
    provider_slug TEXT,
    request_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id AS model_id,
        m.model_name::TEXT,
        m.provider_model_id::TEXT AS model_identifier,
        p.name::TEXT AS provider_name,
        p.slug::TEXT AS provider_slug,
        COUNT(ccr.id) AS request_count
    FROM models m
    INNER JOIN providers p ON p.id = m.provider_id
    INNER JOIN chat_completion_requests ccr ON ccr.model_id = m.id
    GROUP BY m.id, m.model_name, m.provider_model_id, p.name, p.slug
    HAVING COUNT(ccr.id) > 0
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_model_request_counts() IS
'Request count per model, grouped in the database. Backs
/api/monitoring/chat-requests/counts, which previously pulled every joined row
over HTTP and counted in Python - a scan PostgREST silently truncated at
db-max-rows, so the reported counts were wrong as well as slow.';

GRANT EXECUTE ON FUNCTION get_model_request_counts() TO service_role;
GRANT EXECUTE ON FUNCTION get_models_with_requests() TO service_role;
GRANT EXECUTE ON FUNCTION get_models_with_requests_by_provider(INTEGER) TO service_role;
