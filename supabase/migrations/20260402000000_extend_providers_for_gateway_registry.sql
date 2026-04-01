-- Migration: Extend providers for gateway registry (Phase 2A)
-- Created: 2026-04-02
-- Description: Inserts 5 missing providers, updates metadata with timeout/fetch config,
--              and populates logo_url for all 33 gateway slugs.

-- ============================================================================
-- Part 1: Insert 5 missing providers (added after Jan 15 migration)
-- ============================================================================

INSERT INTO "public"."providers" (
    "name",
    "slug",
    "description",
    "api_key_env_var",
    "supports_streaming",
    "is_active",
    "site_url",
    "metadata"
) VALUES
    ('Z.AI', 'zai', 'Z.AI - slow priority gateway', 'ZAI_API_KEY', true, true, 'https://z.ai', '{"color": "bg-purple-700", "priority": "slow"}'::jsonb),
    ('Sybil', 'sybil', 'Sybil - slow priority gateway', 'SYBIL_API_KEY', true, true, 'https://sybil.com', '{"color": "bg-purple-500", "priority": "slow"}'::jsonb),
    ('Morpheus', 'morpheus', 'Morpheus - slow priority gateway', 'MORPHEUS_API_KEY', true, true, 'https://mor.org', '{"color": "bg-cyan-600", "priority": "slow"}'::jsonb),
    ('Canopy Wave', 'canopywave', 'Canopy Wave - slow priority gateway', 'CANOPYWAVE_API_KEY', true, true, 'https://canopywave.io', '{"color": "bg-teal-500", "priority": "slow"}'::jsonb),
    ('NotDiamond', 'notdiamond', 'NotDiamond - fast priority gateway', 'NOTDIAMOND_API_KEY', true, true, 'https://notdiamond.ai', '{"color": "bg-violet-500", "priority": "fast", "icon": "zap"}'::jsonb)

ON CONFLICT (slug)
DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    api_key_env_var = EXCLUDED.api_key_env_var,
    supports_streaming = EXCLUDED.supports_streaming,
    is_active = EXCLUDED.is_active,
    site_url = EXCLUDED.site_url,
    metadata = EXCLUDED.metadata,
    updated_at = now();


-- ============================================================================
-- Part 2: Update metadata JSONB for all 33 providers
--         Merges timeout, has_fetch_function, and fetch_slug_override
--         into existing metadata (preserves color/priority/icon/aliases)
-- ============================================================================

-- Providers with timeout=60 and has_fetch_function=true
UPDATE "public"."providers" SET metadata = metadata || '{"timeout": 60, "has_fetch_function": true}'::jsonb, updated_at = now()
WHERE slug IN ('featherless', 'chutes', 'huggingface', 'aimo', 'near', 'canopywave');

-- Providers with timeout=30 and has_fetch_function=true (27 standard providers)
UPDATE "public"."providers" SET metadata = metadata || '{"timeout": 30, "has_fetch_function": true}'::jsonb, updated_at = now()
WHERE slug IN (
    'openai', 'anthropic', 'openrouter', 'groq', 'together', 'fireworks',
    'vercel-ai-gateway', 'deepinfra', 'google-vertex', 'cerebras', 'nebius',
    'xai', 'novita', 'fal', 'helicone', 'alibaba', 'clarifai', 'onerouter',
    'zai', 'simplismart', 'sybil', 'aihubmix', 'anannas',
    'cloudflare-workers-ai', 'morpheus'
);

-- Providers with timeout=30 and has_fetch_function=false
UPDATE "public"."providers" SET metadata = metadata || '{"timeout": 30, "has_fetch_function": false}'::jsonb, updated_at = now()
WHERE slug IN ('notdiamond', 'alpaca');

-- HuggingFace gets a fetch_slug_override
UPDATE "public"."providers" SET metadata = metadata || '{"fetch_slug_override": "hug"}'::jsonb, updated_at = now()
WHERE slug = 'huggingface';


-- ============================================================================
-- Part 3: Populate logo_url for all 33 gateway slugs
--         Only sets logo_url if it is currently NULL or empty
-- ============================================================================

-- CDN-hosted SVG logos (simple-icons)
UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/openai.svg', updated_at = now()
WHERE slug = 'openai' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/anthropic.svg', updated_at = now()
WHERE slug = 'anthropic' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/vercel.svg', updated_at = now()
WHERE slug = 'vercel-ai-gateway' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/google.svg', updated_at = now()
WHERE slug = 'google-vertex' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/huggingface.svg', updated_at = now()
WHERE slug = 'huggingface' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/alibabacloud.svg', updated_at = now()
WHERE slug = 'alibaba' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/cloudflare.svg', updated_at = now()
WHERE slug = 'cloudflare-workers-ai' AND (logo_url IS NULL OR logo_url = '');

-- Favicon-based logos
UPDATE "public"."providers" SET logo_url = 'https://openrouter.ai/favicon.ico', updated_at = now()
WHERE slug = 'openrouter' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://groq.com/favicon.ico', updated_at = now()
WHERE slug = 'groq' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://together.ai/favicon.ico', updated_at = now()
WHERE slug = 'together' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://fireworks.ai/favicon.ico', updated_at = now()
WHERE slug = 'fireworks' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://featherless.ai/favicon.ico', updated_at = now()
WHERE slug = 'featherless' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://chutes.ai/favicon.ico', updated_at = now()
WHERE slug = 'chutes' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://deepinfra.com/favicon.ico', updated_at = now()
WHERE slug = 'deepinfra' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://cerebras.ai/favicon.ico', updated_at = now()
WHERE slug = 'cerebras' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://nebius.ai/favicon.ico', updated_at = now()
WHERE slug = 'nebius' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://x.ai/favicon.ico', updated_at = now()
WHERE slug = 'xai' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://novita.ai/favicon.ico', updated_at = now()
WHERE slug = 'novita' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://aimo.network/favicon.ico', updated_at = now()
WHERE slug = 'aimo' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://near.ai/favicon.ico', updated_at = now()
WHERE slug = 'near' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://fal.ai/favicon.ico', updated_at = now()
WHERE slug = 'fal' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://www.helicone.ai/favicon.ico', updated_at = now()
WHERE slug = 'helicone' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://alpaca.network/favicon.ico', updated_at = now()
WHERE slug = 'alpaca' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://clarifai.com/favicon.ico', updated_at = now()
WHERE slug = 'clarifai' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://infron.ai/favicon.ico', updated_at = now()
WHERE slug = 'onerouter' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://z.ai/favicon.ico', updated_at = now()
WHERE slug = 'zai' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://simplismart.ai/favicon.ico', updated_at = now()
WHERE slug = 'simplismart' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://sybil.com/favicon.ico', updated_at = now()
WHERE slug = 'sybil' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://aihubmix.com/favicon.ico', updated_at = now()
WHERE slug = 'aihubmix' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://api.anannas.ai/favicon.ico', updated_at = now()
WHERE slug = 'anannas' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://mor.org/favicon.ico', updated_at = now()
WHERE slug = 'morpheus' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://canopywave.io/favicon.ico', updated_at = now()
WHERE slug = 'canopywave' AND (logo_url IS NULL OR logo_url = '');

UPDATE "public"."providers" SET logo_url = 'https://notdiamond.ai/favicon.ico', updated_at = now()
WHERE slug = 'notdiamond' AND (logo_url IS NULL OR logo_url = '');


-- Add comment explaining this migration
COMMENT ON TABLE "public"."providers" IS 'Provider configurations - extended with timeout, fetch config, and logo URLs (Phase 2A)';
