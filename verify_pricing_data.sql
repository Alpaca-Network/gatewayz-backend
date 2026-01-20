-- 1. Check if cost columns exist
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'chat_completion_requests'
AND column_name IN ('cost_usd', 'input_cost_usd', 'output_cost_usd', 'pricing_source')
ORDER BY column_name;

-- 2. Check recent requests with cost data (last 10)
SELECT 
    id,
    created_at,
    model_id,
    input_tokens,
    output_tokens,
    cost_usd,
    input_cost_usd,
    output_cost_usd,
    pricing_source,
    status
FROM chat_completion_requests
WHERE cost_usd IS NOT NULL
ORDER BY created_at DESC
LIMIT 10;

-- 3. Get summary statistics
SELECT 
    COUNT(*) as total_requests,
    COUNT(cost_usd) as requests_with_cost,
    COUNT(*) - COUNT(cost_usd) as requests_without_cost,
    ROUND(COUNT(cost_usd)::numeric / NULLIF(COUNT(*), 0) * 100, 2) as percentage_with_cost,
    SUM(cost_usd) as total_cost_usd,
    AVG(cost_usd) as avg_cost_per_request,
    MIN(cost_usd) as min_cost,
    MAX(cost_usd) as max_cost,
    COUNT(DISTINCT pricing_source) as distinct_pricing_sources
FROM chat_completion_requests;

-- 4. Check cost by pricing_source
SELECT 
    pricing_source,
    COUNT(*) as request_count,
    SUM(cost_usd) as total_cost,
    AVG(cost_usd) as avg_cost,
    MIN(created_at) as first_request,
    MAX(created_at) as last_request
FROM chat_completion_requests
WHERE cost_usd IS NOT NULL
GROUP BY pricing_source
ORDER BY request_count DESC;

-- 5. Check if model_usage_analytics view exists and has data
SELECT 
    model_name,
    provider_slug,
    successful_requests,
    total_cost_usd,
    input_cost_usd,
    output_cost_usd,
    avg_cost_per_request_usd
FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;
