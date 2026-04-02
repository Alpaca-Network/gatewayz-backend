-- Add latency_tier, pricing_format, and failover_priority to providers metadata JSONB
-- These fields enable DB-first lookups in request_prioritization, pricing_normalization,
-- and provider_failover (replacing hardcoded dicts).

-- ── latency_tier (from PROVIDER_LATENCY_TIERS in request_prioritization.py) ──
-- Tier 1: <100ms (specialized inference HW), Tier 2: 100-500ms, Tier 3: 500ms+, Tier 4: variable
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"latency_tier": 1}'::jsonb WHERE slug IN ('groq', 'cerebras');
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"latency_tier": 2}'::jsonb WHERE slug IN ('fireworks', 'together', 'cloudflare-workers-ai');
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"latency_tier": 3}'::jsonb WHERE slug IN ('openrouter', 'deepinfra', 'google-vertex');
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"latency_tier": 4}'::jsonb WHERE slug IN ('huggingface', 'featherless', 'near', 'alibaba');

-- ── pricing_format (from PROVIDER_PRICING_FORMATS in pricing_normalization.py) ──
-- Values: "per_token", "per_1k", "per_1m"
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"pricing_format": "per_token"}'::jsonb WHERE slug = 'openrouter';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"pricing_format": "per_1k"}'::jsonb WHERE slug = 'aihubmix';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"pricing_format": "per_1m"}'::jsonb
    WHERE slug IN ('anthropic', 'deepinfra', 'featherless', 'together', 'fireworks', 'near',
                   'groq', 'cerebras', 'xai', 'aimo', 'google-vertex', 'novita', 'nebius',
                   'alibaba', 'morpheus', 'helicone', 'vercel-ai-gateway', 'chutes');

-- ── failover_priority (from FALLBACK_PROVIDER_PRIORITY in provider_failover.py) ──
-- Lower number = tried first during failover
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 1}'::jsonb WHERE slug = 'onerouter';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 2}'::jsonb WHERE slug = 'openai';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 3}'::jsonb WHERE slug = 'anthropic';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 4}'::jsonb WHERE slug = 'google-vertex';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 5}'::jsonb WHERE slug = 'openrouter';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 6}'::jsonb WHERE slug = 'cerebras';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 7}'::jsonb WHERE slug = 'huggingface';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 8}'::jsonb WHERE slug = 'featherless';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 9}'::jsonb WHERE slug = 'vercel-ai-gateway';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 10}'::jsonb WHERE slug = 'aihubmix';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 11}'::jsonb WHERE slug = 'anannas';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 12}'::jsonb WHERE slug = 'alibaba';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 13}'::jsonb WHERE slug = 'fireworks';
UPDATE "public"."providers" SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"failover_priority": 14}'::jsonb WHERE slug = 'together';
