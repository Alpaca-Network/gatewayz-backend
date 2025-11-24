-- Migration: Add token tracking to model_health_tracking
-- Description: Add input_tokens, output_tokens, total_tokens to track token usage
-- Created: 2025-11-24

-- Add token tracking columns
ALTER TABLE model_health_tracking
ADD COLUMN IF NOT EXISTS input_tokens INTEGER,
ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
ADD COLUMN IF NOT EXISTS total_tokens INTEGER;

-- Add column comments
COMMENT ON COLUMN model_health_tracking.input_tokens IS 'Number of input tokens in the last call';
COMMENT ON COLUMN model_health_tracking.output_tokens IS 'Number of output tokens in the last call';
COMMENT ON COLUMN model_health_tracking.total_tokens IS 'Total tokens (input + output) in the last call';
