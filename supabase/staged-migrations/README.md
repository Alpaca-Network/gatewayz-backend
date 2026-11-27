# Staged Migrations (human-gated)

These migrations are intentionally kept out of `supabase/migrations/` so the `supabase-migrations.yml` CI workflow does not auto-push them to production on merge to `main`. Apply each one manually, one at a time, after its corresponding code deploy has soaked in production — the deleted/changed code stops writing to these tables/rows immediately on deploy, so there's no rush and no correctness risk in waiting. The `drop_*` migrations are irreversible (data loss), so hold off until you're confident the feature removal is final; the `deactivate_nonroster_providers` and `seed_tier2_providers` migrations should be applied to staging first and verified before touching production.

## Stage 3: null plaintext API keys (`20261127150100_null_plaintext_api_keys.sql`)

Apply only after the stage-2 hash lookups (`20261127150000_api_key_hash_lookup.sql` + `src/db/api_key_lookup.py`) have shipped, the `api_key_plaintext_fallback` WARNING has been absent from logs for 24-48 h, and `API_KEY_PLAINTEXT_FALLBACK=false` has soaked in every environment. It refuses to run if any `api_keys_new` row lacks `key_hash`/`encrypted_key` or if a legacy `users.api_key` has no `api_keys_new` row. Irreversible.
