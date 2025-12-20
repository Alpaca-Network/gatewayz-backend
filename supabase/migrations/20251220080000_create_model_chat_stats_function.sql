-- Migration: Create function to get aggregated chat completion stats for a model
-- Created: 2025-12-20
-- Description: Creates a PostgreSQL function to efficiently aggregate chat completion
--              request statistics for a given model.

-- ============================================================================
-- FUNCTION: get_model_chat_stats
-- ============================================================================
CREATE OR REPLACE FUNCTION get_model_chat_stats(p_model_id INTEGER)
RETURNS TABLE (
    total_requests BIGINT,
    total_tokens BIGINT,
    avg_input_tokens NUMERIC,
    avg_output_tokens NUMERIC,
    avg_processing_time_ms NUMERIC,
    total_processing_time_ms BIGINT,
    success_rate NUMERIC,
    completed_requests BIGINT,
    failed_requests BIGINT,
    last_request_at TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT AS total_requests,
        COALESCE(SUM(ccr.total_tokens), 0)::BIGINT AS total_tokens,
        COALESCE(ROUND(AVG(ccr.input_tokens), 2), 0) AS avg_input_tokens,
        COALESCE(ROUND(AVG(ccr.output_tokens), 2), 0) AS avg_output_tokens,
        COALESCE(ROUND(AVG(ccr.processing_time_ms), 2), 0) AS avg_processing_time_ms,
        COALESCE(SUM(ccr.processing_time_ms), 0)::BIGINT AS total_processing_time_ms,
        COALESCE(
            ROUND(
                (COUNT(*) FILTER (WHERE ccr.status = 'completed')::NUMERIC /
                 NULLIF(COUNT(*), 0) * 100),
                2
            ),
            0
        ) AS success_rate,
        COUNT(*) FILTER (WHERE ccr.status = 'completed')::BIGINT AS completed_requests,
        COUNT(*) FILTER (WHERE ccr.status = 'failed')::BIGINT AS failed_requests,
        MAX(ccr.created_at) AS last_request_at
    FROM
        chat_completion_requests ccr
    WHERE
        ccr.model_id = p_model_id;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_model_chat_stats(INTEGER) IS
'Returns aggregated chat completion request statistics for a specific model';

-- Grant execute permission to authenticated users and service role
GRANT EXECUTE ON FUNCTION get_model_chat_stats(INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION get_model_chat_stats(INTEGER) TO service_role;
