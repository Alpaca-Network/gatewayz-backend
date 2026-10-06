-- API key purpose: validator (no-logging) mode.
--
-- Why this exists
-- ---------------
-- GenLayer validators (Gatewayz x GenLayer PRD, R2) need a key whose traffic
-- is persisted only as far as billing requires: the credit_transactions debit,
-- the usage_records row and the request-cap counter. Everything else -- the
-- per-request chat_completion_requests row that feeds analytics/rankings,
-- activity_log, chat history -- is skipped for such a key, and the key never
-- receives a substitute model. The application reads this column once per key
-- lookup (src/db/users.py) and keys every guard off it
-- (src/services/key_purpose.py).
--
-- Why a column and not a key inside scope_permissions
-- ---------------------------------------------------
-- scope_permissions is a permission map (verify_key_permissions iterates it as
-- {permission: [resources]}) and the update route REPLACES it wholesale. A
-- privacy flag stored there would be silently dropped the next time anyone
-- edited the key's permissions -- the guarantee would fail OPEN, without an
-- error. A dedicated column cannot be clobbered that way, and it can carry a
-- CHECK constraint so a typo is rejected by the database, not stored.
--
-- NULL means "general". Existing rows are untouched and an untagged key's row
-- is identical to what it was before this migration; the application only
-- writes the column when a key is explicitly set to 'validator' (or back).

ALTER TABLE public.api_keys_new
    ADD COLUMN IF NOT EXISTS purpose TEXT;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'api_keys_new_purpose_check'
           AND conrelid = 'public.api_keys_new'::regclass
    ) THEN
        ALTER TABLE public.api_keys_new
            ADD CONSTRAINT api_keys_new_purpose_check
            CHECK (purpose IS NULL OR purpose IN ('general', 'validator'));
    END IF;
END;
$block$;

COMMENT ON COLUMN public.api_keys_new.purpose IS
'Key purpose. NULL/general = normal key. validator = no-logging mode: only billing records (credit_transactions, usage_records, requests_used) are persisted per request; excluded from chat_completion_requests/activity_log/chat history and therefore from every analytics aggregate; never served a substitute model. See src/services/key_purpose.py.';

-- House convention (20260527000000_emergency_rls_lockdown.sql /
-- 20260527000002_final_security_hardening.sql): api_keys_new is reachable by
-- service_role only. Adding a column does not change table grants, but
-- re-assert them so this migration cannot be the one that widens access.
REVOKE ALL ON public.api_keys_new FROM anon, authenticated;
ALTER TABLE public.api_keys_new ENABLE ROW LEVEL SECURITY;
