-- Migration: Create unique_models table with automatic sync triggers
-- Description: Maintains a deduplicated list of model names from models
-- Auto-updates via triggers on INSERT/UPDATE/DELETE

-- ============================================================================
-- Step 1: Create the unique_models table
-- ============================================================================

CREATE TABLE IF NOT EXISTS unique_models (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL UNIQUE,
    model_count INTEGER DEFAULT 1,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ DEFAULT NOW(),
    sample_model_id TEXT, -- Store one example model_id for reference
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Add indexes for performance
CREATE INDEX idx_unique_models_name ON unique_models(model_name);
CREATE INDEX idx_unique_models_updated ON unique_models(last_updated_at DESC);

-- Add comments for documentation
COMMENT ON TABLE unique_models IS 'Automatically maintained table of unique model names from models';
COMMENT ON COLUMN unique_models.model_name IS 'Unique model name (cleaned format)';
COMMENT ON COLUMN unique_models.model_count IS 'Number of entries in models with this name';
COMMENT ON COLUMN unique_models.sample_model_id IS 'Example model_id from models for reference';

-- ============================================================================
-- Step 2: Create trigger function to maintain unique_models
-- ============================================================================

CREATE OR REPLACE FUNCTION sync_unique_models()
RETURNS TRIGGER AS $$
BEGIN
    -- Handle INSERT or UPDATE
    IF (TG_OP = 'INSERT' OR TG_OP = 'UPDATE') THEN
        -- Upsert the new model_name
        INSERT INTO unique_models (model_name, model_count, sample_model_id, last_updated_at)
        VALUES (
            NEW.model_name,
            1,
            NEW.model_id,
            NOW()
        )
        ON CONFLICT (model_name)
        DO UPDATE SET
            model_count = (
                SELECT COUNT(*)
                FROM models
                WHERE model_name = NEW.model_name
            ),
            last_updated_at = NOW(),
            sample_model_id = COALESCE(unique_models.sample_model_id, NEW.model_id);

        -- If UPDATE and model_name changed, update the old name's count
        IF (TG_OP = 'UPDATE' AND OLD.model_name IS DISTINCT FROM NEW.model_name) THEN
            -- Update count for old model_name
            UPDATE unique_models
            SET
                model_count = (
                    SELECT COUNT(*)
                    FROM models
                    WHERE model_name = OLD.model_name
                ),
                last_updated_at = NOW()
            WHERE model_name = OLD.model_name;

            -- Remove old model_name if count is 0
            DELETE FROM unique_models
            WHERE model_name = OLD.model_name AND model_count = 0;
        END IF;
    END IF;

    -- Handle DELETE
    IF (TG_OP = 'DELETE') THEN
        -- Decrement count or remove if last instance
        WITH updated AS (
            UPDATE unique_models
            SET
                model_count = (
                    SELECT COUNT(*)
                    FROM models
                    WHERE model_name = OLD.model_name
                ),
                last_updated_at = NOW()
            WHERE model_name = OLD.model_name
            RETURNING model_name, model_count
        )
        DELETE FROM unique_models
        WHERE model_name IN (
            SELECT model_name FROM updated WHERE model_count = 0
        );
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Step 3: Create triggers on models
-- ============================================================================

-- Drop existing triggers if they exist (for re-running migration)
DROP TRIGGER IF EXISTS trg_sync_unique_models_insert ON models;
DROP TRIGGER IF EXISTS trg_sync_unique_models_update ON models;
DROP TRIGGER IF EXISTS trg_sync_unique_models_delete ON models;

-- Create INSERT trigger
CREATE TRIGGER trg_sync_unique_models_insert
    AFTER INSERT ON models
    FOR EACH ROW
    EXECUTE FUNCTION sync_unique_models();

-- Create UPDATE trigger (only fires when model_name changes)
CREATE TRIGGER trg_sync_unique_models_update
    AFTER UPDATE OF model_name ON models
    FOR EACH ROW
    WHEN (OLD.model_name IS DISTINCT FROM NEW.model_name)
    EXECUTE FUNCTION sync_unique_models();

-- Create DELETE trigger
CREATE TRIGGER trg_sync_unique_models_delete
    AFTER DELETE ON models
    FOR EACH ROW
    EXECUTE FUNCTION sync_unique_models();

-- ============================================================================
-- Step 4: Initial population from existing data
-- ============================================================================

INSERT INTO unique_models (model_name, model_count, sample_model_id, first_seen_at, last_updated_at)
SELECT
    model_name,
    COUNT(*) as model_count,
    MIN(model_id) as sample_model_id,
    MIN(created_at) as first_seen_at,
    MAX(updated_at) as last_updated_at
FROM models
WHERE model_name IS NOT NULL AND model_name != ''
GROUP BY model_name
ON CONFLICT (model_name) DO NOTHING;

-- ============================================================================
-- Step 5: Create helper views and functions
-- ============================================================================

-- View to see unique models with their provider counts
CREATE OR REPLACE VIEW unique_models_summary AS
SELECT
    um.id,
    um.model_name,
    um.model_count,
    um.first_seen_at,
    um.last_updated_at,
    um.sample_model_id,
    COUNT(DISTINCT m.provider_id) as provider_count,
    ARRAY_AGG(DISTINCT m.provider_id ORDER BY m.provider_id) as provider_ids
FROM unique_models um
LEFT JOIN models m ON m.model_name = um.model_name
GROUP BY um.id, um.model_name, um.model_count, um.first_seen_at, um.last_updated_at, um.sample_model_id
ORDER BY um.model_count DESC, um.model_name;

COMMENT ON VIEW unique_models_summary IS 'Enhanced view of unique models with provider information';

-- Function to manually refresh all counts (useful for debugging)
CREATE OR REPLACE FUNCTION refresh_unique_models_counts()
RETURNS void AS $$
BEGIN
    UPDATE unique_models um
    SET
        model_count = (
            SELECT COUNT(*)
            FROM models m
            WHERE m.model_name = um.model_name
        ),
        last_updated_at = NOW();

    -- Remove any orphaned entries
    DELETE FROM unique_models
    WHERE model_count = 0;

    RAISE NOTICE 'Unique models counts refreshed successfully';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_unique_models_counts() IS 'Manually refresh all model_count values in unique_models table';

-- ============================================================================
-- Verification queries (commented out for production)
-- ============================================================================

-- SELECT COUNT(*) as total_unique_models FROM unique_models;
-- SELECT COUNT(*) as total_models FROM models;
-- SELECT * FROM unique_models_summary LIMIT 10;
