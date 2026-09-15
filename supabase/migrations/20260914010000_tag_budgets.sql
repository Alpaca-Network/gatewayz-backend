-- Per-tag spend budgets with a webhook that fires once when the line is crossed.
--
-- The partner attribution plan asks for "per-tag budget thresholds that fire a
-- webhook -- so an initiative can have a compute budget an agent cannot
-- silently blow." Silently is the operative word: an agent with no feedback
-- loop spends until someone reads a bill.
--
-- notified_at is the idempotency latch. Without it a budget that stays over
-- its limit fires a webhook on EVERY subsequent call, which is how an alert
-- becomes noise and then becomes ignored.

CREATE TABLE IF NOT EXISTS public.usage_tag_budgets (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    tag          TEXT   NOT NULL,
    limit_usd    NUMERIC(12, 6) NOT NULL CHECK (limit_usd > 0),
    webhook_url  TEXT,
    notified_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT usage_tag_budgets_unique_per_tag UNIQUE (user_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_usage_tag_budgets_user
    ON public.usage_tag_budgets (user_id);

-- The app reaches Supabase only as service_role. anon/authenticated are revoked
-- explicitly: REVOKE FROM PUBLIC does not close Supabase's default grants, and
-- this table holds customer spend limits and outbound URLs.
ALTER TABLE public.usage_tag_budgets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.usage_tag_budgets FROM PUBLIC;
REVOKE ALL ON public.usage_tag_budgets FROM anon, authenticated;
GRANT ALL ON public.usage_tag_budgets TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.usage_tag_budgets_id_seq TO service_role;
