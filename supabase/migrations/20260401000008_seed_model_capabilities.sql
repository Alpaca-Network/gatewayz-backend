-- Seed model capability columns from:
--   src/data/model_capabilities.json (has_json_mode, supports_vision, supports_function_calling)
--   src/services/anonymous_rate_limiter.py ANONYMOUS_ALLOWED_MODELS (is_free)
--   src/services/request_prioritization.py ULTRA_LOW_LATENCY_MODELS + PROVIDER_LATENCY_TIERS (latency_tier)
--   src/services/health_snapshots.py SMALL/MEDIUM_TIER_POOL (latency_tier)
--   src/services/google_models_config.py (max_output_tokens for Gemini)
--   src/services/credit_precheck.py MODEL_MAX_TOKENS (max_output_tokens)
--
-- Uses model_name matching (ILIKE) since provider_model_id varies across providers.
-- Runs idempotently — safe to re-apply.

-- ── has_json_mode (from model_capabilities.json) ──────────────────────────────

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%gpt-4o%'
   OR model_name ILIKE '%gpt-4-turbo%'
   OR model_name ILIKE '%gpt-3.5-turbo%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%claude-3%'
   OR model_name ILIKE '%claude-3.5%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%gemini%flash%'
   OR model_name ILIKE '%gemini%pro%'
   OR model_name ILIKE '%gemini-2%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%deepseek-chat%'
   OR model_name ILIKE '%deepseek-coder%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%mistral-small%'
   OR model_name ILIKE '%mistral-large%'
   OR model_name ILIKE '%codestral%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%llama-3.1%'
   OR model_name ILIKE '%llama-3.2%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%command-r%';

UPDATE models SET has_json_mode = true
WHERE model_name ILIKE '%mixtral%';

-- o1/o3 reasoning models do NOT support json_mode (confirmed in capabilities.json)
UPDATE models SET has_json_mode = false
WHERE model_name ILIKE '%/o1%'
   OR model_name ILIKE '%/o1-%'
   OR model_name ILIKE '%/o3%'
   OR model_name ILIKE '%deepseek-reasoner%';

-- ── is_reasoning (reasoning/thinking models) ─────────────────────────────────

UPDATE models SET is_reasoning = true
WHERE model_name ILIKE '%/o1%'
   OR model_name ILIKE '%/o1-%'
   OR model_name ILIKE '%-o1-%'
   OR model_name ILIKE '%/o3%'
   OR model_name ILIKE '%-o3-%'
   OR model_name ILIKE '%/o4%'
   OR model_name ILIKE '%-o4-%'
   OR model_name ILIKE '%deepseek-r1%'
   OR model_name ILIKE '%deepseek-reasoner%'
   OR model_name ILIKE '%-thinking%'
   OR model_name ILIKE '%gemini%thinking%';

-- ── is_free (from ANONYMOUS_ALLOWED_MODELS — OpenRouter :free models) ─────────

UPDATE models SET is_free = true
WHERE provider_model_id ILIKE '%:free'
   OR model_name ILIKE '%:free';

-- Also mark well-known free models by name pattern
UPDATE models SET is_free = true
WHERE model_name ILIKE '%gemma-2-9b%'
   OR model_name ILIKE '%llama-3.2-3b%'
   OR model_name ILIKE '%llama-3.1-8b%instruct%:free%'
   OR model_name ILIKE '%mistral-7b%instruct%:free%'
   OR model_name ILIKE '%zephyr-7b%:free%'
   OR model_name ILIKE '%openchat-7b%:free%'
   OR model_name ILIKE '%nous-hermes%:free%'
   OR model_name ILIKE '%trinity-mini%:free%';

-- ── latency_tier (from PROVIDER_LATENCY_TIERS + ULTRA_LOW_LATENCY_MODELS) ─────

-- Default all models to tier 3 (standard) — already the column default
-- Tier 1: ultra-fast providers (Groq, Cerebras)
UPDATE models SET latency_tier = 1
WHERE provider_model_id ILIKE 'groq/%'
   OR provider_model_id ILIKE '%/groq/%'
   OR model_name ILIKE 'groq/%';

UPDATE models SET latency_tier = 1
WHERE provider_model_id ILIKE 'cerebras/%'
   OR model_name ILIKE 'cerebras/%';

-- Specific ultra-low latency models (sub-100ms in production)
UPDATE models SET latency_tier = 1
WHERE provider_model_id = 'groq/moonshotai/kimi-k2-instruct-0905'
   OR provider_model_id = 'groq/openai/gpt-oss-120b';

-- Tier 2: fast providers (Fireworks, Together, OpenRouter fast models)
UPDATE models SET latency_tier = 2
WHERE (
    provider_model_id ILIKE 'fireworks/%'
    OR provider_model_id ILIKE 'accounts/fireworks/%'
    OR model_name ILIKE 'fireworks/%'
) AND latency_tier > 2;

UPDATE models SET latency_tier = 2
WHERE (
    provider_model_id ILIKE 'together/%'
    OR model_name ILIKE 'together/%'
) AND latency_tier > 2;

-- Small tier pool models (cheap-fast, assign tier 2 if not already tier 1)
UPDATE models SET latency_tier = 2
WHERE model_name IN (
    'openai/gpt-4o-mini',
    'anthropic/claude-3-haiku',
    'google/gemini-flash-1.5',
    'deepseek/deepseek-chat',
    'mistral/mistral-small',
    'meta-llama/llama-3.1-8b-instant'
) AND latency_tier > 2;

-- ── max_output_tokens (from MODEL_MAX_TOKENS + google_models_config.py) ────────

-- OpenAI models
UPDATE models SET max_output_tokens = 16384
WHERE model_name ILIKE '%gpt-4o-mini%';

UPDATE models SET max_output_tokens = 4096
WHERE model_name ILIKE '%gpt-4o%'
  AND model_name NOT ILIKE '%gpt-4o-mini%';

UPDATE models SET max_output_tokens = 4096
WHERE model_name ILIKE '%gpt-4-turbo%';

UPDATE models SET max_output_tokens = 4096
WHERE model_name ILIKE '%gpt-3.5-turbo%';

UPDATE models SET max_output_tokens = 8192
WHERE model_name ILIKE '%gpt-4%'
  AND model_name NOT ILIKE '%gpt-4o%'
  AND model_name NOT ILIKE '%gpt-4-turbo%';

-- Claude models
UPDATE models SET max_output_tokens = 4096
WHERE model_name ILIKE '%claude-3-opus%'
   OR model_name ILIKE '%claude-3-sonnet%'
   OR model_name ILIKE '%claude-3-haiku%';

UPDATE models SET max_output_tokens = 8192
WHERE model_name ILIKE '%claude-3-5-sonnet%'
   OR model_name ILIKE '%claude-3.5-sonnet%'
   OR model_name ILIKE '%claude-sonnet-4%';

-- Llama models
UPDATE models SET max_output_tokens = 8192
WHERE model_name ILIKE '%llama-3-%'
  AND model_name NOT ILIKE '%llama-3.1%'
  AND model_name NOT ILIKE '%llama-3.2%';

UPDATE models SET max_output_tokens = 128000
WHERE model_name ILIKE '%llama-3.1%'
   OR model_name ILIKE '%llama-3.2%';

-- Mistral / Mixtral
UPDATE models SET max_output_tokens = 8192
WHERE model_name ILIKE '%mistral%';

UPDATE models SET max_output_tokens = 32768
WHERE model_name ILIKE '%mixtral%';

-- Gemini models (from google_models_config.py)
UPDATE models SET max_output_tokens = 65536
WHERE model_name ILIKE '%gemini%flash%';

UPDATE models SET max_output_tokens = 8192
WHERE model_name ILIKE '%gemini%pro%';

UPDATE models SET max_output_tokens = 65536
WHERE model_name ILIKE '%gemini-3%'
   OR model_name ILIKE '%gemini-2.5%';
