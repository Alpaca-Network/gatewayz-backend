-- Model Usage Analytics - Example Queries
-- Use these queries to analyze model usage, costs, and performance

-- ============================================================================
-- 1. Get all models with usage and cost breakdown (default view)
-- ============================================================================
SELECT * FROM model_usage_analytics
ORDER BY successful_requests DESC
LIMIT 20;

-- ============================================================================
-- 2. Top 10 most expensive models (by total cost)
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_input_tokens,
    total_output_tokens,
    total_cost_usd,
    avg_cost_per_request_usd
FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;

-- ============================================================================
-- 3. Most popular models (by request count)
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_cost_usd,
    avg_processing_time_ms
FROM model_usage_analytics
ORDER BY successful_requests DESC
LIMIT 10;

-- ============================================================================
-- 4. Cost per request ranking (most expensive per request)
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    avg_cost_per_request_usd,
    avg_input_tokens_per_request,
    avg_output_tokens_per_request
FROM model_usage_analytics
WHERE successful_requests >= 10  -- Only models with significant usage
ORDER BY avg_cost_per_request_usd DESC
LIMIT 20;

-- ============================================================================
-- 5. Models by provider with total costs
-- ============================================================================
SELECT
    provider_name,
    COUNT(DISTINCT model_id) as model_count,
    SUM(successful_requests) as total_requests,
    SUM(total_input_tokens) as total_input_tokens,
    SUM(total_output_tokens) as total_output_tokens,
    ROUND(SUM(total_cost_usd), 2) as total_cost_usd
FROM model_usage_analytics
GROUP BY provider_name
ORDER BY total_cost_usd DESC;

-- ============================================================================
-- 6. Token efficiency analysis (input vs output ratio)
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_input_tokens,
    total_output_tokens,
    ROUND(
        CAST(total_output_tokens AS NUMERIC) / NULLIF(total_input_tokens, 0),
        2
    ) as output_to_input_ratio,
    total_cost_usd
FROM model_usage_analytics
WHERE successful_requests >= 10
ORDER BY output_to_input_ratio DESC
LIMIT 20;

-- ============================================================================
-- 7. Cost breakdown: Input vs Output costs
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    input_cost_usd,
    output_cost_usd,
    total_cost_usd,
    ROUND(
        (output_cost_usd / NULLIF(total_cost_usd, 0)) * 100,
        1
    ) as output_cost_percentage
FROM model_usage_analytics
WHERE total_cost_usd > 0
ORDER BY total_cost_usd DESC
LIMIT 20;

-- ============================================================================
-- 8. Recently active models (last 24 hours)
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_cost_usd,
    last_request_at,
    avg_processing_time_ms
FROM model_usage_analytics
WHERE last_request_at >= NOW() - INTERVAL '24 hours'
ORDER BY last_request_at DESC;

-- ============================================================================
-- 9. Pricing comparison (most expensive vs cheapest models)
-- ============================================================================
-- Most expensive input tokens
SELECT
    'Most Expensive Input' as category,
    model_name,
    provider_name,
    input_token_price_per_1m,
    successful_requests,
    input_cost_usd
FROM model_usage_analytics
WHERE input_token_price_per_1m > 0
ORDER BY input_token_price_per_1m DESC
LIMIT 5;

-- Cheapest input tokens
SELECT
    'Cheapest Input' as category,
    model_name,
    provider_name,
    input_token_price_per_1m,
    successful_requests,
    input_cost_usd
FROM model_usage_analytics
WHERE input_token_price_per_1m > 0
ORDER BY input_token_price_per_1m ASC
LIMIT 5;

-- ============================================================================
-- 10. Performance vs Cost analysis
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    avg_processing_time_ms,
    avg_cost_per_request_usd,
    -- Cost per second (lower is better)
    ROUND(
        (avg_cost_per_request_usd * 1000) / NULLIF(avg_processing_time_ms, 0),
        6
    ) as cost_per_second,
    total_cost_usd
FROM model_usage_analytics
WHERE avg_processing_time_ms > 0 AND successful_requests >= 10
ORDER BY cost_per_second ASC
LIMIT 20;

-- ============================================================================
-- 11. Models with highest total token usage
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_tokens,
    total_input_tokens,
    total_output_tokens,
    total_cost_usd
FROM model_usage_analytics
ORDER BY total_tokens DESC
LIMIT 20;

-- ============================================================================
-- 12. Active models by health status
-- ============================================================================
SELECT
    health_status,
    COUNT(*) as model_count,
    SUM(successful_requests) as total_requests,
    ROUND(SUM(total_cost_usd), 2) as total_cost_usd
FROM model_usage_analytics
GROUP BY health_status
ORDER BY total_requests DESC;

-- ============================================================================
-- 13. Cost efficiency: Models with best cost per 1000 tokens
-- ============================================================================
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_tokens,
    total_cost_usd,
    ROUND(
        (total_cost_usd * 1000) / NULLIF(total_tokens, 0),
        6
    ) as cost_per_1k_tokens
FROM model_usage_analytics
WHERE total_tokens > 0
ORDER BY cost_per_1k_tokens ASC
LIMIT 20;

-- ============================================================================
-- 14. Filter by specific provider
-- ============================================================================
SELECT * FROM model_usage_analytics
WHERE provider_slug = 'openrouter'  -- Change to desired provider
ORDER BY total_cost_usd DESC;

-- ============================================================================
-- 15. Models with context length > 100k tokens
-- ============================================================================
SELECT
    model_name,
    provider_name,
    context_length,
    successful_requests,
    avg_input_tokens_per_request,
    total_cost_usd
FROM model_usage_analytics
WHERE context_length >= 100000
ORDER BY successful_requests DESC;
