-- Add content_hash column to models table for efficient change detection
-- Issue #1116: Eliminate 17k-row fetches by storing pre-computed SHA-256 hashes
--
-- Before: sync fetches 14 fields per row (~50MB for 17k models), computes hashes in Python
-- After:  sync fetches 2 fields (provider_model_id + content_hash, ~1MB), no recomputation

ALTER TABLE models ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- Composite index for efficient hash lookups during sync
-- Covers the query: SELECT provider_model_id, content_hash FROM models WHERE provider_id = ?
CREATE INDEX IF NOT EXISTS idx_models_provider_content_hash
    ON models (provider_id, provider_model_id, content_hash);
