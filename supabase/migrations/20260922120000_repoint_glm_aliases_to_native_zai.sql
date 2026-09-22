-- GLM aliases were seeded (20260401000004) to rewrite every GLM id to
-- 'z-ai/glm-4-flash', an OpenRouter id that no longer exists. With the native
-- Z.AI adapter live, that rewrite turned valid requests (e.g. zai/glm-4.7) into
-- a dead model. Drop the stale rewrites and point the common spellings at the
-- real Z.AI catalog ids instead.

DELETE FROM model_aliases WHERE canonical_id = 'z-ai/glm-4-flash';

INSERT INTO model_aliases (alias, canonical_id) VALUES
  ('glm-4.5', 'zai/glm-4.5'),
  ('glm-4.5-air', 'zai/glm-4.5-air'),
  ('glm-4.6', 'zai/glm-4.6'),
  ('glm-4.7', 'zai/glm-4.7'),
  ('glm-5', 'zai/glm-5'),
  ('glm-5.1', 'zai/glm-5.1'),
  ('glm-5.3', 'zai/glm-5.3'),
  ('z-ai/glm-4.5', 'zai/glm-4.5'),
  ('z-ai/glm-4.6', 'zai/glm-4.6'),
  ('z-ai/glm-4.7', 'zai/glm-4.7'),
  ('z-ai/glm-4-7', 'zai/glm-4.7'),
  ('z-ai/glm4.7', 'zai/glm-4.7')
ON CONFLICT (alias) DO UPDATE SET canonical_id = EXCLUDED.canonical_id;
