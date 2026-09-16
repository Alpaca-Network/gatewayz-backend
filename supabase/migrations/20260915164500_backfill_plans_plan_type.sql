-- Backfill plans.plan_type and stop it going NULL again.
--
-- Why: GET /plans (a PUBLIC endpoint, used by the pricing page) serializes rows
-- through PlanResponse, which declares `plan_type: str`. plan_type was added as
-- a bare nullable TEXT column by 20251231000000_add_admin_tier_plan.sql and only
-- ever populated for the 'Admin' row, so 6 of the 7 production rows hold NULL.
-- A pydantic default does not rescue an explicitly-passed None, so every request
-- 500'd. The application now coerces NULL at the boundary; this migration fixes
-- the data so the coercion never has to fire.
--
-- Vocabulary: plan_type values are derived from plans.name, matching the only
-- plan_type value that exists in production today ('admin', consumed by
-- src/db/plans.py::is_admin_tier_user) and the label set documented for the
-- Prometheus plan_type metric (src/services/metrics/prometheus_metrics.py:330 --
-- "free, trial, starter, professional, enterprise"). Note this is NOT the
-- src/schemas/common.py PlanType enum (free/dev/team/customize); that enum is
-- only consumed by SubscriptionPlan, whose endpoint is discontinued (HTTP 410),
-- and by a cosmetic sort in GET /plans that falls back safely for unknown types.
--
-- Idempotent: the backfill only touches rows that are still NULL/blank, so it
-- never overwrites a curated value, and the ALTERs are no-ops when re-applied.

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS plan_type TEXT;

-- Name-derived backfill. Keyed on plans.name (UNIQUE) rather than plans.tier,
-- because tier is deliberately NULL for Free, Free Trial, Enterprise and Admin
-- (see 20260618000003_add_tier_to_plans.sql) and so cannot classify every row.
UPDATE public.plans
SET plan_type = CASE lower(btrim(name))
    WHEN 'free'         THEN 'free'
    WHEN 'free trial'   THEN 'trial'
    WHEN 'starter'      THEN 'starter'
    WHEN 'professional' THEN 'professional'
    WHEN 'business'     THEN 'business'
    WHEN 'enterprise'   THEN 'enterprise'
    WHEN 'admin'        THEN 'admin'
    -- Any future plan: slugify the display name rather than invent a value.
    ELSE regexp_replace(lower(btrim(name)), '[^a-z0-9]+', '_', 'g')
END
WHERE plan_type IS NULL OR btrim(plan_type) = '';

-- Safety net for a name that slugifies to nothing, so SET NOT NULL cannot fail.
UPDATE public.plans
SET plan_type = 'free'
WHERE plan_type IS NULL OR btrim(plan_type) = '';

-- Prevent recurrence. DEFAULT + NOT NULL rather than a CHECK constraint: the
-- plan_type vocabulary is still split across the codebase (PlanType enum vs the
-- name-derived values above), so a CHECK would freeze a contested list and hard
-- -fail legitimate future inserts. The actual production defect is NULL, and
-- DEFAULT + NOT NULL closes exactly that without guessing at the enum.
ALTER TABLE public.plans
    ALTER COLUMN plan_type SET DEFAULT 'free';

ALTER TABLE public.plans
    ALTER COLUMN plan_type SET NOT NULL;

COMMENT ON COLUMN public.plans.plan_type IS
    'Plan classification derived from plans.name (free, trial, starter, professional, business, enterprise, admin). NOT NULL, defaults to ''free''. Consumed by is_admin_tier_user() and by GET /plans ordering.';
