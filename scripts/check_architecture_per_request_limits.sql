-- ============================================================================
-- Check if architecture and per_request_limits are redundant
-- ============================================================================

-- 1. Check how many models have non-null architecture
SELECT
    COUNT(*) as total_models,
    COUNT(architecture) as non_null_architecture,
    COUNT(per_request_limits) as non_null_per_request_limits
FROM models;

-- 2. Sample models with architecture
SELECT
    model_name,
    architecture,
    per_request_limits,
    metadata
FROM models
WHERE architecture IS NOT NULL
LIMIT 5;

-- 3. Sample models with per_request_limits
SELECT
    model_name,
    per_request_limits,
    metadata
FROM models
WHERE per_request_limits IS NOT NULL
LIMIT 5;

-- 4. Check if architecture data is in metadata
SELECT
    model_name,
    architecture as architecture_column,
    metadata->'architecture' as architecture_in_metadata,
    metadata
FROM models
WHERE architecture IS NOT NULL
   OR metadata->'architecture' IS NOT NULL
LIMIT 10;

-- 5. Check if per_request_limits data is in metadata
SELECT
    model_name,
    per_request_limits as per_request_limits_column,
    metadata->'per_request_limits' as per_request_limits_in_metadata
FROM models
WHERE per_request_limits IS NOT NULL
   OR metadata->'per_request_limits' IS NOT NULL
LIMIT 10;
