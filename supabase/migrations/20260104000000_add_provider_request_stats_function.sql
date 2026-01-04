-- Migration: Add optimized functions for chat completion request statistics
-- These functions efficiently aggregate chat completion request stats
-- without fetching all individual records

-- ============================================================================
-- 1. Provider Request Statistics
-- ============================================================================

-- Drop function if exists (for re-running migration)
DROP FUNCTION IF EXISTS get_provider_request_stats();

-- Create optimized function for provider request statistics
CREATE OR REPLACE FUNCTION get_provider_request_stats()
RETURNS TABLE (
    provider_id INTEGER,
    name TEXT,
    slug TEXT,
    models_with_requests BIGINT,
    total_requests BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.id AS provider_id,
        p.name,
        p.slug,
        COUNT(DISTINCT m.id) AS models_with_requests,
        COUNT(ccr.id) AS total_requests
    FROM providers p
    INNER JOIN models m ON m.provider_id = p.id
    INNER JOIN chat_completion_requests ccr ON ccr.model_id = m.id
    GROUP BY p.id, p.name, p.slug
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_provider_request_stats() IS
'Efficiently aggregates chat completion request statistics by provider.
Returns provider info, count of distinct models with requests, and total request count.
Optimized for performance using database-level aggregation instead of fetching all records.';

-- ============================================================================
-- 2. Model Request Statistics (for all models)
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
        m.model_id AS model_identifier,
        m.model_name,
        m.provider_model_id,
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
    GROUP BY m.id, m.model_id, m.model_name, m.provider_model_id, p.id, p.name, p.slug
    HAVING COUNT(ccr.id) > 0
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_models_with_requests() IS
'Efficiently aggregates chat completion request statistics for all models.
Returns model info with aggregated stats (counts, tokens, avg processing time).
Only includes models that have at least one request.';

-- ============================================================================
-- 3. Model Request Statistics (filtered by provider)
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
        m.model_id AS model_identifier,
        m.model_name,
        m.provider_model_id,
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
    GROUP BY m.id, m.model_id, m.model_name, m.provider_model_id, p.id, p.name, p.slug
    HAVING COUNT(ccr.id) > 0
    ORDER BY COUNT(ccr.id) DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_models_with_requests_by_provider(INTEGER) IS
'Efficiently aggregates chat completion request statistics for models from a specific provider.
Returns model info with aggregated stats filtered by provider_id.
Only includes models that have at least one request.';

-- ============================================================================
-- 4. Single Model Request Statistics
-- ============================================================================

DROP FUNCTION IF EXISTS get_model_request_stats(INTEGER);

CREATE OR REPLACE FUNCTION get_model_request_stats(p_model_id INTEGER)
RETURNS TABLE (
    total_requests BIGINT,
    total_input_tokens BIGINT,
    total_output_tokens BIGINT,
    total_tokens BIGINT,
    avg_processing_time_ms NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(ccr.id) AS total_requests,
        COALESCE(SUM(ccr.input_tokens), 0) AS total_input_tokens,
        COALESCE(SUM(ccr.output_tokens), 0) AS total_output_tokens,
        COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0) AS total_tokens,
        COALESCE(ROUND(AVG(ccr.processing_time_ms)::numeric, 2), 0) AS avg_processing_time_ms
    FROM chat_completion_requests ccr
    WHERE ccr.model_id = p_model_id;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_model_request_stats(INTEGER) IS
'Efficiently aggregates chat completion request statistics for a single model.
Returns aggregated stats (counts, tokens, avg processing time) for the specified model_id.';
