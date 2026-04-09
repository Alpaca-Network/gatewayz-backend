-- Migration: Create model_quality_scores table
-- Stores benchmark/manual quality priors per model per task type.
-- Replaces the hardcoded QUALITY_PRIORS dict in model_selector.py.
--
-- source values: 'manual' (hand-coded), 'benchmark' (automated eval), 'inferred' (derived)

CREATE TABLE IF NOT EXISTS model_quality_scores (
    id BIGSERIAL PRIMARY KEY,
    model_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    score NUMERIC(5, 2) NOT NULL CHECK (score >= 0 AND score <= 100),
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_model_task_score UNIQUE (model_id, task_type)
);

CREATE INDEX idx_model_quality_scores_model_id ON model_quality_scores(model_id);
CREATE INDEX idx_model_quality_scores_task_type ON model_quality_scores(task_type);

COMMENT ON TABLE model_quality_scores IS 'Quality priors for model selection, keyed by model_id and task_type';
COMMENT ON COLUMN model_quality_scores.model_id IS 'Canonical model ID, e.g. openai/gpt-4o';
COMMENT ON COLUMN model_quality_scores.task_type IS 'Task category: simple_qa, complex_reasoning, code_generation, etc.';
COMMENT ON COLUMN model_quality_scores.score IS '0-100 quality score for this model on this task';
COMMENT ON COLUMN model_quality_scores.source IS 'manual | benchmark | inferred';
