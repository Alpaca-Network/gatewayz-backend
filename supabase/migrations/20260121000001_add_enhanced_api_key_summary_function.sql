-- Migration: Add enhanced function to get comprehensive chat completion summary for API keys
-- This function provides detailed aggregated statistics including cost tracking,
-- success rates, and time ranges for analytics dashboards

-- Drop function if it exists
DROP FUNCTION IF EXISTS get_chat_completion_summary_by_api_key(INTEGER);

-- Create enhanced function to get comprehensive summary statistics
CREATE OR REPLACE FUNCTION get_chat_completion_summary_by_api_key(p_api_key_id INTEGER)
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
    WHERE ccr.api_key_id = p_api_key_id;
END;
$$;

-- Grant execute permission to authenticated users (admin access checked in application layer)
GRANT EXECUTE ON FUNCTION get_chat_completion_summary_by_api_key(INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION get_chat_completion_summary_by_api_key(INTEGER) TO anon;

-- Add comment
COMMENT ON FUNCTION get_chat_completion_summary_by_api_key(INTEGER) IS
'Get comprehensive aggregated summary statistics for all chat completion requests for a given API key.
Returns detailed metrics including token usage, processing time, success rates, cost tracking, and time ranges.
Optimized for analytics dashboards and monitoring - performs database-side aggregation without fetching records.
Used by: /admin/monitoring/chat-requests/by-api-key/summary endpoint';
