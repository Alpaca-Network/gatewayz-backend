# Phase 1 & 3 Migration Review + Apply Guide (Gatewayz One)

**Status:** staged on `main`, NOT applied. Review this before running. **You apply** — these touch the production DB; do not let an automated agent run them blind.

**Files**
- `supabase/migrations/20260617000000_gatewayz_one_phase1_registry.sql` (142 lines)
- `supabase/migrations/20260617000001_gatewayz_one_phase3_credit_ledger.sql` (51 lines)

---

## TL;DR

Both migrations are **purely additive and idempotent** — no `DROP`, no column retypes, no data rewrites, every statement guarded by `IF NOT EXISTS` / a constraint-existence check. **Risk: LOW.** No application code reads the new Phase 1 schema yet, and the Phase 3 ledger is only written by the new flag-gated shadow path (off by default). One thing to confirm before running: the `user_id bigint` typing vs `public.users.id` (FKs were deliberately left off to avoid a type-mismatch failure).

---

## What each migration does

### Phase 1 — registry & projection schema (`…000000`)
- `providers`: + `tier` (`'niche'` default, CHECK `core|aggregator|niche`), `region_affinity`, `async_streaming`, `auth_type`, `base_url`. Retires the `ENABLED_PROVIDERS` env var in favour of `is_active` + `tier`.
- `models_catalog`: + `canonical_id`, `capabilities` (jsonb), `modality`, `context_length`, `deprecated_at`; partial index on `canonical_id WHERE deprecated_at IS NULL`.
- **NEW** `model_provider_offers` — the (model × provider) join the smart router scores over (`upstream_cost`, `quality_prior`, `p50_ms`, `p95_ms`, `is_active`; UNIQUE `(canonical_id, provider_slug)`). RLS on, no policy.
- **NEW** `routing_policies` — per-key / system-default policy + weights (CHECK `cost|latency|quality|balanced`). RLS on, no policy.
- **NEW** `user_memory` — portable per-user memory (`kind`, `content`, `salience`). RLS on, no policy.
- `chat_sessions`: + `conversation_id` (uuid), `rolling_summary`; partial index.

### Phase 3 — append-only credit ledger (`…000001`)
- **NEW** `credit_ledger` — one debit-or-credit line per row; `ref` (idempotency = request id), `account` (`user:subscription_allowance|user:purchased_credits|revenue`), `debit`/`credit` `numeric(14,10)` (CHECK ≥ 0, exactly one side non-zero), `state` (`reserved|settled|released`). Indexes on `ref` and `(user_id, created_at)`.
- Immutability: `BEFORE UPDATE OR DELETE` trigger raises — append-only audit trail.
- RLS on, no permissive policy → backend `service_role` only.

## Safety assessment

| Dimension | Finding |
|-----------|---------|
| Destructive ops | None — no DROP/TRUNCATE/retype/NOT NULL-on-existing |
| Idempotency | Full — re-runnable; `IF NOT EXISTS` + constraint guards |
| Locking | `ADD COLUMN` is metadata-only in PG11+ (defaults here are constant/non-volatile → no table rewrite). New columns with `NOT NULL DEFAULT` on `providers`/`models_catalog` are fast. Brief `ACCESS EXCLUSIVE` per ALTER; run off-peak on very large tables. |
| Security | New tables RLS-enabled, no permissive policy → only `service_role` (which bypasses RLS) can access. Matches the 2026-05-27 hardening posture. |
| App impact at apply time | Zero — no code reads Phase 1 columns; `credit_ledger` only written when `CREDIT_LEDGER_SHADOW_ENABLED=true` (default false). |

## ⚠️ Confirm before applying
1. **`user_id` type / FK.** Both migrations type `user_id` as `bigint` to match the integer user ids in the codebase, and **omit the FK** to `public.users(id)`. Confirm `public.users.id` is an integer/bigint; if so, add `REFERENCES public.users(id)` (and to `credit_ledger.user_id`). If `users.id` is uuid, change these columns to uuid first.
2. **`numeric(14,10)`** gives 4 integer digits — max ~9999.9999999999 per line. Confirm no single charge/balance line exceeds that; widen precision if needed.

## Apply steps (you run)
```bash
# from repo root, with the Supabase project linked (supabase link --project-ref <ref>)
supabase migration list            # confirm both are pending
supabase db push                   # applies all pending migrations
# or apply one explicitly via the SQL editor / psql against the prod connection string
```
Recommended: apply to **staging first**, run the verification queries, then prod.

## Verification (post-apply)
```sql
-- Phase 1
SELECT column_name FROM information_schema.columns
 WHERE table_name='providers' AND column_name IN ('tier','region_affinity','async_streaming','auth_type','base_url');
SELECT to_regclass('public.model_provider_offers'), to_regclass('public.routing_policies'), to_regclass('public.user_memory');
-- Phase 3
SELECT to_regclass('public.credit_ledger');
-- immutability trigger fires:
-- UPDATE public.credit_ledger SET debit=0 WHERE id=<any>;  -- expect: ERROR append-only
SELECT relrowsecurity FROM pg_class WHERE relname IN ('credit_ledger','model_provider_offers','routing_policies','user_memory');
```

## Rollback
Additive, so rollback is dropping the new objects (only if nothing has written to them):
```sql
DROP TABLE IF EXISTS public.credit_ledger;            -- + function credit_ledger_block_mutations
DROP TABLE IF EXISTS public.model_provider_offers, public.routing_policies, public.user_memory;
-- columns: ALTER TABLE public.providers DROP COLUMN IF EXISTS tier, ... (etc.)
```
Once the shadow path has written ledger rows, prefer leaving the table (it's an audit trail) and just disabling `CREDIT_LEDGER_SHADOW_ENABLED`.
