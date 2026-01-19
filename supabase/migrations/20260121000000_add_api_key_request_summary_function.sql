-- Migration: Add function to calculate chat completion request summary statistics for API keys
-- This function performs database-side aggregation for accurate totals across all requests
-- without the need to fetch all records (much faster and more efficient)

-- Drop function if it exists
DROP FUNCTION IF EXISTS get_api_key_request_summary(INTEGER);

-- Create function to get aggregated summary statistics for an API key
CREATE OR REPLACE FUNCTION get_api_key_request_summary(p_api_key_id INTEGER)
RETURNS TABLE (
    total_input_tokens BIGINT,
    total_output_tokens BIGINT,
    total_tokens BIGINT,
    avg_processing_time_ms NUMERIC,
    completed_requests BIGINT,
    failed_requests BIGINT
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(ccr.input_tokens), 0)::BIGINT AS total_input_tokens,
        COALESCE(SUM(ccr.output_tokens), 0)::BIGINT AS total_output_tokens,
        COALESCE(SUM(ccr.input_tokens + ccr.output_tokens), 0)::BIGINT AS total_tokens,
        COALESCE(AVG(ccr.processing_time_ms), 0)::NUMERIC AS avg_processing_time_ms,
        COALESCE(COUNT(*) FILTER (WHERE ccr.status = 'completed'), 0)::BIGINT AS completed_requests,
        COALESCE(COUNT(*) FILTER (WHERE ccr.status = 'failed'), 0)::BIGINT AS failed_requests
    FROM chat_completion_requests ccr
    WHERE ccr.api_key_id = p_api_key_id;
END;
$$;

-- Grant execute permission to authenticated users (admin access is checked in application layer)
GRANT EXECUTE ON FUNCTION get_api_key_request_summary(INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION get_api_key_request_summary(INTEGER) TO anon;

-- Add comment
COMMENT ON FUNCTION get_api_key_request_summary(INTEGER) IS
'Calculate aggregated summary statistics for all chat completion requests for a given API key.
Returns total tokens, average processing time, and request counts without fetching all records.
Used by admin monitoring endpoint for accurate statistics across large datasets.';
