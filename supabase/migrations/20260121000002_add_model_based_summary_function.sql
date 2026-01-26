-- Migration: Add function to get chat completion summary with model-based filtering
-- This function performs database-side aggregation for model analytics dashboards

-- Drop function if it exists
DROP FUNCTION IF EXISTS get_chat_completion_summary_by_filters(INTEGER, INTEGER, TEXT, TIMESTAMPTZ, TIMESTAMPTZ);

-- Create function to get aggregated summary statistics with flexible filtering
CREATE OR REPLACE FUNCTION get_chat_completion_summary_by_filters(
    p_model_id INTEGER DEFAULT NULL,
    p_provider_id INTEGER DEFAULT NULL,
    p_model_name TEXT DEFAULT NULL,
    p_start_date TIMESTAMPTZ DEFAULT NULL,
    p_end_date TIMESTAMPTZ DEFAULT NULL
)
RETURNS TABLE (
    total_requests BIGINT,
    total_input_tokens BIGINT,
    total_output_tokens BIGINT,
    total_tokens BIGINT,
    avg_input_tokens NUMERIC,
    avg_output_tokens NUMERIC,
    avg_processing_time_ms NUMERIC,
    completed_requests BIGINT,
    failed_requests BIGINT,
    success_rate NUMERIC,
    first_request_at TIMESTAMPTZ,
    last_request_at TIMESTAMPTZ,
    total_cost_usd NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT AS total_requests,
        COALESCE(SUM(ccr.input_tokens), 0)::BIGINT AS total_input_tokens,
        COALESCE(SUM(ccr.output_tokens), 0)::BIGINT AS total_output_tokens,
        COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0)::BIGINT AS total_tokens,
        COALESCE(AVG(ccr.input_tokens), 0)::NUMERIC AS avg_input_tokens,
        COALESCE(AVG(ccr.output_tokens), 0)::NUMERIC AS avg_output_tokens,
        COALESCE(AVG(ccr.processing_time_ms), 0)::NUMERIC AS avg_processing_time_ms,
        COALESCE(COUNT(*) FILTER (WHERE ccr.status = 'completed'), 0)::BIGINT AS completed_requests,
        COALESCE(COUNT(*) FILTER (WHERE ccr.status = 'failed'), 0)::BIGINT AS failed_requests,
        CASE
            WHEN COUNT(*) > 0 THEN
                ROUND(
                    COUNT(*) FILTER (WHERE ccr.status = 'completed')::NUMERIC /
                    COUNT(*)::NUMERIC * 100,
                    2
                )
            ELSE 0
        END AS success_rate,
        MIN(ccr.created_at) AS first_request_at,
        MAX(ccr.created_at) AS last_request_at,
        COALESCE(SUM(ccr.cost_usd), 0)::NUMERIC AS total_cost_usd
    FROM chat_completion_requests ccr
    INNER JOIN models m ON ccr.model_id = m.id
    INNER JOIN providers p ON m.provider_id = p.id
    WHERE
        (p_model_id IS NULL OR ccr.model_id = p_model_id)
        AND (p_provider_id IS NULL OR m.provider_id = p_provider_id)
        AND (p_model_name IS NULL OR m.model_name ILIKE '%' || p_model_name || '%')
        AND (p_start_date IS NULL OR ccr.created_at >= p_start_date)
        AND (p_end_date IS NULL OR ccr.created_at <= p_end_date);
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION get_chat_completion_summary_by_filters(INTEGER, INTEGER, TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO authenticated;
GRANT EXECUTE ON FUNCTION get_chat_completion_summary_by_filters(INTEGER, INTEGER, TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO anon;

-- Add comment
COMMENT ON FUNCTION get_chat_completion_summary_by_filters(INTEGER, INTEGER, TEXT, TIMESTAMPTZ, TIMESTAMPTZ) IS
'Get comprehensive aggregated summary statistics for chat completion requests with flexible filtering.
Supports filtering by model_id, provider_id, model_name (partial match), start_date, and end_date.
Returns detailed metrics including token usage, processing time, success rates, cost tracking, and time ranges.
Optimized for analytics dashboards - performs database-side aggregation without fetching records.
Used by: /admin/monitoring/chat-requests/summary endpoint';
