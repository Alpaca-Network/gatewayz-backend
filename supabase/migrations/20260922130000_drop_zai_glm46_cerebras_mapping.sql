-- 'zai/glm-4.6' was seeded (20260401000004) as a Cerebras mapping. The zai/
-- namespace now belongs to the native Z.AI adapter, and the mapping-table scan
-- runs before org-prefix rules, so this row sent zai/glm-4.6 (and the glm-4.6
-- alias) to Cerebras, then on failover to OpenRouter. Cerebras-specific ids
-- (cerebras/zai-glm-4.6, zai-glm-4.6) are left alone.
DELETE FROM model_provider_mappings
WHERE model_id = 'zai/glm-4.6' AND provider = 'cerebras';
