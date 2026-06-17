-- Migration: Gatewayz One Phase 3 — append-only credit ledger
-- Created: 2026-06-17
-- Status:  STAGED / NOT YET APPLIED — review before running. The ledger code
--          (src/services/credit_ledger.py) is pure and NOT wired to live billing;
--          this table is the eventual durable store. Do NOT cut billing over to
--          it without explicit sign-off.
-- Description:
--   Append-only double-entry ledger (spec §6.D) over subscription_allowance +
--   purchased_credits. Each row is one debit-or-credit line; transactions are
--   balanced in application code (Σdebit == Σcredit per ref). Rows are immutable
--   (UPDATE/DELETE blocked by trigger). RLS enabled, no permissive policy →
--   backend service_role only.
-- REVIEW NOTES:
--   * user_id typed bigint to match the codebase; CONFIRM vs public.users.id
--     and add the FK before applying.

CREATE TABLE IF NOT EXISTS public.credit_ledger (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ref         text NOT NULL,                 -- idempotency key (request id)
    user_id     bigint,                        -- CONFIRM vs users.id, then add FK
    account     text NOT NULL,                 -- 'user:subscription_allowance' | 'user:purchased_credits' | 'revenue'
    debit       numeric(14, 10) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit      numeric(14, 10) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    state       text NOT NULL CHECK (state IN ('reserved', 'settled', 'released')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- exactly one side of each line is non-zero
    CONSTRAINT credit_ledger_one_sided CHECK ((debit = 0) OR (credit = 0))
);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_ref
    ON public.credit_ledger (ref);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user_time
    ON public.credit_ledger (user_id, created_at);

-- Append-only enforcement: block UPDATE/DELETE so the audit trail is immutable.
CREATE OR REPLACE FUNCTION public.credit_ledger_block_mutations()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'credit_ledger is append-only: % is not permitted', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS trg_credit_ledger_immutable ON public.credit_ledger;
CREATE TRIGGER trg_credit_ledger_immutable
    BEFORE UPDATE OR DELETE ON public.credit_ledger
    FOR EACH ROW EXECUTE FUNCTION public.credit_ledger_block_mutations();

-- Sensitive financial data: RLS on, no permissive policy → service_role only.
ALTER TABLE public.credit_ledger ENABLE ROW LEVEL SECURITY;
