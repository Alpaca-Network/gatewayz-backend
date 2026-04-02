-- Migration: Add model capability columns to models table
-- These columns replace hardcoded Python dicts/constants with DB-driven data
-- that can be updated without code deploys.
--
-- latency_tier values:
--   1 = ultra  (<100ms typical, e.g. Groq, Cerebras)
--   2 = fast   (100-500ms typical, e.g. Fireworks, Together)
--   3 = standard (500ms-2s typical, default for most providers)
--   4 = slow   (>2s typical, e.g. large Vertex AI models)

ALTER TABLE models ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER;
ALTER TABLE models ADD COLUMN IF NOT EXISTS has_json_mode BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE models ADD COLUMN IF NOT EXISTS is_reasoning BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE models ADD COLUMN IF NOT EXISTS is_free BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE models ADD COLUMN IF NOT EXISTS latency_tier SMALLINT NOT NULL DEFAULT 3;

COMMENT ON COLUMN models.max_output_tokens IS 'Maximum number of output tokens the model can generate in a single request';
COMMENT ON COLUMN models.has_json_mode IS 'Model supports constrained JSON output mode';
COMMENT ON COLUMN models.is_reasoning IS 'Model is a reasoning/thinking model (o1, o3, R1, etc.)';
COMMENT ON COLUMN models.is_free IS 'Model is available for free (no credit cost, e.g. OpenRouter :free models)';
COMMENT ON COLUMN models.latency_tier IS '1=ultra(<100ms), 2=fast(<500ms), 3=standard(<2s), 4=slow(>2s)';

CREATE INDEX IF NOT EXISTS idx_models_is_free ON models(is_free) WHERE is_free = true;
CREATE INDEX IF NOT EXISTS idx_models_latency_tier ON models(latency_tier);
CREATE INDEX IF NOT EXISTS idx_models_is_reasoning ON models(is_reasoning) WHERE is_reasoning = true;
