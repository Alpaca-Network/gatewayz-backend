-- Add the foreign key unique_models_provider.provider_id -> providers.id
--
-- 20260129000001_create_unique_models_provider_table.sql declared foreign keys
-- for unique_model_id and model_id but left provider_id as a bare BIGINT with
-- only an index. Without the constraint PostgREST has no relationship to follow,
-- so `providers!inner(...)` embeds fail with PGRST200 and the unique-models
-- catalog silently returned nothing.
--
-- The read path no longer depends on the embed (it joins providers in Python),
-- but the constraint is still the correct data model: it stops orphan mappings
-- and restores the embed for any future caller.

-- Drop mappings pointing at providers that no longer exist, otherwise ADD
-- CONSTRAINT fails. These rows are already unusable — the catalog skips them.
DELETE FROM unique_models_provider ump
WHERE NOT EXISTS (
    SELECT 1 FROM providers p WHERE p.id = ump.provider_id
);

ALTER TABLE unique_models_provider
    DROP CONSTRAINT IF EXISTS fk_unique_models_provider_provider;

ALTER TABLE unique_models_provider
    ADD CONSTRAINT fk_unique_models_provider_provider
    FOREIGN KEY (provider_id)
    REFERENCES providers(id)
    ON DELETE CASCADE;

COMMENT ON CONSTRAINT fk_unique_models_provider_provider ON unique_models_provider
    IS 'Restores PostgREST embedding of providers and prevents orphan provider mappings.';

-- PostgREST caches the schema; without this the new relationship stays invisible
-- until the next restart.
NOTIFY pgrst, 'reload schema';
