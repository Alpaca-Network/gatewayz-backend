-- Migration: pin vendor-native model ids to their canonical catalog ids.
--
-- Vendor-native ids are what SDKs actually send: the Anthropic SDK has no
-- `vendor/` prefix, so /v1/messages callers send `claude-sonnet-4-6`. Until
-- 2026-09-08 those were rejected with 503 "contact support" while the
-- prefixed id served fine.
--
-- resolve_catalog_model_id() (src/services/model_resolution.py) would already
-- find these by unique suffix. Pinning them means a future re-host entry
-- sharing the same bare name cannot silently flip a live partner integration
-- from working to 400 model_ambiguous.
--
-- Every canonical_id below was verified present in GET /v1/models on
-- 2026-09-08. `claude-sonnet-4-5` is deliberately ABSENT: the catalog carries
-- it only as `anthropic/claude-sonnet-4-5-20250929`, so the bare form has no
-- unambiguous target and must keep returning 400 model_not_found.
--
-- Alias keys are lowercase — load_alias_map() lowercases on read.

INSERT INTO model_aliases (alias, canonical_id)
VALUES
    ('claude-sonnet-4-6',         'anthropic/claude-sonnet-4-6'),
    ('claude-sonnet-5',           'anthropic/claude-sonnet-5'),
    ('claude-opus-5',             'anthropic/claude-opus-5'),
    ('claude-opus-4-6',           'anthropic/claude-opus-4-6'),
    ('claude-haiku-4-5-20251001', 'anthropic/claude-haiku-4-5-20251001'),
    ('claude-sonnet-4-5-20250929','anthropic/claude-sonnet-4-5-20250929'),
    ('gpt-4o-mini',               'openai/gpt-4o-mini'),
    ('gpt-5',                     'openai/gpt-5')
ON CONFLICT (alias) DO UPDATE
    SET canonical_id = EXCLUDED.canonical_id,
        updated_at   = NOW();
