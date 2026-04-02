-- Phase 3: Seed infrastructure metadata into providers.metadata JSONB
-- Adds: base_url (pool), models_endpoint, chat_completions_endpoint,
--        min_expected_models, header_type, default_headers, custom_timeout_ms,
--        hostnames, monitor_402_frequency, async_streaming
--
-- These fields allow gateway_registry.py to serve infrastructure config
-- that was previously hardcoded in connection_pool.py, gateway_health_service.py,
-- intelligent_health_monitor.py, provider_span_enricher.py, and
-- provider_credit_monitor.py.

-- Helper: merge keys into existing metadata JSONB without overwriting other fields.
-- Uses || (JSONB concat) which merges top-level keys.

-- openrouter
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://openrouter.ai/api/v1',
    'models_endpoint', 'https://openrouter.ai/api/v1/models',
    'chat_completions_endpoint', 'https://openrouter.ai/api/v1/chat/completions',
    'min_expected_models', 100,
    'header_type', 'bearer',
    'default_headers', '{"HTTP-Referer": "__OPENROUTER_SITE_URL__", "X-Title": "__OPENROUTER_SITE_NAME__"}'::jsonb,
    'hostnames', '["openrouter.ai", "api.openrouter.ai"]'::jsonb,
    'monitor_402_frequency', false,
    'async_streaming', true
)
WHERE slug = 'openrouter';

-- featherless
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.featherless.ai/v1',
    'models_endpoint', 'https://api.featherless.ai/v1/models',
    'chat_completions_endpoint', 'https://api.featherless.ai/v1/chat/completions',
    'min_expected_models', 10,
    'header_type', 'bearer',
    'hostnames', '["inference.featherless.ai"]'::jsonb
)
WHERE slug = 'featherless';

-- fireworks
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.fireworks.ai/inference/v1',
    'models_endpoint', 'https://api.fireworks.ai/inference/v1/models',
    'chat_completions_endpoint', 'https://api.fireworks.ai/inference/v1/chat/completions',
    'min_expected_models', 10,
    'header_type', 'bearer',
    'hostnames', '["api.fireworks.ai"]'::jsonb,
    'monitor_402_frequency', true
)
WHERE slug = 'fireworks';

-- together
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.together.xyz/v1',
    'models_endpoint', 'https://api.together.xyz/v1/models',
    'chat_completions_endpoint', 'https://api.together.xyz/v1/chat/completions',
    'min_expected_models', 20,
    'header_type', 'bearer',
    'hostnames', '["api.together.ai", "api.together.xyz"]'::jsonb,
    'monitor_402_frequency', true
)
WHERE slug = 'together';

-- huggingface
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://router.huggingface.co/v1',
    'models_endpoint', 'https://huggingface.co/api/models',
    'min_expected_models', 100,
    'header_type', 'bearer',
    'custom_timeout_ms', 120000,
    'hostnames', '["api.huggingface.co", "huggingface.co"]'::jsonb
)
WHERE slug = 'huggingface';

-- xai
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.x.ai/v1',
    'models_endpoint', 'https://api.x.ai/v1/models',
    'chat_completions_endpoint', 'https://api.x.ai/v1/chat/completions',
    'min_expected_models', 2,
    'header_type', 'bearer',
    'custom_timeout_ms', 600000,
    'hostnames', '["api.x.ai"]'::jsonb
)
WHERE slug = 'xai';

-- deepinfra
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.deepinfra.com/v1/openai',
    'models_endpoint', 'https://api.deepinfra.com/models/list',
    'chat_completions_endpoint', 'https://api.deepinfra.com/v1/openai/chat/completions',
    'min_expected_models', 50,
    'header_type', 'bearer',
    'monitor_402_frequency', true
)
WHERE slug = 'deepinfra';

-- chutes
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://llm.chutes.ai/v1',
    'models_endpoint', 'https://llm.chutes.ai/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'chutes';

-- groq
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.groq.com/openai/v1',
    'models_endpoint', 'https://api.groq.com/openai/v1/models',
    'chat_completions_endpoint', 'https://api.groq.com/openai/v1/chat/completions',
    'min_expected_models', 5,
    'header_type', 'bearer',
    'hostnames', '["api.groq.com"]'::jsonb,
    'monitor_402_frequency', true
)
WHERE slug = 'groq';

-- cerebras
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://api.cerebras.ai/v1/models',
    'chat_completions_endpoint', 'https://api.cerebras.ai/v1/chat/completions',
    'min_expected_models', 2,
    'header_type', 'bearer',
    'hostnames', '["api.cerebras.ai"]'::jsonb
)
WHERE slug = 'cerebras';

-- novita
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://api.novita.ai/v3/openai/models',
    'chat_completions_endpoint', 'https://api.novita.ai/v3/openai/chat/completions',
    'min_expected_models', 5,
    'header_type', 'bearer',
    'hostnames', '["api.novita.ai"]'::jsonb
)
WHERE slug = 'novita';

-- nebius
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://api.studio.nebius.ai/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'nebius';

-- openai
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.openai.com/v1',
    'models_endpoint', 'https://api.openai.com/v1/models',
    'min_expected_models', 10,
    'header_type', 'bearer',
    'hostnames', '["api.openai.com"]'::jsonb
)
WHERE slug = 'openai';

-- anthropic
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.anthropic.com/v1',
    'min_expected_models', 3,
    'header_type', 'bearer',
    'hostnames', '["api.anthropic.com"]'::jsonb
)
WHERE slug = 'anthropic';

-- google-vertex
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'min_expected_models', 10,
    'header_type', 'google',
    'hostnames', '["us-central1-aiplatform.googleapis.com", "generativelanguage.googleapis.com"]'::jsonb
)
WHERE slug = 'google-vertex';

-- clarifai
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.clarifai.com/v2/ext/openai/v1',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'clarifai';

-- onerouter
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://llm.infron.ai/v1',
    'models_endpoint', 'https://api.infron.ai/v1/models',
    'min_expected_models', 100,
    'header_type', 'bearer'
)
WHERE slug = 'onerouter';

-- zai
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.z.ai/api/paas/v4/'
)
WHERE slug = 'zai';

-- cloudflare-workers-ai
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1',
    'min_expected_models', 10,
    'header_type', 'bearer'
)
WHERE slug = 'cloudflare-workers-ai';

-- simplismart
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.simplismart.live',
    'models_endpoint', 'https://api.simplismart.ai/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'simplismart';

-- sybil
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.sybil.com/v1',
    'min_expected_models', 3,
    'header_type', 'bearer'
)
WHERE slug = 'sybil';

-- canopywave
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://inference.canopywave.io/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'canopywave';

-- morpheus
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.mor.org/api/v1',
    'min_expected_models', 3,
    'header_type', 'bearer'
)
WHERE slug = 'morpheus';

-- akash
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://api.akashml.com/v1'
)
WHERE slug = 'akash';

-- nosana
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'pool_base_url', 'https://dashboard.k8s.prd.nos.ci/api/v1'
)
WHERE slug = 'nosana';

-- aihubmix
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://aihubmix.com/v1/models',
    'min_expected_models', 5,
    'header_type', 'aihubmix'
)
WHERE slug = 'aihubmix';

-- anannas
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://api.anannas.ai/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'anannas';

-- near
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://cloud-api.near.ai/v1/models',
    'min_expected_models', 4,
    'header_type', 'bearer'
)
WHERE slug = 'near';

-- aimo
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'models_endpoint', 'https://beta.aimo.network/api/v1/models',
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'aimo';

-- fal
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'min_expected_models', 50,
    'header_type', 'bearer'
)
WHERE slug = 'fal';

-- vercel-ai-gateway
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'vercel-ai-gateway';

-- helicone
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'helicone';

-- alibaba
UPDATE "public"."providers"
SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
    'min_expected_models', 5,
    'header_type', 'bearer'
)
WHERE slug = 'alibaba';
