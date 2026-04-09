-- Add columns for tracking stale models during provider sync.
-- When a provider API no longer lists a model, we increment consecutive_missing_count.
-- After 3 consecutive misses, the model is soft-deactivated (is_active = false).

ALTER TABLE models
  ADD COLUMN IF NOT EXISTS last_seen_in_provider_at TIMESTAMPTZ DEFAULT now(),
  ADD COLUMN IF NOT EXISTS consecutive_missing_count INTEGER DEFAULT 0 NOT NULL;

-- Backfill: set last_seen_in_provider_at to updated_at for existing active models
UPDATE models
SET last_seen_in_provider_at = updated_at
WHERE last_seen_in_provider_at IS NULL;

-- Index for efficient stale-model queries (find models with high miss count per provider)
CREATE INDEX IF NOT EXISTS idx_models_stale_tracking
  ON models (provider_id, is_active, consecutive_missing_count)
  WHERE is_active = true;
