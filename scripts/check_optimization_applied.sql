-- ============================================================================
-- Check if models table optimization indexes have been applied
-- Run this in your Supabase SQL Editor to see the status
-- https://supabase.com/dashboard/project/YOUR-PROJECT/sql
-- ============================================================================

-- 1. Check table size and row count
SELECT
  'Table Size' as check_type,
  pg_size_pretty(pg_total_relation_size('public.models')) AS total_size,
  pg_size_pretty(pg_relation_size('public.models')) AS table_size,
  pg_size_pretty(pg_total_relation_size('public.models') - pg_relation_size('public.models')) AS indexes_size,
  (SELECT count(*) FROM models) as row_count;

-- 2. Check if pg_trgm extension is installed (required for optimization)
SELECT
  'Extension Status' as check_type,
  extname as extension_name,
  extversion as version,
  CASE WHEN extname = 'pg_trgm' THEN '✅ Installed' ELSE '❌ Not Installed' END as status
FROM pg_extension
WHERE extname = 'pg_trgm'
UNION ALL
SELECT
  'Extension Status',
  'pg_trgm',
  NULL,
  '❌ NOT INSTALLED - Optimization migration NOT applied'
WHERE NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm');

-- 3. Check for search_vector column (added by optimization migration)
SELECT
  'Search Vector Column' as check_type,
  column_name,
  data_type,
  '✅ Column exists - optimization likely applied' as status
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'models'
  AND column_name = 'search_vector'
UNION ALL
SELECT
  'Search Vector Column',
  'search_vector',
  NULL,
  '❌ Column missing - optimization NOT applied'
WHERE NOT EXISTS (
  SELECT 1 FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'models'
    AND column_name = 'search_vector'
);

-- 4. List all indexes on models table
SELECT
  'Index List' as check_type,
  indexname as index_name,
  pg_size_pretty(pg_relation_size(indexrelid)) as index_size,
  CASE
    WHEN indexname LIKE '%active%' OR
         indexname LIKE '%trgm%' OR
         indexname LIKE '%covering%' OR
         indexname LIKE '%search_vector%' THEN '✅ Optimization index'
    ELSE '📋 Standard index'
  END as index_type
FROM pg_stat_user_indexes
WHERE schemaname = 'public' AND tablename = 'models'
ORDER BY indexname;

-- 5. Check for specific optimization indexes
WITH expected_indexes AS (
  SELECT unnest(ARRAY[
    'idx_models_active_name',
    'idx_models_active_provider',
    'idx_models_active_modality',
    'idx_models_catalog_covering',
    'idx_models_search_covering',
    'idx_models_model_id_trgm',
    'idx_models_model_name_trgm',
    'idx_models_by_price',
    'idx_models_by_context',
    'idx_models_provider_join',
    'idx_models_search_vector'
  ]) as expected_index_name
),
existing_indexes AS (
  SELECT indexname
  FROM pg_indexes
  WHERE tablename = 'models' AND schemaname = 'public'
)
SELECT
  'Optimization Index Status' as check_type,
  ei.expected_index_name as index_name,
  CASE
    WHEN EXISTS (SELECT 1 FROM existing_indexes WHERE indexname = ei.expected_index_name)
    THEN '✅ Applied'
    ELSE '❌ Missing'
  END as status
FROM expected_indexes ei
ORDER BY status DESC, index_name;

-- 6. Summary
WITH index_check AS (
  SELECT
    COUNT(*) FILTER (WHERE indexname IN (
      'idx_models_active_name',
      'idx_models_active_provider',
      'idx_models_active_modality',
      'idx_models_catalog_covering',
      'idx_models_search_covering',
      'idx_models_model_id_trgm',
      'idx_models_model_name_trgm',
      'idx_models_by_price',
      'idx_models_by_context',
      'idx_models_provider_join',
      'idx_models_search_vector'
    )) as applied_count,
    11 as expected_count
  FROM pg_indexes
  WHERE tablename = 'models' AND schemaname = 'public'
)
SELECT
  '📊 SUMMARY' as check_type,
  applied_count || ' / ' || expected_count as optimization_indexes,
  CASE
    WHEN applied_count = expected_count THEN '✅ ALL optimization indexes applied!'
    WHEN applied_count > 0 THEN '⚠️  PARTIAL - Some indexes missing'
    ELSE '❌ NOT APPLIED - Run migration to optimize'
  END as overall_status,
  CASE
    WHEN applied_count = expected_count THEN 'Your models table is fully optimized!'
    ELSE 'Apply the migration: supabase db push'
  END as recommendation
FROM index_check;
