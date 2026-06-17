-- Performance indexes for hot read paths identified in the scalability audit.
--
-- 1. credit_transactions(user_id, created_at DESC)
--    The daily-usage limiter and per-user transaction history both filter by
--    user_id AND a created_at range (e.g. WHERE user_id = $1 AND created_at >=
--    start_of_day AND amount < 0). Separate single-column indexes on user_id
--    and created_at already exist, but the planner can only use one of them and
--    then range-scans the rest. A composite index covers both predicates.
--
-- 2. user_plans(user_id) WHERE is_active
--    The admin-tier / active-plan check runs on every authenticated request
--    (WHERE user_id = $1 AND is_active = TRUE). A partial index keyed on user_id
--    restricted to active rows keeps this lookup index-only and tiny.
--
-- Both use IF NOT EXISTS so the migration is idempotent. On very large tables,
-- prefer running the equivalent CREATE INDEX CONCURRENTLY out-of-band (it cannot
-- run inside a migration transaction) to avoid a write lock during deploy.

CREATE INDEX IF NOT EXISTS "idx_credit_transactions_user_id_created_at"
    ON "public"."credit_transactions" USING "btree" ("user_id", "created_at" DESC);

CREATE INDEX IF NOT EXISTS "idx_user_plans_user_id_active"
    ON "public"."user_plans" USING "btree" ("user_id")
    WHERE "is_active";
