-- Analyze models table performance
-- Run this in your Supabase SQL editor to identify bottlenecks

-- 1. Check table size and row count
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
  pg_size_pretty(pg_relation_size(schemaname||'.'||tablename)) AS table_size,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename) - pg_relation_size(schemaname||'.'||tablename)) AS indexes_size,
  (SELECT count(*) FROM models) as row_count
FROM pg_tables
WHERE tablename = 'models';

-- 2. Check index usage
SELECT
  schemaname,
  tablename,
  indexname,
  idx_scan as index_scans,
  idx_tup_read as tuples_read,
  idx_tup_fetch as tuples_fetched,
  pg_size_pretty(pg_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'public' AND tablename = 'models'
ORDER BY idx_scan DESC;

-- 3. Find slow queries (if you have pg_stat_statements enabled)
-- This requires the extension: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT
  substring(query, 1, 100) as query_preview,
  calls,
  round(total_exec_time::numeric, 2) as total_time_ms,
  round(mean_exec_time::numeric, 2) as avg_time_ms,
  round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 2) as percent_total
FROM pg_stat_statements
WHERE query LIKE '%models%'
  AND query NOT LIKE '%pg_stat%'
ORDER BY total_exec_time DESC
LIMIT 10;

-- 4. Check for duplicate data (same provider + model combo)
SELECT
  provider_id,
  provider_model_id,
  count(*) as duplicate_count
FROM models
GROUP BY provider_id, provider_model_id
HAVING count(*) > 1
ORDER BY duplicate_count DESC;

-- 5. Analyze common query patterns
-- Test your most common query with EXPLAIN ANALYZE
EXPLAIN (ANALYZE, BUFFERS)
SELECT m.*, p.name as provider_name, p.slug as provider_slug
FROM models m
INNER JOIN providers p ON m.provider_id = p.id
WHERE m.is_active = true
ORDER BY m.model_name
LIMIT 100;

-- 6. Check for missing indexes on foreign keys
SELECT
  tc.table_name,
  kcu.column_name,
  EXISTS(
    SELECT 1 FROM pg_indexes
    WHERE tablename = tc.table_name
    AND indexdef LIKE '%' || kcu.column_name || '%'
  ) as has_index
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_name = 'models';

-- 7. Analyze query plan for search queries
EXPLAIN (ANALYZE, BUFFERS)
SELECT m.*, p.*
FROM models m
INNER JOIN providers p ON m.provider_id = p.id
WHERE m.model_id ILIKE '%gpt%'
  AND m.is_active = true
LIMIT 50;
