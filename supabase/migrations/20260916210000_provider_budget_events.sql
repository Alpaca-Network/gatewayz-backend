-- Operator-visible record of provider credit/budget exhaustion.
--
-- Why this table exists: is_provider_budget_error() in src/utils/errors.py detects an
-- unfunded provider account precisely, and then deliberately replaces it with
-- PROVIDER_CAPACITY_MESSAGE ("temporarily unavailable ... try again shortly"). Masking
-- our own billing state from customers is correct and stays. The gap it left is that no
-- operator-facing signal existed either, so on 2026-09-16 all 11 Anthropic models were
-- down on an unfunded key -- 57 of ~65 catalog models unavailable -- and a terminal
-- condition needing a human to buy credits presented to everyone as transient.
--
-- Why Postgres and not memory or Redis: the condition being recorded (an unfunded
-- account) survives a process restart, so the evidence must too. A deploy is a restart,
-- and an alert a deploy silently clears is worse than no alert -- it reads as "resolved".
-- The backend also runs more than one Railway instance, and in-memory state would make
-- /admin/status report whichever instance happened to answer. Redis in this repo is a
-- cache with fail-open semantics (src/config/redis_config.py falls back to memory and
-- the Upstash database was deleted out from under the org once already); nothing here
-- treats it as a system of record.
--
-- One row per (provider, reason), not one per failed request: writes happen on an
-- inference failure path, so the app coalesces them
-- (src/services/provider_budget_alerts.py) and this table counts occurrences instead of
-- storing them.

CREATE TABLE IF NOT EXISTS public.provider_budget_events (
    id             BIGSERIAL PRIMARY KEY,
    provider       TEXT   NOT NULL,
    reason         TEXT   NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurrences    BIGINT NOT NULL DEFAULT 1 CHECK (occurrences > 0),
    sample_model   TEXT,
    CONSTRAINT provider_budget_events_unique_per_reason UNIQUE (provider, reason)
);

-- /admin/status asks "anything seen in the last N hours", newest first.
CREATE INDEX IF NOT EXISTS idx_provider_budget_events_last_seen
    ON public.provider_budget_events (last_seen_at DESC);

COMMENT ON TABLE public.provider_budget_events IS
    'Provider credit/budget exhaustion, aggregated per (provider, reason). Surfaced at GET /admin/status.';
COMMENT ON COLUMN public.provider_budget_events.reason IS
    'Closed vocabulary, written only by the app: credit_balance_low | quota_exhausted | '
    'spend_limit_reached | payment_required | unknown. Deliberately NOT a CHECK '
    'constraint: the vocabulary belongs to PROVIDER_BUDGET_REASONS in src/utils/errors.py '
    'and is enforced there (the recorder coerces anything unrecognized to "unknown"). A '
    'CHECK would both couple every new reason to a migration and turn a classifier bug '
    'into a swallowed constraint violation in a fire-and-forget writer -- i.e. silently '
    'lose the very alert this table exists to raise.';
COMMENT ON COLUMN public.provider_budget_events.sample_model IS
    'One model id from our own catalog that hit this condition, to give the operator '
    'something concrete to retry. Never upstream error text.';

-- Atomic upsert-and-increment. PostgREST cannot express "occurrences = occurrences + n",
-- and a read-modify-write from the app would lose counts across concurrent instances.
-- p_increment carries the number of occurrences the caller coalesced in-process since
-- its last flush, so throttling the write does not undercount.
CREATE OR REPLACE FUNCTION public.record_provider_budget_event(
    p_provider  TEXT,
    p_reason    TEXT,
    p_model     TEXT DEFAULT NULL,
    p_increment BIGINT DEFAULT 1
) RETURNS void
LANGUAGE sql
SET search_path = public, pg_temp
AS $$
    INSERT INTO public.provider_budget_events (provider, reason, sample_model, occurrences)
    VALUES (p_provider, p_reason, p_model, GREATEST(p_increment, 1))
    ON CONFLICT ON CONSTRAINT provider_budget_events_unique_per_reason DO UPDATE
        SET last_seen_at = NOW(),
            occurrences  = public.provider_budget_events.occurrences + GREATEST(p_increment, 1),
            sample_model = COALESCE(EXCLUDED.sample_model, public.provider_budget_events.sample_model);
$$;

-- The app reaches Supabase only as service_role. anon/authenticated are revoked
-- explicitly: REVOKE FROM PUBLIC does not close Supabase's default grants, and this
-- table describes which of our provider accounts are out of money.
ALTER TABLE public.provider_budget_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.provider_budget_events FROM PUBLIC;
REVOKE ALL ON public.provider_budget_events FROM anon, authenticated;
GRANT ALL ON public.provider_budget_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.provider_budget_events_id_seq TO service_role;

REVOKE ALL ON FUNCTION public.record_provider_budget_event(TEXT, TEXT, TEXT, BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_provider_budget_event(TEXT, TEXT, TEXT, BIGINT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_provider_budget_event(TEXT, TEXT, TEXT, BIGINT) TO service_role;

-- PostgREST caches the schema; without this a fresh table/function 404s until the next
-- cache refresh (PGRST202/PGRST205).
NOTIFY pgrst, 'reload schema';
