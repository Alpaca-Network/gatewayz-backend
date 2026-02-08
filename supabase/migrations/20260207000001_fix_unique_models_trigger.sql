-- Migration: Fix sync_unique_models trigger to reference model_name instead of model_id
-- Description: model_id was dropped in migration 20260131000002. This fix updates the trigger to use provider_model_id as the sample.

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
            NEW.provider_model_id, -- Use provider_model_id since model_id doesn't exist anymore
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
            sample_model_id = COALESCE(unique_models.sample_model_id, NEW.provider_model_id);

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

-- Populating missing entries if any were missed during the error period
INSERT INTO unique_models (model_name, model_count, sample_model_id, first_seen_at, last_updated_at)
SELECT
    model_name,
    COUNT(*) as model_count,
    MIN(provider_model_id) as sample_model_id,
    MIN(created_at) as first_seen_at,
    MAX(updated_at) as last_updated_at
FROM models
WHERE model_name IS NOT NULL AND model_name != ''
GROUP BY model_name
ON CONFLICT (model_name) DO UPDATE SET
    model_count = EXCLUDED.model_count,
    last_updated_at = EXCLUDED.last_updated_at;

DO $$
BEGIN
    RAISE NOTICE '✅ Migration completed: Fixed sync_unique_models function to use provider_model_id';
END $$;
