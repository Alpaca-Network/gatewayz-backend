-- Add fetch_module_path and fetch_function_name to providers table
-- Enables dynamic import dispatch for the sync pipeline (Phase 2D)

ALTER TABLE "public"."providers" ADD COLUMN IF NOT EXISTS "fetch_module_path" TEXT;
ALTER TABLE "public"."providers" ADD COLUMN IF NOT EXISTS "fetch_function_name" TEXT;

-- Populate fetch_module_path for all providers with fetch functions.
-- Convention: src.services.{slug_underscored}_client
-- Three exceptions: fal, alibaba, huggingface have irregular module names.

-- Standard naming: slug → src.services.{slug}_client
UPDATE "public"."providers" SET fetch_module_path = 'src.services.openrouter_client'          WHERE slug = 'openrouter';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.deepinfra_client'            WHERE slug = 'deepinfra';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.featherless_client'          WHERE slug = 'featherless';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.chutes_client'               WHERE slug = 'chutes';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.groq_client'                 WHERE slug = 'groq';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.fireworks_client'            WHERE slug = 'fireworks';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.together_client'             WHERE slug = 'together';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.aimo_client'                 WHERE slug = 'aimo';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.near_client'                 WHERE slug = 'near';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.vercel_ai_gateway_client'    WHERE slug = 'vercel-ai-gateway';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.aihubmix_client'             WHERE slug = 'aihubmix';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.helicone_client'             WHERE slug = 'helicone';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.anannas_client'              WHERE slug = 'anannas';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.cerebras_client'             WHERE slug = 'cerebras';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.google_vertex_client'        WHERE slug = 'google-vertex';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.xai_client'                  WHERE slug = 'xai';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.nebius_client'               WHERE slug = 'nebius';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.novita_client'               WHERE slug = 'novita';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.openai_client'               WHERE slug = 'openai';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.anthropic_client'            WHERE slug = 'anthropic';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.clarifai_client'             WHERE slug = 'clarifai';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.simplismart_client'          WHERE slug = 'simplismart';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.onerouter_client'            WHERE slug = 'onerouter';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.cloudflare_workers_ai_client' WHERE slug = 'cloudflare-workers-ai';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.modelz_client'               WHERE slug = 'modelz';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.cohere_client'               WHERE slug = 'cohere';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.zai_client'                  WHERE slug = 'zai';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.morpheus_client'             WHERE slug = 'morpheus';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.sybil_client'                WHERE slug = 'sybil';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.canopywave_client'           WHERE slug = 'canopywave';

-- Irregular module names
UPDATE "public"."providers" SET fetch_module_path = 'src.services.fal_image_client'            WHERE slug = 'fal';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.alibaba_cloud_client'        WHERE slug = 'alibaba';
UPDATE "public"."providers" SET fetch_module_path = 'src.services.huggingface_models'          WHERE slug = 'huggingface';

-- Irregular function name (only huggingface deviates from fetch_models_from_{slug})
UPDATE "public"."providers" SET fetch_function_name = 'fetch_models_from_huggingface_api'      WHERE slug = 'huggingface';
