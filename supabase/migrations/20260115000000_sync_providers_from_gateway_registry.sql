-- Migration: Sync all providers from GATEWAY_REGISTRY
-- Created: 2026-01-15
-- Description: Updates providers table with all gateways from src/routes/catalog.py::GATEWAY_REGISTRY
-- This migration ensures providers table stays in sync with code

-- Upsert all 28 providers from GATEWAY_REGISTRY
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
    -- Fast priority gateways
    ('OpenAI', 'openai', 'OpenAI - fast priority gateway', 'OPENAI_API_KEY', true, true, 'https://openai.com', '{"color": "bg-emerald-600", "priority": "fast"}'::jsonb),
    ('Anthropic', 'anthropic', 'Anthropic - fast priority gateway', 'ANTHROPIC_API_KEY', true, true, 'https://anthropic.com', '{"color": "bg-amber-700", "priority": "fast"}'::jsonb),
    ('OpenRouter', 'openrouter', 'OpenRouter - fast priority gateway', 'OPENROUTER_API_KEY', true, true, 'https://openrouter.ai', '{"color": "bg-blue-500", "priority": "fast"}'::jsonb),
    ('Groq', 'groq', 'Groq - fast priority gateway', 'GROQ_API_KEY', true, true, 'https://groq.com', '{"color": "bg-orange-500", "priority": "fast", "icon": "zap"}'::jsonb),
    ('Together', 'together', 'Together - fast priority gateway', 'TOGETHER_API_KEY', true, true, 'https://together.ai', '{"color": "bg-indigo-500", "priority": "fast"}'::jsonb),
    ('Fireworks', 'fireworks', 'Fireworks - fast priority gateway', 'FIREWORKS_API_KEY', true, true, 'https://fireworks.ai', '{"color": "bg-red-500", "priority": "fast"}'::jsonb),
    ('Vercel AI', 'vercel-ai-gateway', 'Vercel AI - fast priority gateway', 'VERCEL_AI_GATEWAY_KEY', true, true, 'https://vercel.com/ai', '{"color": "bg-slate-900", "priority": "fast"}'::jsonb),

    -- Slow priority gateways
    ('Featherless', 'featherless', 'Featherless - slow priority gateway', 'FEATHERLESS_API_KEY', true, true, 'https://featherless.ai', '{"color": "bg-green-500", "priority": "slow"}'::jsonb),
    ('Chutes', 'chutes', 'Chutes - slow priority gateway', 'CHUTES_API_KEY', true, true, 'https://chutes.ai', '{"color": "bg-yellow-500", "priority": "slow"}'::jsonb),
    ('DeepInfra', 'deepinfra', 'DeepInfra - slow priority gateway', 'DEEPINFRA_API_KEY', true, true, 'https://deepinfra.com', '{"color": "bg-cyan-500", "priority": "slow"}'::jsonb),
    ('Google', 'google-vertex', 'Google - slow priority gateway', 'GOOGLE_APPLICATION_CREDENTIALS', true, true, 'https://cloud.google.com/vertex-ai', '{"color": "bg-blue-600", "priority": "slow", "aliases": ["google"]}'::jsonb),
    ('Cerebras', 'cerebras', 'Cerebras - slow priority gateway', 'CEREBRAS_API_KEY', true, true, 'https://cerebras.ai', '{"color": "bg-amber-600", "priority": "slow"}'::jsonb),
    ('Nebius', 'nebius', 'Nebius - slow priority gateway', 'NEBIUS_API_KEY', true, true, 'https://nebius.ai', '{"color": "bg-slate-600", "priority": "slow"}'::jsonb),
    ('xAI', 'xai', 'xAI - slow priority gateway', 'XAI_API_KEY', true, true, 'https://x.ai', '{"color": "bg-black", "priority": "slow"}'::jsonb),
    ('Novita', 'novita', 'Novita - slow priority gateway', 'NOVITA_API_KEY', true, true, 'https://novita.ai', '{"color": "bg-violet-600", "priority": "slow"}'::jsonb),
    ('Hugging Face', 'huggingface', 'Hugging Face - slow priority gateway', 'HUGGINGFACE_API_KEY', true, true, 'https://huggingface.co', '{"color": "bg-yellow-600", "priority": "slow", "aliases": ["hug"]}'::jsonb),
    ('AiMo', 'aimo', 'AiMo - slow priority gateway', 'AIMO_API_KEY', true, true, 'https://aimo.network', '{"color": "bg-pink-600", "priority": "slow"}'::jsonb),
    ('NEAR', 'near', 'NEAR - slow priority gateway', 'NEAR_API_KEY', true, true, 'https://near.ai', '{"color": "bg-teal-600", "priority": "slow"}'::jsonb),
    ('Fal', 'fal', 'Fal - slow priority gateway', 'FAL_API_KEY', false, true, 'https://fal.ai', '{"color": "bg-emerald-600", "priority": "slow"}'::jsonb),
    ('Helicone', 'helicone', 'Helicone - slow priority gateway', 'HELICONE_API_KEY', true, true, 'https://helicone.ai', '{"color": "bg-indigo-600", "priority": "slow"}'::jsonb),
    ('Alpaca Network', 'alpaca', 'Alpaca Network - slow priority gateway', 'ALPACA_NETWORK_API_KEY', true, true, 'https://alpaca.network', '{"color": "bg-green-700", "priority": "slow"}'::jsonb),
    ('Alibaba', 'alibaba', 'Alibaba - slow priority gateway', 'ALIBABA_CLOUD_API_KEY', true, true, 'https://dashscope.aliyun.com', '{"color": "bg-orange-700", "priority": "slow"}'::jsonb),
    ('Clarifai', 'clarifai', 'Clarifai - slow priority gateway', 'CLARIFAI_API_KEY', true, true, 'https://clarifai.com', '{"color": "bg-purple-600", "priority": "slow"}'::jsonb),
    ('OneRouter', 'onerouter', 'OneRouter - slow priority gateway', 'ONEROUTER_API_KEY', true, true, 'https://onerouter.pro', '{"color": "bg-emerald-500", "priority": "slow"}'::jsonb),
    ('SimpliSmart', 'simplismart', 'SimpliSmart - slow priority gateway', 'SIMPLISMART_API_KEY', true, true, 'https://simplismart.ai', '{"color": "bg-sky-500", "priority": "slow"}'::jsonb),
    ('AiHubMix', 'aihubmix', 'AiHubMix - slow priority gateway', 'AIHUBMIX_API_KEY', true, true, 'https://aihubmix.com', '{"color": "bg-rose-500", "priority": "slow"}'::jsonb),
    ('Anannas', 'anannas', 'Anannas - slow priority gateway', 'ANANNAS_API_KEY', true, true, 'https://anannas.ai', '{"color": "bg-lime-600", "priority": "slow"}'::jsonb),
    ('Cloudflare Workers AI', 'cloudflare-workers-ai', 'Cloudflare Workers AI - slow priority gateway', 'CLOUDFLARE_WORKERS_AI_API_KEY', true, true, 'https://developers.cloudflare.com/workers-ai', '{"color": "bg-orange-500", "priority": "slow"}'::jsonb)

-- ON CONFLICT: Update existing providers with latest data
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

-- Add comment explaining this migration
COMMENT ON TABLE "public"."providers" IS 'Provider configurations - synced from GATEWAY_REGISTRY on deploy';
